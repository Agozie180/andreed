from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("andreed_plugin", ROOT / "__init__.py")
assert SPEC and SPEC.loader
plugin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = plugin
SPEC.loader.exec_module(plugin)

DEVICES = "List of devices attached\nSERIAL123 device product:pixel model:Pixel_8 device:shiba transport_id:1\n"
PACKAGES = "package:com.chat.app uid:10123\npackage:com.video.app uid:10124\n"
DF = "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-8 1000000 700000 300000 70% /data\n"
NETSTATS = """ident=[{type=MOBILE, metered=YES}]
uid=10123 set=DEFAULT tag=0x0
st=1700000000 rb=1000 rp=2 tb=500 tp=1 op=0
ident=[{type=WIFI, metered=NO}]
uid=10124 set=DEFAULT tag=0x0
st=1700000000 rb=9000 rp=9 tb=1000 tp=2 op=0
uid=10124 set=DEFAULT tag=0xf00
st=1700000000 rb=99999 rp=1 tb=99999 tp=1 op=0
"""


class FakeADB:
    def __init__(self):
        self.calls = []
        self.restricted = {10123}
        self.df_calls = 0

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        assert kwargs.get("shell") is False
        assert kwargs.get("capture_output") is True
        assert kwargs.get("timeout") is not None
        tail = argv[1:]
        stdout = ""
        if tail == ["devices", "-l"]:
            stdout = DEVICES
        elif tail == ["version"]:
            stdout = "Android Debug Bridge version 1.0.41\nVersion 35.0.2\n"
        elif tail[-2:] == ["am", "get-current-user"]:
            stdout = "0\n"
        elif "list" in tail and "packages" in tail:
            stdout = PACKAGES
        elif tail[-3:] == ["df", "-k", "/data"]:
            self.df_calls += 1
            stdout = DF.replace("300000", str(300000 if self.df_calls == 1 else 301000))
        elif tail[-3:] == ["dumpsys", "netstats", "detail"]:
            stdout = NETSTATS
        elif len(tail) >= 2 and tail[-2] == "pidof":
            if tail[-1] == "com.video.app":
                stdout = "4242\n"
            else:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        elif tail[-3:] == ["netpolicy", "list", "restrict-background-blacklist"]:
            stdout = "Restrict background blacklisted UIDs: " + " ".join(map(str, sorted(self.restricted)))
        elif "netpolicy" in tail and "restrict-background-blacklist" in tail:
            uid = int(tail[-1])
            self.restricted.add(uid) if "add" in tail else self.restricted.discard(uid)
        elif tail[-2:] == ["pm", "help"]:
            stdout = "trim-caches DESIRED_FREE_SPACE\nclear [--cache-only] PACKAGE\n"
        elif tail[-2:] == ["getprop", "ro.build.version.sdk"]:
            stdout = "35\n"
        elif tail[-2:] == ["getprop", "ro.build.version.release"]:
            stdout = "15\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


class AndreedTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeADB()
        self.which = patch.object(plugin.shutil, "which", return_value="adb")
        self.runner = patch.object(plugin.subprocess, "run", side_effect=self.fake)
        self.which.start()
        self.runner.start()

    def tearDown(self):
        self.runner.stop()
        self.which.stop()

    @staticmethod
    def payload(text):
        return json.loads(text)

    def test_parse_devices(self):
        devices = plugin._parse_devices(DEVICES)
        self.assertEqual(devices[0].serial, "SERIAL123")
        self.assertEqual(devices[0].details["model"], "Pixel_8")

    def test_multiple_ready_devices_require_an_explicit_serial(self):
        multiple = DEVICES + "SERIAL456 device product:pixel model:Pixel_7\n"

        def multiple_runner(argv, **kwargs):
            completed = self.fake(argv, **kwargs)
            if argv[1:] == ["devices", "-l"]:
                completed.stdout = multiple
            return completed

        with patch.object(plugin.subprocess, "run", side_effect=multiple_runner):
            with self.assertRaisesRegex(plugin.AndreedError, "More than one"):
                plugin._select_device()

    def test_unauthorized_device_is_not_selected(self):
        unauthorized = "List of devices attached\nSERIAL123 unauthorized transport_id:1\n"

        def unauthorized_runner(argv, **kwargs):
            completed = self.fake(argv, **kwargs)
            if argv[1:] == ["devices", "-l"]:
                completed.stdout = unauthorized
            return completed

        with patch.object(plugin.subprocess, "run", side_effect=unauthorized_runner):
            with self.assertRaisesRegex(plugin.AndreedError, "No authorized"):
                plugin._select_device()

    def test_invalid_serial_is_rejected_before_adb(self):
        with self.assertRaisesRegex(plugin.AndreedError, "Invalid Android device serial"):
            plugin._select_device("SERIAL123;whoami")

    def test_package_injection_is_rejected_before_mutation(self):
        result = self.payload(plugin.andreed_force_stop({"package": "com.ok;rm -rf /"}))
        self.assertFalse(result["success"])
        self.assertFalse(any("force-stop" in call[0] for call in self.fake.calls))

    def test_scan_ranks_apps_and_ignores_tagged_rows(self):
        result = self.payload(plugin.andreed_scan({"limit": 2}))
        self.assertTrue(result["success"])
        self.assertEqual(result["top_data_apps"][0]["packages"], ["com.video.app"])
        self.assertEqual(result["top_data_apps"][0]["total_bytes"], 10000)
        self.assertEqual(result["top_data_apps"][0]["metered_total_bytes"], 0)
        self.assertTrue(result["top_data_apps"][0]["main_process_running"])
        self.assertTrue(result["top_data_apps"][1]["metered_background_data_restricted"])
        self.assertEqual(result["top_data_apps"][1]["metered_total_bytes"], 1500)

    def test_unparseable_netstats_is_reported_as_unavailable_not_zero(self):
        def no_usage_runner(argv, **kwargs):
            completed = self.fake(argv, **kwargs)
            if argv[-3:] == ["dumpsys", "netstats", "detail"]:
                completed.stdout = "OEM format not understood\n"
            return completed

        with patch.object(plugin.subprocess, "run", side_effect=no_usage_runner):
            result = self.payload(plugin.andreed_scan({"limit": 1}))
        self.assertTrue(result["success"])
        self.assertFalse(result["data_usage_available"])
        self.assertIsNone(result["top_data_apps"][0]["total_bytes"])

    def test_system_only_netstats_is_not_reported_as_app_usage(self):
        def system_usage_runner(argv, **kwargs):
            completed = self.fake(argv, **kwargs)
            if argv[-3:] == ["dumpsys", "netstats", "detail"]:
                completed.stdout = "uid=1000 set=DEFAULT tag=0x0 rxBytes=99 txBytes=1\n"
            return completed

        with patch.object(plugin.subprocess, "run", side_effect=system_usage_runner):
            result = self.payload(plugin.andreed_scan({"limit": 1}))
        self.assertTrue(result["success"])
        self.assertFalse(result["data_usage_available"])

    def test_force_stop_uses_bounded_argv(self):
        result = self.payload(plugin.andreed_force_stop({"package": "com.chat.app"}))
        self.assertTrue(result["success"])
        commands = [call[0] for call in self.fake.calls]
        self.assertIn(["adb", "-s", "SERIAL123", "shell", "am", "force-stop", "--user", "0", "com.chat.app"], commands)

    def test_background_policy_is_changed_and_verified(self):
        result = self.payload(plugin.andreed_set_background_data({"package": "com.video.app", "restricted": True}))
        self.assertTrue(result["success"])
        self.assertTrue(result["metered_background_data_restricted"])

    def test_shared_uid_is_refused(self):
        shared = PACKAGES + "package:com.video.helper uid:10124\n"

        def shared_runner(argv, **kwargs):
            completed = self.fake(argv, **kwargs)
            if "packages" in argv:
                completed.stdout = shared
            return completed

        with patch.object(plugin.subprocess, "run", side_effect=shared_runner):
            result = self.payload(plugin.andreed_set_background_data({"package": "com.video.app", "restricted": True}))
        self.assertFalse(result["success"])
        self.assertIn("shared", result["error"])

    def test_trim_caches_never_uses_pm_clear(self):
        result = self.payload(plugin.andreed_trim_caches({"requested_mb": 64}))
        self.assertTrue(result["success"])
        commands = [" ".join(call[0]) for call in self.fake.calls]
        self.assertTrue(any("pm trim-caches" in command for command in commands))
        self.assertFalse(any("pm clear " in command for command in commands))

    def test_impossible_trim_target_is_refused(self):
        result = self.payload(plugin.andreed_trim_caches({"requested_mb": 4096}))
        self.assertFalse(result["success"])
        self.assertIn("exceeds total storage", result["error"])
        self.assertFalse(any("pm trim-caches" in " ".join(call[0]) for call in self.fake.calls))

    def test_mutations_request_approval_but_reads_do_not(self):
        self.assertIsNone(plugin._approval("andreed_scan", {}))
        stop = plugin._approval("andreed_force_stop", {"package": "com.chat.app", "serial": "SERIAL123"})
        self.assertEqual(stop["action"], "approve")
        self.assertIn("SERIAL123", stop["rule_key"])
        policy = plugin._approval("andreed_set_background_data", {"package": "com.chat.app", "restricted": True, "serial": "SERIAL123"})
        self.assertEqual(policy["action"], "approve")
        self.assertIn("metered", policy["message"])
        trim = plugin._approval("andreed_trim_caches", {"requested_mb": 128, "serial": "SERIAL123"})
        self.assertEqual(trim["action"], "approve")
        self.assertIn("device-wide", trim["message"])
        self.assertNotEqual(
            stop["rule_key"],
            plugin._approval("andreed_force_stop", {"package": "com.chat.app", "serial": "SERIAL123"})["rule_key"],
        )

    def test_registration_matches_manifest(self):
        class Context:
            def __init__(self):
                self.tools, self.hooks, self.skills = [], [], []

            def register_tool(self, **kwargs):
                self.tools.append(kwargs["name"])

            def register_hook(self, name, callback):
                self.hooks.append(name)

            def register_skill(self, name, path, **kwargs):
                self.skills.append((name, Path(path)))

        ctx = Context()
        plugin.register(ctx)
        self.assertEqual(ctx.tools, ["andreed_doctor", "andreed_scan", "andreed_force_stop", "andreed_set_background_data", "andreed_trim_caches"])
        self.assertEqual(ctx.hooks, ["pre_tool_call"])
        self.assertTrue(ctx.skills[0][1].is_file())


if __name__ == "__main__":
    unittest.main()
