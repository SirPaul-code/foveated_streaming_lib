# Realtime streaming readiness

Last updated: 2026-09-14

## Current status in one sentence

FoveaStream has a **causal, provider-agnostic drop-in frame middleware with persistent multi-ROI tracking**, plus a bounded Python latest-frame bridge for callback-driven sources; platform-native zero-copy capture/encoder integrations remain adapter work.

## Preferred realtime contract

```text
existing camera / decoded-frame callback
        |
        v
FramePacket
        |
        v
FoveaStreamTransform / StreamMiddleware
        |
        v
OptimizedFrame / ProcessResult
        |
        v
existing encoder / transport / model / callback
```

The source and destination do not need to know how FoveaStream tracks relevance internally.

## Python middleware

Primary entry points:

- `FramePacket`;
- `OptimizedFrame`;
- `FoveaStreamTransform`;
- `CallbackFrameSink`;
- `InlinePipeline`;
- `RealtimeBridge`;
- `transform_source(...)`.

The simplest inline path is:

```python
optimizer = FoveaStreamTransform(preset="aggressive")
optimized = optimizer.transform(frame_rgb, timestamp_s)
downstream.send(optimized.frame_rgb)
```

The output frame has the same width/height as the input frame.

## Callback-driven capture / backpressure

`RealtimeBridge` is the reference asynchronous host adapter:

```python
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)

camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

With `latest`:

- the frame currently being processed finishes normally;
- the pending queue is bounded;
- when full, the oldest queued frame is replaced by the newest input;
- latency therefore does not grow indefinitely when capture outruns processing.

`drop_policy="block"` is available for sources that can accept backpressure and workflows that must process every submitted frame.

The Python bridge exposes counters for submitted, processed, emitted, scheduler-skipped, dropped-input and error frames plus maximum observed queue depth.

## Continuous video vs adaptive request suppression

`FoveaStreamTransform` defaults to:

```text
emit_policy = every_frame
```

This is intentional for camera -> encoder/video paths.

For request/event-driven consumers:

```text
emit_policy = when_send
```

may suppress redundant frames according to the adaptive scheduler.

SEND/SKIP is therefore not forced onto continuous video transports.

## Native Rust middleware

Rust exposes:

- `StreamMiddleware`;
- `EmitPolicy::EveryFrame`;
- `EmitPolicy::WhenSend`;
- `StreamRuntime`;
- `FrameInput`;
- `ProcessResult`;
- `StreamSink`;
- `MultiRoiTracker`;
- `RoiProposal`.

The Rust middleware remains synchronous and causal. Production platform adapters own their camera threads, queues, hardware encoders and network I/O.

## Multiple ROI support

`MultiRoiTracker` supports zero/one/many simultaneous regions. It:

- deduplicates overlapping proposals;
- associates proposals to persistent tracks;
- keeps stable track IDs;
- estimates rectangle velocity;
- predicts regions forward;
- expands stale/uncertain support;
- ranks by confidence and priority;
- enforces `max_tracks` and a hard total normalized ROI-area budget.

The core intentionally does not define ROI as face/car/person recognition. ROI proposal sources remain replaceable.

The Python reference includes camera-motion-compensated residual-motion proposals as a fallback when the host has no stronger relevance signal.

## Causality

Frame N can be processed and emitted before frame N+1 exists. The runtime requires no complete video file and no future frames.

## Output choices

A downstream adapter may consume:

```text
ordinary video/image consumer  -> same-size foveated frame
multi-image VLM/API            -> context + ROI views
ROI-aware encoder              -> original/native frame + QP map
custom transport               -> ROI metadata / quality field
event/request consumer         -> optional SEND/SKIP
```

This keeps the same runtime usable behind HTTP/WebSocket/WebRTC/RTP/GStreamer/gRPC/shared memory/local inference without coupling those protocols to core.

## What remains platform-native work

Not yet universally production-complete:

- CameraX/Camera2 direct adapter;
- AVFoundation/CVPixelBuffer direct adapter;
- OpenXR/Meta direct adapter;
- native YUV/NV12 hot path;
- zero-copy AHardwareBuffer / IOSurface / DMA-BUF;
- direct MediaCodec/NVENC/VideoToolbox/VAAPI ROI/QP wiring;
- concrete WebRTC/RTP/GStreamer adapters;
- stateful C ABI wrapper;
- real-device p50/p95/energy matrix.

These should plug into the source/sink edges without changing middleware semantics.

## Recommended native topology

```text
capture thread/callback
        |
        v
bounded latest-frame queue
        |
        v
FoveaStream native transform
        |
        v
bounded output / encoder / transport path
```

Never use an unbounded frame queue for latency-sensitive perception.

## Multi-rate operation

Capture, optimization and model/output rates are independent.

Example:

```text
camera:       60 FPS
optimizer:    30-60 FPS depending on target/device
IMU:          200 Hz
model:         1-10 FPS or event-driven
```

A lower model request rate does not require throwing away local temporal state.

## Validation requirement

Production integrations should measure:

- processing latency p50/p95/p99;
- source FPS / processed FPS;
- dropped stale input frames;
- queue depth;
- end-to-end latency;
- output bytes/s;
- decoded/model pixels/s;
- ROI count/area;
- important-region miss rate;
- downstream task quality;
- CPU/GPU/NPU/energy where relevant.

The target remains: **lower bytes/tokens/latency/energy at equivalent task quality and bounded important-region miss probability**.
