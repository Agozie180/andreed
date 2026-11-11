# Andreed live-device self-audit

This audit records the real-device finding that changed the submitted plugin.
It is included because the limitation is as important as the happy path.

- Audit date: 11 November 2026
- Fix commit: [`951a719`](https://github.com/Agozie180/andreed/commit/951a7191d59455a2d2f0a740e3b898c283e692ad)
- Scope: read-only doctor and scan; no mutating tool was approved or executed

## Probe

- Device class: consumer Android phone
- Android: 15 / SDK 35
- Transport: owner-authorized encrypted wireless ADB
- Test type: read-only doctor and scan
- Live observations at test time: 146 third-party packages and 66% storage used

No app was stopped, no policy was changed and no cache was trimmed during the
live verification.

## Finding

The device returned more than Andreed's 200 KB processing cap for
`dumpsys netstats detail`. The initial implementation correctly refused the
oversized result, but that meant the whole scan failed even though Android also
offered a smaller UID summary.

## Fix

Andreed keeps the cap and does not parse the oversized historical result. It
falls back to Android's compact `mAppUidStatsMap` since-boot UID totals, labels
the result `since_boot_summary`, and returns metered values as `null` because
that compact view cannot support an honest metered split.

The fix deliberately does not increase the cap or turn unknown values into
zero.

## Regression proof

[`test_oversized_detail_falls_back_to_bounded_since_boot_totals`](tests/test_andreed.py)
feeds a result larger than the cap and verifies:

1. the scan still succeeds;
2. the scope is explicitly `since_boot_summary`;
3. metered availability is false;
4. total UID counters remain ranked; and
5. metered totals remain `null`, not a misleading zero.

Reproduce the full suite:

```bash
python -m unittest discover -s tests -v
```

## Remaining limitations

The output cap is a post-capture processing limit in the current implementation,
not a streaming memory limit. The compact fallback worked on the tested Android
15 build, but OEM output formats can differ. Unsupported formats are reported as
unavailable rather than guessed.
