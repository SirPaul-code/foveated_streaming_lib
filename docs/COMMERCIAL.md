# Commercialization and licensing

Last updated: 2026-09-14

## What is actually being sold

Do **not** sell "foveated blur" or "gaze cropping". Those are features and substantial prior art exists.

Sell a measurable systems result:

> **FoveaStream reduces vision uplink / VLM pixel-token cost / latency / edge energy while maintaining a customer-defined downstream task-quality target.**

Examples:

- industrial inspection: preserve >= 99.5% defect recall while minimizing uploaded MB and Gemini/OpenAI visual input;
- smart glasses: maintain OCR/recognition success while minimizing wireless traffic and device power;
- robotics/bodycam: keep task-relevant regions at full fidelity and suppress redundant context;
- remote assistance: dynamically allocate bitrate to the currently manipulated/inspected object.

The commercial value is the **budget controller + native integrations + calibration evidence**, not the radial kernel.

---

## Best business model if we do not want to host inference

### Recommended: annual SDK license, edge-only runtime

The runtime remains entirely on the customer's device. We do not process frames and therefore do not pay GPU, bandwidth or storage bills.

Package:

```text
foveastream-core        proprietary optimized native library
foveastream-android     AAR/JNI, CameraX/ARCore/MediaCodec adapters
foveastream-apple       XCFramework, AVFoundation/ARKit/Metal adapters
foveastream-desktop     C ABI/C++ + FFmpeg/GStreamer/NVENC adapters
foveastream-python      integration and benchmarking wrapper
foveastream-calibrate   policy optimizer / benchmark CLI
```

Revenue is license + maintenance/support, not hosted compute.

### Why not usage billing initially

Usage billing creates exactly the infrastructure burden we are trying to avoid: metering, authenticated reporting, billing reconciliation and customer concerns about telemetry. It also weakens the edge/privacy story.

The customer should be able to run 100 cameras or zero cameras without our cloud being in the data path. Fleet/OEM rights can be priced separately.

---

## Licensing architecture

### Reality: offline binaries cannot phone home

If the customer has a full unrestricted binary/source and it never talks to anything controlled by the vendor, **we cannot reliably discover unauthorized use technically**. A cracked binary can always remove local checks. Offline licensing raises the cost of misuse; contracts/legal discovery provide the actual enforceable boundary.

Do not design fake "uncrackable" licensing.

### Option A — zero vendor hosting, normal internet activation

Use a merchant/licensing SaaS directly from the SDK activation tool.

Lemon Squeezy currently provides:

- automatic license-key generation after a purchase;
- activation / validation / deactivation API;
- subscription-linked expiry;
- activation limits;
- digital download delivery;
- no monthly ecommerce fee, with transaction pricing currently listed as 5% + $0.50.

Docs:
- https://docs.lemonsqueezy.com/api/license-api
- https://docs.lemonsqueezy.com/guides/tutorials/license-keys
- https://www.lemonsqueezy.com/pricing

Flow:

```text
customer -> Lemon Squeezy checkout
         -> receives SDK download + license key
foveastream-license activate KEY
         -> Lemon Squeezy API
         -> local activation receipt / cache
runtime -> local cached entitlement
```

No server that we operate and no frame/video telemetry.

Do not validate on every frame. Activation/periodic entitlement verification must be fully outside the hot path.

### Option B — fully offline / air-gapped enterprise customer

Issue an Ed25519-signed license document:

```json
{
  "license_id": "...",
  "customer": "Acme Inspection GmbH",
  "product": "foveastream-enterprise",
  "features": ["core","nvenc","android","vlm-adapters"],
  "not_before": "2026-09-14T00:00:00Z",
  "not_after": "2027-09-14T00:00:00Z",
  "major_versions": [1],
  "fleet_limit": 1000,
  "nonce": "..."
}
```

The payload is signed by a private key that never ships. The SDK contains only the public verification key. Verification happens locally.

This model is standard; Keygen documents cryptographically signed offline license files and Ed25519 verification for air-gapped software:
https://keygen.sh/docs/api/cryptography/

At low sales volume, these enterprise offline licenses can be generated manually from an issuer CLI. That requires **zero runtime service** and avoids building billing infrastructure prematurely.

### Tamper-resistance strategy

Use several cheap layers, without pretending they are DRM magic:

- production core ships as compiled native binary, not source;
- signed license entitlements;
- customer/license identifier embedded in distributed package metadata;
- per-customer artifact watermark/build ID for enterprise deliveries;
- feature gating at more than one call boundary;
- symbols stripped, LTO enabled;
- updates/support only for valid maintenance;
- commercial contract explicitly controls redistribution, OEM embedding and derived bindings.

The real moat must still be product performance and integration depth.

---

## Suggested commercial packaging

These are **pricing experiments**, not established market prices. Validate willingness-to-pay after real downstream benchmarks.

### Evaluation

- free benchmark CLI/reference Python layer;
- watermark/throughput limit or non-commercial terms;
- enough functionality to measure savings on the customer's own video;
- no production redistribution rights.

Goal: customer can prove the ROI before speaking to us.

### Indie / single product

Hypothesis: **EUR 399-999/year**.

- one developer / one commercial product;
- native core;
- Python/C API;
- community/email-level updates;
- no OEM redistribution.

### Team / company product

Hypothesis: **EUR 2,500-7,500/year**.

- multiple developers;
- Android/Apple/Desktop adapters;
- standard encoder/VLM adapters;
- benchmark/calibration tooling;
- internal deployment rights for one product/team.

### Enterprise/OEM/fleet

Hypothesis: **EUR 10k-50k+/year or negotiated per product/fleet**.

- offline/air-gapped license;
- fleet/device redistribution rights;
- source escrow/source option if priced appropriately;
- integration support;
- customer-specific encoder/device optimization;
- SLA/priority fixes.

For industrial customers, price against **money saved or capability enabled**, not against the number of Rust source files.

---

## Stronger enterprise pricing metric

The best sales proof is a before/after report:

```text
baseline:  12.4 GB/hour uplink, $X VLM/day, p95 Y ms, defect recall 99.7%
FoveaStream: 3.1 GB/hour, $0.31X VLM/day, p95 0.72Y ms, defect recall 99.6%
```

If the SDK saves a customer EUR 100k/year in networking/inference/edge hardware, a EUR 10k-30k annual license is easier to justify than a generic EUR 99 developer library.

The benchmark/calibration engine therefore has direct commercial importance.

---

## What can be public vs proprietary

Recommended split while validating the business:

### Public/evaluation

- headers/API specification;
- benchmark methodology and CLI;
- Python reference implementation;
- examples;
- papers/prior-art notes;
- possibly a deliberately non-optimized CPU backend.

### Proprietary paid core

- optimized Rust/C++ SIMD kernels;
- zero-copy platform adapters;
- NVENC/MediaCodec/VideoToolbox/VAAPI QP/ROI integrations;
- predictive fusion runtime;
- automatic policy optimizer;
- production VLM cost adapters;
- enterprise/OEM redistribution rights;
- tuned domain packs.

Do not put the monetizable production core under MIT/Apache if controlling commercial redistribution is part of the strategy.

---

## Distribution without our own servers

Possible stack:

```text
marketing/docs        GitHub Pages or static site
source/API examples   GitHub
binary releases       private GitHub Release or merchant-hosted digital download
checkout/tax          Lemon Squeezy
license activation    Lemon Squeezy directly
fully offline license local Ed25519 issuer CLI
support               GitHub Issues/email
```

This can operate with effectively **zero custom backend hosting**.

---

## What determines whether this is worth selling

Do not commercialize until all four are measured on real workloads:

1. meaningful reduction in transmitted bytes / visual tokens / model cost;
2. bounded downstream accuracy loss, ideally task-neutral or better;
3. preprocessing overhead materially lower than the cost being saved;
4. integration simple enough that a customer can add it without redesigning their camera stack.

If those hold, the business model is credible. If only JPEG size decreases while model token cost and task latency do not, it is not.
