# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a provider-agnostic frame optimization/relevance SDK that can be inserted between an existing camera/frame source and an existing encoder, transport, model or callback without making either side depend on FoveaStream internals.

Preferred product boundary:

```text
existing source
    -> timestamped frame + optional relevance evidence
    -> FoveaStream middleware
    -> optimized same-size frame + optional advanced outputs
    -> existing consumer
```

Do not turn the core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

Feature branch: `feat/drop-in-frame-middleware`

PR: `#4 Add drop-in frame middleware for camera pipelines`

Base main before this work: `c3aab631cd840e20d26cf28cb7f87201ef45534e`

Merge status: pending CI/merge at the time this handoff was written. Check PR #4 before claiming the feature is on `main`.

## Implemented native Rust core

Existing native capabilities:

- normalized point/ROI relevance;
- persistent multi-ROI tracking;
- hard total ROI-area budgeting;
- quality-field generation;
- same-size foveated RGB generation;
- context + multiple high-resolution ROI views;
- QP-delta map generation;
- predictive attention primitives;
- adaptive SEND/SKIP scheduler;
- synchronous `StreamRuntime`.

New `src/middleware.rs`:

- `EmitPolicy::EveryFrame`;
- `EmitPolicy::WhenSend`;
- `StreamMiddleware`;
- `StreamMiddleware::aggressive()`;
- middleware processing that returns `Option<ProcessResult>`;
- sink integration that only invokes a sink for emitted outputs.

`EveryFrame` is the safe/default semantic for continuous camera/video paths. `WhenSend` is explicit opt-in for model/request/event pipelines.

The native middleware remains synchronous and does not own camera threads, queues, encoders or sockets.

## Implemented Python middleware

New `python/foveastream/middleware.py`:

- `FramePacket` — RGB frame + monotonic timestamp + opaque metadata + optional relevance evidence;
- `OptimizedFrame` — same-size optimized frame + preserved timestamp/metadata + full `ProcessResult`;
- `FoveaStreamTransform` — persistent drop-in transform;
- `FrameTransform` / `FrameSink` protocols;
- `CallbackFrameSink`;
- `transform_source(...)` pull adapter;
- `InlinePipeline`;
- `RealtimeBridge`;
- `PipelineStats`.

Primary inline usage:

```python
optimizer = FoveaStreamTransform(preset="aggressive")
optimized = optimizer.transform(frame_rgb, timestamp_s)
downstream.send(optimized.frame_rgb)
```

Callback-driven camera usage:

```python
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)
camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

### Realtime queue rule

`RealtimeBridge(drop_policy="latest")` keeps a bounded pending queue and discards stale queued frames when capture outruns processing. The frame currently being processed is never interrupted.

This avoids the failure mode:

```text
60 FPS camera -> 30 FPS processing -> unbounded queue -> increasing end-to-end latency
```

Recommended latency-sensitive behavior:

```text
60 FPS camera -> queue depth 1/latest -> process freshest available state -> bounded latency
```

Use `drop_policy="block"` only when every frame must be processed and backpressure into the producer is acceptable.

## Metadata preservation

`FramePacket.metadata` is intentionally opaque and is copied to `OptimizedFrame.metadata` unchanged.

Platform adapters may store camera ID, frame sequence, RTP timestamp, tracing IDs or other application metadata without adding provider knowledge to core.

## ROI design

Do not hardcode faces/cars/people as the meaning of ROI.

An ROI means only:

> spatial support whose loss of detail would disproportionately harm the current downstream task.

Automatic residual-motion proposals are a fallback source. External task detectors, gaze, user input, AR anchors, OCR regions, depth, model feedback and other signals can inject `RoiProposal` values.

## Benchmark state

Current checked-in real-video benchmark data remains under:

`docs/assets/real_demo/benchmark_presets_2026-09-14.json`

Aggressive reference results from the two sample clips:

- example1: 71.30% same-encoder H.264 saving; 96.26% context+ROI pixel saving;
- example2: 31.34% same-encoder H.264 saving; 84.08% context+ROI pixel saving.

Those Python/OpenCV timings are reference-host measurements, not universal device latency claims.

## Docs / examples

Primary integration documentation:

- `AGENTS.md`;
- `docs/FRAME_MIDDLEWARE.md`;
- `docs/AGENT_INTEGRATION.md`;
- `docs/REALTIME_STREAMING_STATUS.md`.

Examples:

- `examples/live_webcam.py` — inline camera -> `FoveaStreamTransform` -> preview;
- `examples/custom_sink_adapter.py` — callback camera -> bounded `RealtimeBridge` -> arbitrary sink.

## Verification in this PR

New Python tests cover:

- same-size output shape;
- timestamp preservation;
- metadata preservation;
- `EveryFrame` default behavior;
- `WhenSend` suppression;
- synchronous source -> transform -> sink wiring;
- deterministic latest-frame queue dropping;
- worker error propagation.

New Rust tests cover:

- every-frame middleware semantics;
- scheduler-based suppression;
- sink invocation only for emitted results.

CI must pass Python tests and Rust test/release builds on Ubuntu, Windows and macOS before merge.

## Realtime readiness: exact claim

Implemented / valid to claim:

- causal frame-by-frame processing;
- persistent multi-ROI state;
- hard ROI budgets;
- same-size drop-in optimized frame output;
- generic source/transform/sink contract;
- bounded latest-frame Python bridge;
- explicit continuous-video vs SEND/SKIP semantics;
- native synchronous middleware API.

Not yet valid to claim as universally production-complete:

- native YUV/NV12 hot path;
- zero-copy AHardwareBuffer / CVPixelBuffer / DMA-BUF;
- direct CameraX/AVFoundation/OpenXR source adapters;
- direct MediaCodec/NVENC/VideoToolbox/VAAPI ROI/QP wiring;
- concrete WebRTC/RTP/GStreamer adapters;
- stateful C ABI wrapper;
- target-device p50/p95/energy characterization;
- production downstream task-accuracy validation.

## Next engineering priorities

### P0 — finish middleware checkpoint

1. Get PR #4 green across Python + Rust Linux/Windows/macOS.
2. Merge to `main`.
3. Keep README and `docs/FRAME_MIDDLEWARE.md` aligned with actual public APIs.

### P1 — pixel-format/native path

1. Add explicit frame pixel-format contract.
2. Add NV12/YUV input path to avoid RGB round trips.
3. Add stateful C ABI for `StreamMiddleware`.

### P2 — concrete source/encoder adapters

1. Android Camera2/CameraX + AHardwareBuffer + MediaCodec.
2. Apple AVFoundation/CVPixelBuffer/VideoToolbox.
3. Linux V4L2/GStreamer/DMA-BUF.
4. Windows Media Foundation / hardware encoder path.

Adapters must remain outside provider-agnostic core.

### P3 — benchmark / task quality

Measure bytes/pixels/tokens, p50/p95 latency, queue drops, CPU/GPU/NPU/energy and downstream task quality on real target devices.

## Win condition

A developer should be able to change:

```python
frame = camera.read()
downstream.send(frame)
```

to:

```python
frame = camera.read()
optimized = foveastream.transform(frame)
downstream.send(optimized.frame_rgb)
```

without rewriting their camera stack, transport, model client or encoder architecture.
