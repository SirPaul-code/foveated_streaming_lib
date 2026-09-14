# Realtime streaming readiness

Last updated: 2026-09-14

## Current status in one sentence

FoveaStream now has a **causal, provider-agnostic push-frame streaming runtime with persistent multi-ROI tracking and a sink contract**, but concrete platform zero-copy capture/encoder integrations and provider/transport adapters remain host/application work.

## Implemented now

### Runtime contract

Both Rust and Python expose the same architecture:

```text
timestamped frame + arbitrary ROI proposals + optional points/signals
        |
        v
persistent multi-ROI tracker
        |
        v
quality map + scheduler
        |
        +--> same-size foveated frame
        +--> low-res global context + N high-res ROI views
        +--> encoder delta-QP map
        +--> SEND/SKIP decision
        +--> ROI/track metadata
        |
        v
StreamSink / CallbackSink / arbitrary application adapter
```

Rust entry points:

- `StreamRuntime`
- `StreamRuntimeConfig::{balanced, aggressive, extreme}`
- `FrameInput`
- `ProcessResult`
- `StreamSink`
- `MultiRoiTracker`
- `RoiProposal`

Python entry points:

- `FoveaStreamRuntime`
- `StreamRuntimeConfig.preset(...)`
- `ProcessResult`
- `CallbackSink`
- `run_stream(...)`
- `MultiRoiTracker`
- `RoiProposal`
- optional `ClassAgnosticMotionRoiDetector`

## Multiple ROI support

The runtime no longer assumes a single attention rectangle.

`MultiRoiTracker` can maintain several independent regions at the same time. It:

- deduplicates overlapping proposals from different evidence sources;
- associates proposals to persistent tracks;
- keeps stable track IDs;
- estimates rectangle velocity;
- predicts regions forward by a latency horizon;
- expands stale/uncertain support;
- ranks regions by confidence and priority;
- keeps multiple regions under `max_tracks` and a total normalized ROI pixel budget.

The core intentionally does not contain face/car/person recognition. An ROI is defined only as spatial support that is valuable to the downstream task. Proposal sources are replaceable.

The Python reference includes class-agnostic camera-motion-compensated residual-motion proposals as a useful fallback when the host has no better relevance signal.

## Causality / frame-by-frame operation

The runtime is causal. Frame N can be processed and emitted before frame N+1 exists. It does not require a complete video file or future frames.

The real-video benchmark also uses persistent FFmpeg encoder pipes, so the demonstrated processing path is frame sequential rather than batch-only.

## Transport/provider independence

The runtime does **not** open network connections and does **not** import provider SDKs.

A transport/model adapter consumes `ProcessResult` and chooses one representation:

```text
ordinary video/image consumer  -> foveated frame
multi-image VLM/API            -> context + ROI views
ROI-aware encoder              -> original frame + QP map
custom tiled transport         -> ROI metadata / quality map
redundant frame                -> respect SEND/SKIP and send nothing
```

This keeps the same core usable with WebSocket, HTTP, WebRTC, RTP, GStreamer, gRPC, queues, shared memory, local inference or future APIs.

See `docs/AGENT_INTEGRATION.md` and root `AGENTS.md`.

## Presets

Reference presets exist in both the MP4 benchmark and streaming runtime:

- `balanced`
- `aggressive`
- `extreme`

The aggressive reference policy uses a 1/16 peripheral scale, 0.15 context scale and a multi-ROI budget. It is intended as a tunable reference point, not a guaranteed quality setting for every task.

## What is still not production platform plumbing

The generic streaming contract is implemented; the following remain concrete adapters/optimizations rather than core runtime features:

- CameraX/Camera2 direct camera adapter;
- AVFoundation/CVPixelBuffer direct camera adapter;
- OpenXR/Meta camera adapter;
- native YUV/NV12 hot path;
- zero-copy AHardwareBuffer / IOSurface / DMA-BUF integration;
- MediaCodec ROI/QP adapter;
- NVENC ROI/QP adapter;
- VideoToolbox/VAAPI-specific actuator integration;
- concrete WebRTC/RTP/GStreamer clients;
- provider-specific WebSocket/HTTP SDK clients;
- host-specific async bounded queues and backpressure;
- end-to-end latency/energy benchmarks on real target devices.

Those pieces should plug into the source/sink edges without changing `StreamRuntime` semantics.

## Recommended production host topology

```text
capture / sensor threads
        |
        v
bounded latest-frame/evidence queue
        |
        v
FoveaStream analysis/runtime thread
        |
        +--> local state continues every analysis frame
        |
        v
bounded selected-output queue
        |
        v
encoder / transport / model adapter thread
```

Do not use unbounded queues. Under overload, stale frames should be dropped rather than accumulated into seconds of latency.

Capture FPS, local analysis FPS and output/model FPS are independent.

Example:

```text
camera:       60 FPS
ROI analysis: 30-60 FPS
IMU:          200 Hz
transport:     5 FPS or event-driven
model:         whatever the consumer allows
```

## Validation requirement

A transport integration is not considered production-ready until it measures:

- processing p50/p95;
- end-to-end latency p50/p95;
- output frames/s and bytes/s;
- context + ROI decoded/model pixels/s;
- active ROI count and area;
- target coverage / important-region miss rate;
- downstream task quality against full-frame and simple downscale baselines;
- CPU/GPU/NPU and energy where relevant.

The product claim remains: **lower total bytes/tokens/latency/energy at equivalent task quality and bounded miss probability**.
