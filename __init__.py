"""Andreed: bounded Android device-care tools for Hermes Agent.

The plugin exposes deterministic ADB operations instead of a raw shell or
model-written code surface. Read-only inspection runs directly. Every mutation
is routed through Hermes's approval system by the ``pre_tool_call`` hook.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PLUGIN_NAME = "andreed"
DEFAULT_TIMEOUT_S = 20
OUTPUT_LIMIT = 200_000
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_APPROVAL_NONCES = itertools.count()


class AndreedError(RuntimeError):
    """A safe, user-facing Andreed failure."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: dict[str, str]


def _result(success: bool, **fields: Any) -> str:
    payload = {"success": success}
    payload.update({key: value for key, value in fields.items() if value is not None})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _run(argv: list[str], timeout: int = DEFAULT_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    """Run an argv-only child process with bounded output and no shell."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )
    if len(completed.stdout or "") > OUTPUT_LIMIT or len(completed.stderr or "") > OUTPUT_LIMIT:
        raise AndreedError("ADB returned too much output; refusing to process it.")
    return completed


def _adb() -> str:
    path = shutil.which("adb")
    if not path:
        raise AndreedError(
            "adb was not found on PATH. Install Android platform-tools, then run andreed_doctor."
        )
    return path


def _parse_devices(output: str) -> list[Device]:
    devices: list[Device] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        details: dict[str, str] = {}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                details[key] = value
        devices.append(Device(serial=serial, state=state, details=details))
    return devices


def _devices() -> list[Device]:
    completed = _run([_adb(), "devices", "-l"])
    if completed.returncode != 0:
        raise AndreedError((completed.stderr or completed.stdout or "adb devices failed").strip())
    return _parse_devices(completed.stdout)


def _select_device(serial: str | None = None) -> Device:
    if serial:
        serial = str(serial).strip()
        if not SERIAL_RE.fullmatch(serial):
            raise AndreedError("Invalid Android device serial.")
    devices = _devices()
    if serial:
        matches = [device for device in devices if device.serial == serial]
        if not matches:
            raise AndreedError(f"Android device {serial!r} was not returned by adb.")
        device = matches[0]
        if device.state != "device":
            raise AndreedError(f"Android device {serial!r} is {device.state!r}, not authorized and ready.")
        return device

    ready = [device for device in devices if device.state == "device"]
    if not ready:
        states = {device.serial: device.state for device in devices}
        raise AndreedError(f"No authorized Android device is ready. adb states: {states or 'none'}")
    if len(ready) > 1:
        raise AndreedError(
            "More than one Android device is ready. Pass serial explicitly: "
            + ", ".join(device.serial for device in ready)
        )
    return ready[0]


def _shell(device: Device, *args: str, timeout: int = DEFAULT_TIMEOUT_S) -> str:
    completed = _run([_adb(), "-s", device.serial, "shell", *args], timeout=timeout)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "ADB shell command failed").strip()
        raise AndreedError(message)
    return completed.stdout or ""


def _current_user(device: Device) -> int:
    output = _shell(device, "am", "get-current-user").strip()
    match = re.search(r"\d+", output)
    if not match:
        raise AndreedError(f"Could not determine the active Android user from: {output!r}")
    return int(match.group(0))


def _parse_packages(output: str) -> dict[str, int]:
    packages: dict[str, int] = {}
    for line in output.splitlines():
        package_match = re.search(r"package:([^\s]+)", line)
        uid_match = re.search(r"(?:uid:|uid=)(\d+)", line)
        if not package_match or not uid_match:
            continue
        package = package_match.group(1).strip()
        if PACKAGE_RE.fullmatch(package):
            packages[package] = int(uid_match.group(1))
    return packages


def _third_party_packages(device: Device, user_id: int) -> dict[str, int]:
    output = _shell(
        device,
        "cmd",
        "package",
        "list",
        "packages",
        "-3",
        "-U",
        "--user",
        str(user_id),
    )
    return _parse_packages(output)


def _validated_package(value: Any) -> str:
    package = str(value or "").strip()
    if not PACKAGE_RE.fullmatch(package):
        raise AndreedError("package must be a valid Android package name such as com.example.app")
    return package


def _package_uid(device: Device, user_id: int, package: str) -> tuple[int, dict[str, int]]:
    packages = _third_party_packages(device, user_id)
    if package not in packages:
        raise AndreedError(
            f"{package!r} is not an installed third-party app for Android user {user_id}. "
            "Andreed refuses to mutate system packages."
        )
    return packages[package], packages


def _parse_df(output: str) -> dict[str, int | str]:
    rows = [line.split() for line in output.splitlines() if line.strip()]
    for columns in reversed(rows):
        if len(columns) < 6 or not columns[1].isdigit() or not columns[3].isdigit():
            continue
        return {
            "filesystem": columns[0],
            "total_bytes": int(columns[1]) * 1024,
            "used_bytes": int(columns[2]) * 1024,
            "free_bytes": int(columns[3]) * 1024,
            "use_percent": columns[4],
            "mount": columns[5],
        }
    raise AndreedError("Could not parse Android storage information from df output.")


def _storage(device: Device) -> dict[str, int | str]:
    return _parse_df(_shell(device, "df", "-k", "/data"))


def _parse_restricted_uids(output: str) -> set[int]:
    if ":" in output:
        output = output.split(":", 1)[1]
    return {int(value) for value in re.findall(r"\b\d{4,}\b", output)}


def _restricted_uids(device: Device) -> set[int]:
    return _parse_restricted_uids(
        _shell(device, "cmd", "netpolicy", "list", "restrict-background-blacklist")
    )


def _parse_netstats(output: str) -> dict[int, dict[str, int | None]]:
    """Parse common Android netstats detail formats.

    AOSP commonly prints a UID/identity header followed by history buckets using
    ``rb``/``tb`` abbreviations. Some builds print ``rxBytes``/``txBytes`` on
    the UID line instead. Non-zero tagged traffic is skipped to avoid counting
    the same app traffic again under per-socket tags.
    """
    totals: dict[int, dict[str, int]] = defaultdict(
        lambda: {
            "rx_bytes": 0,
            "tx_bytes": 0,
            "metered_rx_bytes": 0,
            "metered_tx_bytes": 0,
        }
    )
    current_uid: int | None = None
    identity_metered = False
    current_metered = False
    current_allowed = False
    for line in output.splitlines():
        upper = line.upper()
        if "IDENT=" in upper or "NETWORKIDENTITYSET" in upper:
            identity_metered = "METERED=YES" in upper or "TYPE=MOBILE" in upper
        uid_match = re.search(r"\buid=(\d+)\b", line)
        if uid_match:
            current_uid = int(uid_match.group(1))
            tag_match = re.search(r"\btag=([^\s,}\]]+)", line)
            current_allowed = not tag_match or tag_match.group(1).lower() in {"0", "0x0"}
            current_metered = identity_metered or "METERED=YES" in upper or "TYPE=MOBILE" in upper
        if current_uid is None or not current_allowed:
            continue
        rx_match = re.search(r"\b(?:rxBytes|rb)=(\d+)\b", line)
        tx_match = re.search(r"\b(?:txBytes|tb)=(\d+)\b", line)
        if not rx_match or not tx_match:
            continue
        rx_bytes = int(rx_match.group(1))
        tx_bytes = int(tx_match.group(1))
        totals[current_uid]["rx_bytes"] += rx_bytes
        totals[current_uid]["tx_bytes"] += tx_bytes
        if current_metered:
            totals[current_uid]["metered_rx_bytes"] += rx_bytes
            totals[current_uid]["metered_tx_bytes"] += tx_bytes
    return dict(totals)


def _parse_netstats_summary(output: str) -> dict[int, dict[str, int | None]]:
    """Parse Android's compact since-boot BPF UID totals.

    Recent Android builds can emit megabytes of historical buckets for
    ``dumpsys netstats detail``. The compact map stays within Andreed's parsing
    cap on supported builds, but it cannot separate metered traffic, so those
    fields remain explicitly unknown.
    """
    totals: dict[int, dict[str, int | None]] = {}
    in_app_uid_stats = False
    for raw in output.splitlines():
        line = raw.strip()
        if line == "mAppUidStatsMap:":
            in_app_uid_stats = True
            continue
        if not in_app_uid_stats:
            continue
        if line.endswith(":") and line != "mAppUidStatsMap:":
            break
        columns = line.split()
        if len(columns) != 5 or not all(value.isdigit() for value in columns):
            continue
        uid, rx_bytes, _, tx_bytes, _ = map(int, columns)
        totals[uid] = {
            "rx_bytes": rx_bytes,
            "tx_bytes": tx_bytes,
            "metered_rx_bytes": None,
            "metered_tx_bytes": None,
        }
    return totals


def _network_usage(device: Device) -> tuple[dict[int, dict[str, int | None]], str]:
    try:
        output = _shell(device, "dumpsys", "netstats", "detail", timeout=35)
        return _parse_netstats(output), "historical_detail"
    except AndreedError as exc:
        if "too much output" not in str(exc):
            raise
    output = _shell(device, "dumpsys", "netstats", timeout=35)
    return _parse_netstats_summary(output), "since_boot_summary"


def _is_running(device: Device, package: str) -> bool:
    completed = _run([_adb(), "-s", device.serial, "shell", "pidof", package])
    return completed.returncode == 0 and bool((completed.stdout or "").strip())


def andreed_doctor(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        adb_path = _adb()
        version = _run([adb_path, "version"])
        devices = _devices()
        payload: dict[str, Any] = {
            "adb_path": adb_path,
            "adb_version": (version.stdout or version.stderr).strip().splitlines()[:2],
            "devices": [asdict(device) for device in devices],
        }
        serial = str((args or {}).get("serial") or "").strip() or None
        if serial or len([item for item in devices if item.state == "device"]) == 1:
            device = _select_device(serial)
            payload["selected_device"] = asdict(device)
            payload["android_sdk"] = _shell(device, "getprop", "ro.build.version.sdk").strip()
            payload["android_release"] = _shell(device, "getprop", "ro.build.version.release").strip()
            help_text = _shell(device, "pm", "help")
            payload["supports_trim_caches"] = "trim-caches" in help_text
        return _result(True, **payload)
    except (AndreedError, subprocess.TimeoutExpired, OSError) as exc:
        return _result(False, error=str(exc))


def andreed_scan(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        device = _select_device((args or {}).get("serial"))
        user_id = _current_user(device)
        packages = _third_party_packages(device, user_id)
        by_uid: dict[int, list[str]] = defaultdict(list)
        for package, uid in packages.items():
            by_uid[uid].append(package)
        restricted = _restricted_uids(device)
        usage, usage_scope = _network_usage(device)
        limit = max(1, min(int((args or {}).get("limit") or 15), 50))
        relevant_usage = {uid: traffic for uid, traffic in usage.items() if uid in by_uid}
        usage_available = bool(relevant_usage)
        rows = []
        for uid, package_names in by_uid.items():
            traffic = relevant_usage.get(uid)
            rx_bytes = None if traffic is None else traffic["rx_bytes"]
            tx_bytes = None if traffic is None else traffic["tx_bytes"]
            total = None if rx_bytes is None or tx_bytes is None else rx_bytes + tx_bytes
            metered_rx = None if traffic is None else traffic["metered_rx_bytes"]
            metered_tx = None if traffic is None else traffic["metered_tx_bytes"]
            metered_total = (
                None if metered_rx is None or metered_tx is None else metered_rx + metered_tx
            )
            rows.append(
                {
                    "uid": uid,
                    "packages": sorted(package_names),
                    "rx_bytes": rx_bytes,
                    "tx_bytes": tx_bytes,
                    "total_bytes": total,
                    "metered_rx_bytes": metered_rx,
                    "metered_tx_bytes": metered_tx,
                    "metered_total_bytes": metered_total,
                    "metered_background_data_restricted": uid in restricted,
                }
            )
        if usage_available:
            rows.sort(key=lambda row: int(row["total_bytes"] or 0), reverse=True)
        else:
            rows.sort(key=lambda row: row["packages"])
        selected = rows[:limit]
        for row in selected:
            row["main_process_running"] = any(
                _is_running(device, package) for package in row["packages"]
            )
        return _result(
            True,
            device=asdict(device),
            android_user=user_id,
            storage=_storage(device),
            third_party_app_count=len(packages),
            data_usage_available=usage_available,
            data_usage_scope=usage_scope,
            metered_data_usage_available=any(
                traffic["metered_rx_bytes"] is not None for traffic in relevant_usage.values()
            ),
            top_data_apps=selected,
            data_usage_note=(
                "Approximate historical counters reported by Android dumpsys netstats, split into "
                "total and metered traffic. They are not live bandwidth measurements; availability "
                "varies by Android build. Background policy applies to metered networks."
                if usage_scope == "historical_detail"
                else "Bounded since-boot UID totals reported by Android dumpsys netstats. This build "
                "returned too much historical detail, so Andreed did not process it and cannot report "
                "a metered split. These are not live bandwidth measurements."
            ),
        )
    except (AndreedError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return _result(False, error=str(exc))


def andreed_force_stop(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        package = _validated_package((args or {}).get("package"))
        device = _select_device((args or {}).get("serial"))
        user_id = _current_user(device)
        uid, _ = _package_uid(device, user_id, package)
        _shell(device, "am", "force-stop", "--user", str(user_id), package)
        return _result(
            True,
            device=device.serial,
            package=package,
            uid=uid,
            note="Force-stop is temporary; Android or the user may start the app again later.",
        )
    except (AndreedError, subprocess.TimeoutExpired, OSError) as exc:
        return _result(False, error=str(exc))


def andreed_set_background_data(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        package = _validated_package((args or {}).get("package"))
        restricted = (args or {}).get("restricted")
        if not isinstance(restricted, bool):
            raise AndreedError("restricted must be true or false")
        device = _select_device((args or {}).get("serial"))
        user_id = _current_user(device)
        uid, packages = _package_uid(device, user_id, package)
        shared = sorted(name for name, package_uid in packages.items() if package_uid == uid)
        if len(shared) != 1:
            raise AndreedError(
                f"UID {uid} is shared by {shared}; Andreed refuses because the policy would affect every app."
            )
        operation = "add" if restricted else "remove"
        _shell(device, "cmd", "netpolicy", operation, "restrict-background-blacklist", str(uid))
        verified = uid in _restricted_uids(device)
        if verified != restricted:
            raise AndreedError("Android did not report the requested background-data policy after the change.")
        return _result(
            True,
            device=device.serial,
            package=package,
            uid=uid,
            metered_background_data_restricted=verified,
        )
    except (AndreedError, subprocess.TimeoutExpired, OSError) as exc:
        return _result(False, error=str(exc))


def andreed_trim_caches(args: dict, **kwargs: Any) -> str:
    del kwargs
    try:
        requested_mb = int((args or {}).get("requested_mb") or 256)
        if requested_mb < 16 or requested_mb > 4096:
            raise AndreedError("requested_mb must be between 16 and 4096")
        device = _select_device((args or {}).get("serial"))
        if "trim-caches" not in _shell(device, "pm", "help"):
            raise AndreedError("This Android build does not advertise pm trim-caches support.")
        before = _storage(device)
        requested_bytes = requested_mb * 1024 * 1024
        target_free = int(before["free_bytes"]) + requested_bytes
        if target_free > int(before["total_bytes"]):
            max_possible = int(before["total_bytes"]) - int(before["free_bytes"])
            raise AndreedError(
                f"The requested target exceeds total storage. At most {max_possible} additional bytes "
                "could theoretically be freed."
            )
        _shell(device, "pm", "trim-caches", str(target_free), timeout=60)
        after = _storage(device)
        recovered = max(0, int(after["free_bytes"]) - int(before["free_bytes"]))
        return _result(
            True,
            device=device.serial,
            requested_bytes=requested_bytes,
            target_free_bytes=target_free,
            recovered_bytes=recovered,
            storage_before=before,
            storage_after=after,
            note=(
                "Android decides which disposable caches, if any, can be trimmed. This is a "
                "device-wide request and may trim caches belonging to other apps, users or system packages."
            ),
        )
    except (AndreedError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        return _result(False, error=str(exc))


DOCTOR_SCHEMA = {
    "name": "andreed_doctor",
    "description": "Check whether adb and an authorized Android device are ready for Andreed.",
    "parameters": {
        "type": "object",
        "properties": {"serial": {"type": "string", "description": "Optional adb device serial."}},
    },
}

SCAN_SCHEMA = {
    "name": "andreed_scan",
    "description": (
        "Read Android storage, third-party packages, approximate per-UID network usage and "
        "metered background-data restrictions and whether selected apps are running. This is read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "serial": {"type": "string", "description": "Optional adb device serial."},
            "limit": {"type": "integer", "default": 15, "minimum": 1, "maximum": 50},
        },
    },
}

FORCE_STOP_SCHEMA = {
    "name": "andreed_force_stop",
    "description": (
        "Force-stop one installed third-party Android app after user approval. "
        "The effect is temporary and system packages are refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "Exact Android package name."},
            "serial": {"type": "string", "description": "Optional adb device serial."},
        },
        "required": ["package"],
    },
}

BACKGROUND_DATA_SCHEMA = {
    "name": "andreed_set_background_data",
    "description": (
        "Restrict or restore background network access on metered networks for one third-party Android app UID "
        "after user approval. Shared UIDs are refused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "Exact Android package name."},
            "restricted": {"type": "boolean", "description": "true to restrict, false to restore."},
            "serial": {"type": "string", "description": "Optional adb device serial."},
        },
        "required": ["package", "restricted"],
    },
}

TRIM_CACHES_SCHEMA = {
    "name": "andreed_trim_caches",
    "description": (
        "Ask Android to trim disposable app caches device-wide until a target amount of additional free "
        "space is available. Requires user approval and never clears app user data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "requested_mb": {
                "type": "integer",
                "default": 256,
                "minimum": 16,
                "maximum": 4096,
                "description": "Additional free-space target in MiB.",
            },
            "serial": {"type": "string", "description": "Optional adb device serial."},
        },
    },
}


def _approval(tool_name: str = "", args: dict | None = None, **kwargs: Any) -> dict | None:
    del kwargs
    values = args or {}
    serial = re.sub(r"[^A-Za-z0-9._:-]", "_", str(values.get("serial") or "auto-selected"))[:80]
    nonce = next(_APPROVAL_NONCES)
    if tool_name == "andreed_force_stop":
        package = re.sub(r"[^A-Za-z0-9._]", "_", str(values.get("package") or "missing"))[:160]
        return {
            "action": "approve",
            "message": f"Andreed wants to force-stop Android app {package} on device {serial}. This interrupts its background work.",
            "rule_key": f"andreed:force-stop:{serial}:{package}:{nonce}",
        }
    if tool_name == "andreed_set_background_data":
        package = re.sub(r"[^A-Za-z0-9._]", "_", str(values.get("package") or "missing"))[:160]
        action = "restrict" if values.get("restricted") is True else "restore"
        return {
            "action": "approve",
            "message": f"Andreed wants to {action} metered background data for Android app {package} on device {serial}.",
            "rule_key": f"andreed:metered-background-data:{serial}:{action}:{package}:{nonce}",
        }
    if tool_name == "andreed_trim_caches":
        amount = values.get("requested_mb", 256)
        return {
            "action": "approve",
            "message": (
                f"Andreed wants device {serial} to trim disposable app caches device-wide toward "
                f"{amount} MiB of additional free space. App user data is not cleared."
            ),
            "rule_key": f"andreed:trim-caches:{serial}:{amount}:{nonce}",
        }
    return None


def _available() -> bool:
    return shutil.which("adb") is not None


def register(ctx: Any) -> None:
    ctx.register_tool(name="andreed_doctor", toolset=PLUGIN_NAME, schema=DOCTOR_SCHEMA, handler=andreed_doctor, emoji="🩺")
    ctx.register_tool(name="andreed_scan", toolset=PLUGIN_NAME, schema=SCAN_SCHEMA, handler=andreed_scan, check_fn=_available, emoji="📊")
    ctx.register_tool(name="andreed_force_stop", toolset=PLUGIN_NAME, schema=FORCE_STOP_SCHEMA, handler=andreed_force_stop, check_fn=_available, emoji="⏹️")
    ctx.register_tool(name="andreed_set_background_data", toolset=PLUGIN_NAME, schema=BACKGROUND_DATA_SCHEMA, handler=andreed_set_background_data, check_fn=_available, emoji="📵")
    ctx.register_tool(name="andreed_trim_caches", toolset=PLUGIN_NAME, schema=TRIM_CACHES_SCHEMA, handler=andreed_trim_caches, check_fn=_available, emoji="🧹")
    ctx.register_hook("pre_tool_call", _approval)
    skill_path = Path(__file__).parent / "skills" / "andreed" / "SKILL.md"
    try:
        ctx.register_skill(
            "andreed",
            skill_path,
            description=(
                "Audit an authorized Android phone, identify high-data third-party apps, "
                "restrict background data, force-stop apps and safely trim disposable caches."
            ),
        )
    except TypeError:
        ctx.register_skill("andreed", skill_path)
