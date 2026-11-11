# Andreed

Andreed is a deterministic Android device-care plugin for
[Hermes Agent](https://github.com/NousResearch/hermes-agent). It audits an
authorized phone, identifies third-party apps with high historical data usage,
restricts metered background data, force-stops selected apps, and asks Android to trim
disposable caches.

Unlike a general phone controller, Andreed exposes no raw ADB shell and does not
run model-written code. Every command is a bounded argv template, system
packages are excluded, shared-UID policy changes are refused, and every
mutating tool goes through Hermes's approval flow.

## Install

Install Android platform-tools so `adb version` works, then install Andreed:

```bash
hermes plugins install Agozie180/andreed
```

Start a new Hermes session and ask:

> Use Andreed to scan my Android phone, show the apps using the most data, and
> recommend safe actions. Do not change anything until I approve it.

The phone owner must enable USB or wireless debugging, unlock the phone, and
accept the authorization prompt.

## Tools

| Tool | Behavior |
|---|---|
| `andreed_doctor` | Checks ADB, devices, Android version and cache-trim support. |
| `andreed_scan` | Reads storage, third-party packages, approximate total/metered traffic, main-process state and policies. |
| `andreed_force_stop` | Force-stops one third-party package after approval. |
| `andreed_set_background_data` | Restricts or restores background data on metered networks after approval. |
| `andreed_trim_caches` | Requests bounded, device-wide Android cache trimming after approval. |

## Safety and disclosure

Andreed executes local `adb` and bounded `adb shell` commands. It reads device
identifiers, Android version, third-party package names, UIDs, storage totals,
network statistics and policy state. It can interrupt a selected app, change
its metered background-data policy and ask Android to delete disposable caches
device-wide, which can include caches belonging to system apps or other users.

It never invokes `pm clear`, uninstalls apps, mutates system packages, accepts a
raw shell command, enters credentials, or bypasses Hermes approvals. It stores
no credentials, installs no background service, sends no telemetry, and makes
no third-party network requests. ADB itself can run its standard local server.
Only use Andreed with a phone whose owner authorized debugging.

Network usage is approximate data from `dumpsys netstats`, not a live packet
monitor. When a device's historical detail exceeds Andreed's processing cap,
the scan discards that detail, requests the smaller since-boot summary, and
explicitly marks metered usage unavailable. Force-stop is temporary. Cache
trimming is best-effort.

## Verified

- 16 mocked-device tests cover parsing, safety gates, approval requests, shared
  UIDs, bounded commands, cache trimming, and oversized netstats fallback.
- Live read-only doctor and scan verified on Android 15 (SDK 35) over encrypted
  wireless ADB. The live test exposed and now covers the oversized-netstats
  fallback described above.
- Mutating actions are tested with the fake transport and still require a fresh
  Hermes approval on a real phone; the live verification did not alter apps or
  settings.

## Development and tests

The tests use a fake ADB transport and never touch a phone:

```bash
python -m unittest discover -s tests -v
```

## License

MIT
