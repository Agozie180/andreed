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
no credentials, starts no daemon, sends no telemetry, and makes no third-party
network requests. Only use it with a phone whose owner authorized debugging.

Network usage is approximate historical data from `dumpsys netstats`, not a
live packet monitor. Force-stop is temporary. Cache trimming is best-effort.

## Development and tests

The tests use a fake ADB transport and never touch a phone:

```bash
python -m unittest discover -s tests -v
```

## License

MIT
