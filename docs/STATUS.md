# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a provider-agnostic adaptive relevance transport SDK that can be inserted between an existing camera/frame source and an existing encoder, transport, model or callback without forcing either side to adopt FoveaStream internals.

Stable product boundary:

```text
existing source
    -> timestamped frame + optional relevance evidence
    -> persistent relevance / predictive multi-ROI state
    -> one or more transport/encoder actuators
    -> existing consumer
```

Do not turn core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

Branch: `main`

Merged PR: `#5 Add adaptive relevance transport stack`

Squash merge commit: `81c9be9e5122a2dec7809b70add97818f4ed7be3`

Previous middleware merge: `77324c90f10a51ec39bb05ca8bb4b996b6516ae2`

Package version: `0.3.0` in Python and Rust manifests.

## Verification

PR #5 passed the configured CI matrix before merge:

- Python install/tests on Ubuntu — **PASS**;
- Rust `cargo test --all-targets` + `cargo build --release` on Ubuntu — **PASS**;
- Rust `cargo test --all-targets` + `cargo build --release` on macOS — **PASS**;
- Rust `cargo test --all-targets` + `cargo build --release` on Windows — **PASS**.

The first native CI run exposed only invalid shorthand Rust float literals in new tests (`.1`, `.02`, etc.). They were corrected before the final green run.

## Existing drop-in middleware

The merged middleware layer provides:

- causal frame-by-frame processing;
- `FoveaStreamTransform` as a same-size drop-in transform;
- `FramePacket` / `OptimizedFrame`;
- bounded `RealtimeBridge` with `latest` drop policy;
- continuous-video `every_frame` semantics;
- opt-in scheduler-driven `when_send` semantics;
- persistent multi-ROI tracking;
- same-size foveated RGB;
- low-resolution context + ROI views;
- block delta-QP maps;
- native Rust middleware API.

Recommended latency-sensitive topology:

```text
capture callback
    -> bounded queue depth 1 / latest
    -> FoveaStream
    -> encoder / model / transport
```

Do not use an unbounded queue for realtime capture. If capture outruns processing, prefer the freshest pending frame rather than accumulating latency.

## Adaptive transport stack

### Python: `python/foveastream/optimization.py`

#### `EvidenceBus`

Timestamped, TTL-bound relevance fusion from arbitrary independent sources.

A source may publish:

- ROI proposals;
- points;
- dense relevance/quality maps;
- a source weight.

Dense map fusion supports:

- `max`;
- `noisy_or`;
- clipped additive fusion.

Dense fused maps can be converted to class-agnostic ROI proposals before entering the persistent tracker.

Do not hardcode face/person/car semantics into this layer. Relevance may come from motion, gaze, OCR, a detector, user interaction, an AR anchor, task state, model feedback, or another application-specific source.

#### `LowResProposalAdapter`

Runs arbitrary proposal logic on a downscaled analysis image while preserving the original full-resolution frame for output/encoding.

Coordinates remain normalized, so the proposal source does not need resolution-specific coordinate conversion.

#### `LatencyBudget`

Maps expected capture/analysis/encode/network/decode/consumer delay to the tracker prediction horizon.

The runtime therefore protects where a tracked region is expected to matter after downstream latency, not only where it was at capture time.

The horizon is clamped by `max_horizon_s`.

#### `TemporalRoiCache`

Caches high-resolution ROI enhancements using persistent track/ROI association.

Behavior:

```text
first ROI                    -> SEND
same ROI materially same     -> REUSE cached high-res ROI
same ROI materially same     -> REUSE cached high-res ROI
material change              -> SEND
max-refresh timeout          -> SEND
```

Change is measured against the **last emitted ROI state**, not merely the immediately previous frame. Slow cumulative changes therefore eventually trigger a refresh.

#### `BackgroundTileCache`

Opt-in tile delta cache for mostly static cameras/backgrounds.

It emits only tiles whose signature changed or whose refresh timeout expired.

This is intentionally **disabled by default**. A freely moving camera invalidates many background tiles unless the host first performs camera/world-motion compensation.

#### `LayeredPayload`

Represents:

```text
low-resolution whole-scene base
+
changed high-resolution ROI enhancements
+
optional changed background tiles
```

This supports base + enhancement transport while preserving global scene context even if an enhancement is delayed or omitted.

#### Packed ROI atlas

`pack_roi_atlas(...)` packs:

- global context;
- only changed ROI enhancements;

into one ordinary RGB image plus placement/source metadata.

Use this for consumers that accept one image but cannot accept a list of context + ROI images.

#### `EncoderSpatialHints`

Portable encoder control-plane contract:

- encoder block size;
- signed `int8` delta-QP map;
- active normalized ROIs;
- fovea/periphery reference QP deltas;
- row-major byte export for native adapters.

**This is not a fake claim of completed NVENC/MediaCodec/VideoToolbox integration.** A concrete encoder adapter must translate these hints into the actual platform encoder API.

#### `AdaptiveBudgetController`

Closed-loop spatial budget controller driven by actual encoder bitrate and/or layered payload pixel fraction.

Instead of assuming a fixed preset always hits a fixed network budget, it continuously adjusts:

- peripheral scale;
- falloff width;
- total ROI budget;
- max active ROI count;
- context scale;
- quality floor;
- uncertainty floor;
- periphery delta-QP.

`balanced`, `aggressive`, and `extreme` remain useful initial conditions.

#### `AdaptiveTransportRuntime`

Canonical high-level orchestration:

```text
EvidenceBus
    -> cheap/local relevance analysis
    -> predictive persistent multi-ROI runtime
    -> temporal ROI cache
    -> optional static-background tile cache
    -> layered base + enhancement payload
    -> optional one-image atlas
    -> portable encoder spatial hints
    -> optional adaptive bitrate/pixel controller
```

Drop-in path remains simple:

```python
out = optimizer.process(frame_rgb, timestamp_s)
downstream.send(out.frame_rgb)
```

Advanced consumers can choose:

```python
out.layered.context
out.layered.changed_rois
out.layered.background_updates
out.layered.atlas
out.encoder_hints.qp_delta_map
out.process_result.decision
```

## Native Rust control plane

`src/transport.rs` exports provider-independent native primitives for:

- `LatencyBudget`;
- `AdaptiveBudgetConfig` / `AdaptiveBudgetController` / `AdaptiveBudgetState`;
- `EvidenceBus` / `EvidenceRecord`;
- `RegionSignature` / `signature_rgb8`;
- `TemporalRegionCache`;
- `EncoderSpatialHints`;
- low-resolution `analysis_dimensions`.

The Rust layer intentionally does not pretend that a generic QP map is already wired into every hardware encoder.

## Examples and documentation

Primary reading order for another coding agent:

1. `AGENTS.md`
2. `docs/ADAPTIVE_TRANSPORT.md`
3. `docs/FRAME_MIDDLEWARE.md`
4. `docs/AGENT_INTEGRATION.md`
5. this `docs/STATUS.md`
6. `docs/REALTIME_STREAMING_STATUS.md`

Examples:

- `examples/live_webcam.py` — drop-in live camera transform;
- `examples/custom_sink_adapter.py` — bounded realtime source -> transform -> arbitrary sink;
- `examples/adaptive_transport.py` — adaptive transport stack including layered/atlas/QP paths.

## Benchmarking

Existing checked-in real-video codec benchmark remains authoritative for current same-encoder H.264 byte results:

- `example1` aggressive: **71.30%** H.264 saving; **96.26%** context+ROI pixel saving;
- `example2` aggressive: **31.34%** H.264 saving; **84.08%** context+ROI pixel saving.

Those are reference-video measurements, not universal task-quality or target-device latency claims.

New runner:

`bench/benchmark_transport_stack.py`

It records:

- mean / p50 / p95 / p99 preprocessing latency;
- effective processing throughput;
- active ROI count;
- changed ROI count;
- temporal ROI reuse fraction;
- layered payload pixel fraction;
- atlas pixel fraction;
- background changed-tile rate;
- delta-QP statistics;
- adaptive controller state;
- source hash/environment metadata.

Example:

```bash
python bench/benchmark_transport_stack.py example1.mp4 example2.mp4 \
  --preset aggressive \
  --out output/transport_benchmark.json
```

This benchmark does not claim hardware-encoder QP savings until a concrete encoder adapter actually consumes `EncoderSpatialHints`.

## Exact implementation boundary

Implemented and valid to claim now:

- provider-agnostic drop-in frame middleware;
- causal frame-by-frame processing;
- bounded latest-frame realtime queue;
- persistent predictive multi-ROI state;
- hard total ROI-area budget;
- same-size foveated output;
- context + multiple high-resolution ROIs;
- multi-source relevance fusion with TTL;
- low-resolution analysis/full-resolution preservation;
- latency-aware prediction horizon;
- temporal ROI reuse;
- opt-in static-background tile reuse;
- low-resolution base + changed high-resolution enhancement payload;
- one-image context+ROI atlas;
- portable encoder delta-QP hints;
- adaptive bitrate/pixel-budget controller;
- Python reference and native Rust control-plane primitives;
- reproducible transport-stack benchmark runner.

Still adapter/platform work and **not** valid to claim as complete:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI QP wiring;
- native NV12/YUV hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- concrete WebRTC/RTP/GStreamer layered packetization;
- world-locked background cache for freely moving cameras;
- stateful C ABI wrapper for the high-level adaptive transport runtime;
- target-device p50/p95/energy characterization;
- downstream task-quality validation across production datasets.

## Next priorities

### P0 — concrete encoder adapter

Take `EncoderSpatialHints` and wire it to one real encoder family. Benchmark against ordinary global CRF/QP reduction at equivalent downstream task quality.

### P1 — native pixel formats

Add an explicit pixel-format/stride contract and native NV12/YUV processing to remove RGB round trips.

### P2 — moving-camera world cache

Use pose/homography/depth where available so cached background content remains world-locked instead of invalidating during camera motion.

### P3 — downstream quality benchmark

Compare at equivalent task quality:

- original full resolution;
- global CRF/QP reduction;
- global downscale;
- same-size foveation;
- layered context+ROI;
- temporal ROI reuse;
- encoder spatial-QP mode.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.

The SDK is useful only if integration remains substantially easier than rebuilding these policies independently in every camera/vision application.
