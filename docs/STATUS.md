# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a provider-agnostic adaptive relevance transport SDK that can be inserted between an existing camera/frame source and an existing encoder, transport, model or callback without forcing either side to adopt FoveaStream internals.

Stable product boundary:

```text
existing source
    -> timestamped frame + optional relevance evidence
    -> persistent relevance / multi-ROI state
    -> one or more transport actuators
    -> existing consumer
```

Do not turn core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

Feature branch: `feat/relevance-transport-stack`

PR: `#5 Add adaptive relevance transport stack`

Base `main` before this work: `3828a26cf2db42989866f54201cddca0aeed766e`

Package version on this branch: `0.3.0` in Python and Rust manifests.

At the time this handoff was written PR #5 was open and awaiting CI. Check the PR before claiming these features are on `main`.

## Existing merged middleware checkpoint

PR #4 / merge commit `77324c90f10a51ec39bb05ca8bb4b996b6516ae2` already provides:

- causal frame-by-frame processing;
- `FoveaStreamTransform` drop-in same-size frame transform;
- `FramePacket` / `OptimizedFrame`;
- bounded `RealtimeBridge` with `latest` drop policy;
- continuous-video `every_frame` semantics;
- opt-in scheduler-driven `when_send` semantics;
- persistent multi-ROI tracking;
- same-size foveated RGB;
- context + ROI views;
- block delta-QP maps;
- native Rust middleware API.

## New adaptive transport stack in PR #5

### Python: `python/foveastream/optimization.py`

#### `EvidenceBus`

Timestamped, TTL-bound relevance fusion from arbitrary independent sources.

A source may publish:

- ROI proposals;
- points;
- dense quality/relevance maps;
- per-source weight.

Dense maps support `max`, `noisy_or`, and clipped additive fusion. Dense fused maps are converted into class-agnostic ROI proposals before entering the existing tracker.

Do not hardcode semantic classes such as person/car/face into this layer.

#### `LowResProposalAdapter`

Runs arbitrary proposal logic on a downscaled analysis frame while preserving the original full-resolution frame for encoding/output. Coordinates remain normalized.

This formalizes the pattern already used by the cheap residual-motion proposal source.

#### `LatencyBudget`

Maps expected capture/analysis/encode/network/decode/consumer delay to the ROI tracker's prediction horizon.

The tracker therefore protects where relevance is expected to matter after downstream delay rather than only where the region was at capture time.

The horizon is clamped by `max_horizon_s`.

#### `TemporalRoiCache`

High-resolution ROI cache keyed by persistent tracks/ROI association.

Important behavior:

- first ROI is emitted;
- unchanged ROI reuses the last emitted high-res copy;
- material change emits a new ROI;
- max-refresh timeout forces refresh;
- change is compared against the **last emitted state**, not merely the previous frame, so slow drift eventually accumulates enough difference to refresh.

This reduces repeated high-resolution ROI payloads in VLM/layered transports.

#### `BackgroundTileCache`

Opt-in tile delta cache for mostly static cameras/backgrounds.

It emits only changed tiles or forced refreshes. It is deliberately **not enabled by default** because a moving camera naturally invalidates most tiles unless the host first performs camera/world-motion compensation.

#### `LayeredPayload`

Represents:

```text
low-res whole-scene context
+
changed high-res ROI enhancements
+
optional changed background tiles
```

This supports a base + enhancement transport where global scene context remains available even if enhancement packets are delayed/dropped.

#### Packed ROI atlas

`pack_roi_atlas(...)` packs:

- global context;
- only changed ROI enhancements;

into one ordinary RGB image plus placement metadata.

Use this for consumers that accept one image but cannot accept a list of context/ROI images.

#### `EncoderSpatialHints`

Portable encoder control-plane contract:

- block size;
- signed `int8` delta-QP map;
- active ROIs;
- fovea/periphery reference QP deltas.

`to_bytes()` exposes a row-major signed int8 map for native adapters.

**Do not claim this is already a direct NVENC/MediaCodec/VideoToolbox integration.** Those platform adapters must translate this portable contract to their concrete encoder APIs.

#### `AdaptiveBudgetController`

Closed-loop spatial budget controller driven by actual encoder bitrate and/or layered pixel fraction.

It continuously interpolates between balanced-like and extreme-like policies by adjusting:

- peripheral scale;
- falloff width;
- total ROI budget;
- max active ROI count;
- context scale;
- quality floor;
- uncertainty floor;
- periphery delta-QP.

A fixed preset is therefore only an initial condition, not a permanent bitrate policy.

#### `AdaptiveTransportRuntime`

Canonical high-level orchestrator combining:

```text
EvidenceBus
-> low-cost relevance proposals
-> predictive multi-ROI runtime
-> temporal cache
-> optional background cache
-> layered payload
-> optional packed atlas
-> encoder spatial hints
-> optional adaptive budget controller
```

It remains provider/transport agnostic.

Drop-in output is still available as:

```python
out = optimizer.process(frame_rgb, timestamp_s)
downstream.send(out.frame_rgb)
```

Advanced consumers can instead use:

```python
out.layered.context
out.layered.changed_rois
out.layered.atlas
out.encoder_hints.qp_delta_map
```

### Native Rust: `src/transport.rs`

Native control-plane primitives now mirror the pieces that do not require Python/OpenCV image plumbing:

- `LatencyBudget`;
- `AdaptiveBudgetConfig`;
- `AdaptiveBudgetController`;
- `AdaptiveBudgetState`;
- `EvidenceBus` / `EvidenceRecord`;
- `RegionSignature` / `signature_rgb8`;
- `TemporalRegionCache`;
- `EncoderSpatialHints`;
- `analysis_dimensions`.

The Rust layer intentionally does not fake a platform hardware-encoder API.

## Examples / docs

New:

- `docs/ADAPTIVE_TRANSPORT.md` — complete architecture and integration guide;
- `examples/adaptive_transport.py` — live camera -> adaptive transport -> ordinary preview, with examples for layered/VLM/QP paths;
- `bench/benchmark_transport_stack.py` — preprocessing/cache/layered/QP benchmark runner.

Existing integration docs remain relevant:

- `AGENTS.md`;
- `docs/FRAME_MIDDLEWARE.md`;
- `docs/AGENT_INTEGRATION.md`;
- `docs/REALTIME_STREAMING_STATUS.md`.

A new agent should read `docs/ADAPTIVE_TRANSPORT.md` immediately after `AGENTS.md`.

## Benchmarking

Existing checked-in codec benchmark remains authoritative for current same-encoder H.264 byte results:

- example1 aggressive: 71.30% H.264 saving; 96.26% context+ROI pixel saving;
- example2 aggressive: 31.34% H.264 saving; 84.08% context+ROI pixel saving.

New transport-stack benchmark records:

- mean / p50 / p95 / p99 preprocessing latency;
- effective throughput;
- active ROI count;
- changed ROI count;
- temporal reuse fraction;
- layered payload pixel fraction;
- atlas pixel fraction;
- background changed-tile rate;
- delta-QP map statistics;
- adaptive controller state.

Command:

```bash
python bench/benchmark_transport_stack.py example1.mp4 example2.mp4 \
  --preset aggressive \
  --out output/transport_benchmark.json
```

The transport-stack benchmark does not pretend that a delta-QP map has been consumed by a hardware encoder. Actual hardware encoder byte savings must be benchmarked only after a concrete adapter exists.

## Verification added in PR #5

Python tests cover:

- EvidenceBus fusion/TTL expiration;
- temporal ROI cache reuse and material-change refresh;
- background tile cache behavior;
- low-resolution proposal adapter;
- latency-horizon application;
- adaptive controller response above target;
- full `AdaptiveTransportRuntime` layered/atlas/QP output;
- context + changed ROI atlas packing.

Rust tests cover:

- latency horizon application;
- adaptive controller response;
- EvidenceBus expiration;
- temporal region cache change detection;
- native RGB region signatures;
- low-resolution analysis sizing.

PR #5 must pass Python tests and Rust `cargo test --all-targets` + release build on Ubuntu/macOS/Windows before merge.

## Exact implementation boundary

Implemented / valid to claim after PR #5 merges:

- same-size foveated transport;
- multi-source relevance fusion;
- low-res analysis/full-res preservation;
- persistent predictive multi-ROI state;
- latency-aware prediction horizon;
- temporal ROI reuse;
- static-background tile reuse;
- low-res base + changed high-res enhancement payload;
- one-image context+ROI atlas;
- portable encoder delta-QP hints;
- adaptive target bitrate/pixel-budget control;
- native control-plane equivalents;
- reproducible benchmark runner.

Still adapter/platform work, not valid to claim as complete:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI QP wiring;
- native NV12/YUV hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- concrete WebRTC/RTP/GStreamer layered packetization;
- world-locked background cache for freely moving cameras;
- stateful C ABI wrapper for the new high-level transport runtime;
- target-device p50/p95/energy characterization;
- downstream task-quality validation across production datasets.

## Next priorities after PR #5

### P0 — concrete encoder adapter

Start with one encoder family and translate `EncoderSpatialHints` into its real spatial ROI/QP API. Benchmark against ordinary CRF/QP changes at equivalent downstream task quality.

### P1 — native pixel formats

Add explicit pixel-format/stride contract and NV12/YUV processing to remove RGB round trips.

### P2 — moving-camera world cache

Use camera pose/homography/depth where available to keep a world-locked cache instead of invalidating all background tiles during camera motion.

### P3 — downstream quality benchmark

Compare full resolution, global quality reduction, global downscale, same-size foveation, layered context+ROI, temporal cache and encoder-QP mode at equal downstream task quality.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.

The SDK is useful only if the integration remains simpler than rebuilding these policies independently in every camera/vision application.
