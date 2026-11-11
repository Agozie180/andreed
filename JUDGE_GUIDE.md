# Andreed judge testing guide

This guide verifies the plugin without requiring a destructive action. The
recommended path takes about five minutes; the live mutation is optional.

| Check | Time | Changes the phone? |
|---|---:|---|
| Unit tests and Hermes validation | 1–2 min | No; tests use a fake ADB transport |
| Live doctor and scan | 2–3 min | No; read-only |
| Cancelled approval guardrail | 1 min | No; cancel before execution |
| Reversible background-data test | 2 min | Optional; yes, then restored |

## Prerequisites

- Hermes Agent `>=0.21.6`.
- Android platform-tools, with `adb version` working.
- For live checks, an Android phone owned or authorized by the tester.
- USB or wireless debugging enabled and the Android authorization prompt
  accepted.

Andreed needs no API key, cloud account or third-party service.

## 1. Install the reviewed code

Before the catalog PR merges, install the exact live-tested code revision:

```bash
hermes plugins install Agozie180/andreed --ref 951a7191d59455a2d2f0a740e3b898c283e692ad --enable
```

After the catalog entry is available, the equivalent reviewed install is:

```bash
hermes plugins install andreed --enable
```

Start a new Hermes session after installation. If necessary, enable the plugin
explicitly with `hermes plugins enable andreed`.

## 2. Verify without a phone

From the repository root, run:

```bash
python -m unittest discover -s tests -v
hermes plugins validate . --install-deps
```

Expected result:

- 16 tests pass.
- Hermes reports `Validation passed` with matching declared tools and hooks,
  no built-in tool collisions, a safe security scan and no core overrides.
- The tests do not call a real phone; they patch the ADB transport.

The suite proves package/serial injection rejection, ambiguous-device refusal,
system-package exclusion, shared-UID refusal, bounded cache trimming, unique
approval requests and the oversized-netstats fallback.

## 3. Run the live read-only path

Confirm that ADB sees the authorized device:

```bash
adb devices -l
```

Then paste this into a new Hermes session:

> Use Andreed to run the device doctor. Show the selected device, Android
> version and cache-trim support. Do not change anything on the phone.

Expected doctor result:

- `success` is `true`.
- Exactly one authorized device is selected, or Andreed asks for an explicit
  serial when more than one is ready.
- Android SDK/release and cache-trim support are reported.

Next paste:

> Use Andreed to scan my Android phone with a limit of 10. Show storage,
> third-party app count, available network totals, running state and metered
> background-data policy. Do not perform any mutation.

Expected scan result:

- `success` is `true`.
- The exact device and active Android user are reported.
- Storage totals and the third-party package count are present.
- Up to 10 UID rows contain package names, policy state and main-process state.
- `data_usage_scope` is either `historical_detail` or
  `since_boot_summary`; both are valid.

On an Android build whose detailed netstats exceeds Andreed's processing cap,
the scan intentionally uses `since_boot_summary`. Total counters remain
available, while metered counters are `null` and
`metered_data_usage_available` is `false`. That is the honest fallback, not a
failure or a measured zero.

The output can contain a device serial and installed package names. Andreed
sends no telemetry, but testers should avoid publishing raw output without
reviewing it.

## 4. Prove the approval boundary without changing the phone

Choose one exact third-party package and the serial returned by the scan, then
paste:

> Call `andreed_set_background_data` for package `<PACKAGE>` on serial
> `<SERIAL>` with `restricted=true`. I will cancel the Hermes approval. Do not
> use any other method.

Expected result:

1. Hermes shows an approval request naming the action, package and device.
2. Select **Cancel**.
3. The tool does not execute and no policy changes.

Run the read-only scan again if desired; the package's reported policy should
be unchanged. This is the central guardrail: the model can request a bounded
action, but cannot silently perform it.

## 5. Optional reversible live mutation

This step is not required to verify the submission. Only use a phone and app
the tester is authorized to modify.

1. Choose a non-critical third-party package from the scan and note its current
   `metered_background_data_restricted` value.
2. Request `andreed_set_background_data` with `restricted` set to the opposite
   value.
3. Review and approve the Hermes prompt.
4. Confirm the result reports the requested value.
5. Scan again to verify the after-state.
6. Restore the original value with the same tool, then approve and verify the
   result.

If Andreed reports that the UID is shared by multiple packages, the refusal is
expected. Choose a different package; Andreed will not apply a UID-wide policy
while presenting it as a one-app change.

There is no need to live-test force-stop or cache trimming. Force-stop can
interrupt app work, and cache trimming is device-wide and best-effort. Their
bounded commands and approval behavior are covered by the fake-ADB tests.

## Expected platform variation

- Network counters are Android/OEM-dependent and may be historical,
  since-boot-only or unavailable.
- A missing network measurement is returned as `null`, never invented as zero.
- Main-process state is a point-in-time `pidof` check and can change immediately.
- Work-managed devices may block ADB or netpolicy operations.
- Cache trimming may recover zero bytes because Android decides what is
  disposable.

## Troubleshooting

| Symptom | Meaning / next step |
|---|---|
| `adb was not found on PATH` | Install Android platform-tools and reopen the terminal. |
| Device is `unauthorized` | Unlock the phone and accept its debugging authorization prompt. |
| More than one device is ready | Pass the exact serial reported by `andreed_doctor`. |
| Package is refused | Use an exact installed third-party package from `andreed_scan`; system packages are intentionally excluded. |
| Shared UID is refused | Select another package; one policy change would otherwise affect several apps. |
| Metered totals are unavailable | Check `data_usage_scope`; the bounded since-boot fallback cannot honestly provide a metered split. |
| Tools are not visible | Run `hermes plugins enable andreed`, then start a new Hermes session. |

## Pass checklist

- [ ] The 16 fake-ADB tests pass.
- [ ] Hermes plugin validation passes.
- [ ] Doctor selects only one authorized device or requires an explicit serial.
- [ ] Scan returns real storage/package state without mutating the phone.
- [ ] Unknown network values remain unknown rather than becoming zero.
- [ ] A mutating tool produces a fresh, action-specific Hermes approval.
- [ ] Cancelling approval leaves the phone unchanged.
- [ ] If the optional policy mutation is tested, the after-state is verified and
      the original state is restored.

See [AUDIT.md](AUDIT.md) for the live Android 15 finding, fix and regression
proof.

## Cleanup

Restore any optional policy change before removing Andreed, then run:

```bash
hermes plugins disable andreed
hermes plugins remove andreed
```
