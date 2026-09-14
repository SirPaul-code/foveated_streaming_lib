# Commercialization and licensing

## What to sell

Do not sell "foveated blur". Sell measurable **cost/latency reduction for continuous machine vision** at a bounded task-quality loss.

Buyer-facing value can be fewer uploaded MB/GB, fewer visual pixels/tokens/API cost, lower latency, more cameras per edge device, or preserved inspection detail.

## No-hosting licensing reality

If the full source or unrestricted binary runs completely offline and never contacts anything controlled by the vendor, there is no reliable technical way to know that an unauthorized company is using it. Contractual licensing can forbid use, but detection needs some observable event such as activation, update checks, telemetry, support, or external evidence.

## Recommended model

### Proprietary native core + free evaluation/reference layer

- Keep production Rust/native backends proprietary or distribute signed binaries.
- Keep API/spec/benchmark and optionally a slower Python reference layer freely evaluable.
- Paid customers receive production binaries and a signed offline license.
- Verify the license locally using a public key embedded in the SDK; the private signing key never ships.
- License payload can contain organization, expiry/maintenance, product tier, major-version entitlement and optional device entitlement.

This requires no runtime server. It prevents casual key editing but cannot report where valid/cracked binaries run.

Keygen documents signed offline license files as a standard model: https://keygen.sh/docs/choosing-a-licensing-model/offline-licenses/

### Third-party online activation without our own infrastructure

If activation counts and revocation are needed, a licensing/merchant SaaS can provide the endpoint. Lemon Squeezy exposes activate/validate/deactivate license-key APIs: https://docs.lemonsqueezy.com/api/license-api

Trade-off: no server we operate, but runtime/activation depends on a third party.

## Packaging

```text
foveastream-core        proprietary Rust/C ABI
foveastream-python      reference/evaluation wrapper
foveastream-android     AAR/JNI + MediaCodec adapter
foveastream-apple       XCFramework + Metal/AVFoundation adapter
foveastream-desktop     C/C++ + NVENC/FFmpeg/GStreamer adapters
foveastream-bench       benchmark/calibration CLI
```

## Suggested commercial tiers

Start with annual commercial licensing, not per-frame hosting:

- free evaluation/non-commercial tier;
- indie/small commercial license;
- company/team license;
- OEM/device-fleet negotiated license;
- paid integration/support.

Do not set permanent prices until benchmark savings and willingness-to-pay are measured.

## What should remain open/free

Potentially open the API/header/spec, Python reference implementation, benchmark CLI and examples. Keep zero-copy hardware adapters, premium VLM adapters, OEM/fleet rights and production support commercial.

Avoid MIT/Apache on the monetizable production core if commercial-use control is part of the business model.
