---
name: andreed
description: Audit and care for an authorized Android phone through bounded ADB tools.
---

# Andreed Skill

Andreed gives Hermes deterministic Android device-care tools. It can inspect an
authorized phone, identify high-data third-party apps, restrict their metered background
network access, force-stop them, and ask Android to trim disposable caches. It
does not expose a raw ADB shell, mutate system packages, clear app user data, or
silently delete personal files.

## When to Use

Use Andreed when the user asks to find apps consuming data, reduce background
usage, stop a selected third-party app, inspect free storage, or reclaim safe
cache space. Do not use it for surveillance, lock-screen bypasses, credentials,
factory resets, uninstalling apps, or a phone the user did not authorize.

## Prerequisites

1. Hermes runs on Windows, macOS, or Linux.
2. Android platform-tools (`adb`) is installed and on `PATH`.
3. The owner enabled Developer options and USB or wireless debugging.
4. The phone is unlocked and the owner accepted debugging authorization.

Run `andreed_doctor` first. If multiple devices are attached, pass the exact
`serial` returned by that tool to every later call.

## Procedure

1. Call `andreed_doctor`; stop if the device is unauthorized or ambiguous.
2. Call `andreed_scan`. Explain that network counters are approximate historical
   statistics, not proof of live activity.
3. Summarize the highest-usage apps and storage pressure without mutating them.
4. Ask which package and action the user wants.
5. Prefer `andreed_set_background_data` for durable data control on metered
   networks. Shared UIDs are refused.
6. Use `andreed_force_stop` only to interrupt current work; it is temporary.
7. Use `andreed_trim_caches` for safe storage recovery. It is device-wide and
   may trim caches for system apps or other users. Never fall back to `pm clear`.
8. Scan again and report the verified after-state.

Every mutation enters Hermes's approval flow. Never work around that boundary.

## Quick Reference

- `andreed_doctor(serial?)`
- `andreed_scan(serial?, limit=15)`
- `andreed_force_stop(package, serial?)`
- `andreed_set_background_data(package, restricted, serial?)`
- `andreed_trim_caches(requested_mb=256, serial?)`

## Pitfalls

- Android vendors vary and may expose different ADB output or policy support.
- `dumpsys netstats` is approximate and may span a long historical window.
- Background restriction operates on UIDs, not marketing app names.
- Force-stop is not permanent and cache trimming may recover zero bytes.
- Work-managed phones may disable ADB or network-policy operations.

## Verification

Run `andreed_doctor`, then `andreed_scan`. A successful scan shows one exact
device serial, the active user, storage totals, and third-party app count.
