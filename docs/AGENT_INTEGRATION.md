# Integrating FoveaStream into an existing workflow

This document is for a coding agent that must insert FoveaStream into an arbitrary camera, video, streaming or vision-model workflow.

## 1. Integration objective

Do not rebuild the host application's camera/transport stack.

The preferred change is:

```text
BEFORE
source -> downstream

AFTER
source -> FoveaStreamTransform -> downstream
```

FoveaStream is not a Gemini/OpenAI/WebRTC/CameraX client. It is a stateful transform/control layer.

## 2. Start with the existing frame boundary

Find the point where the application already has a decoded frame and the point where it forwards that frame to an encoder, transport or model.

Typical examples:

```text
CameraX ImageAnalysis callback -> existing encoder
AVFoundation sample callback    -> existing model client
OpenCV cap.read()               -> recorder
WebRTC decoded frame callback   -> vision inference
RTSP/GStreamer decoder          -> custom transport
```

Insert FoveaStream at that boundary first. Do not replace the full media stack unless the host architecture forces it.

## 3. Synchronous Python path

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="every_frame",
)

while source.running:
    frame_rgb, timestamp_s = source.next_frame()
    optimized = optimizer.transform(frame_rgb, timestamp_s)
    downstream.send(optimized.frame_rgb)
```

`optimized.frame_rgb` is a same-size ordinary RGB frame. Existing downstream code can treat it as the original frame.

Advanced outputs remain attached at `optimized.result`.

## 4. Callback-driven source / bounded latency

If the source invokes a callback asynchronously, do not let frames build up in an unbounded queue.

```python
from foveastream import CallbackFrameSink, RealtimeBridge

sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)


def on_frame(frame_rgb, timestamp_s):
    bridge.submit(frame_rgb, timestamp_s)
```

`latest` means the newest pending frame replaces an older queued frame when processing falls behind. The currently-processing frame is not interrupted.

This is the recommended behavior for latency-sensitive camera/perception pipelines.

Use `drop_policy="block"` only when the source can safely accept backpressure and every submitted frame must be processed.

## 5. Continuous video vs request suppression

Do not confuse adaptive SEND/SKIP with the frame transform itself.

For camera -> video/encoder paths:

```python
emit_policy="every_frame"
```

For model/API/event-driven paths that may suppress redundant inputs:

```python
emit_policy="when_send"
```

`when_send` returns `None` for scheduler-suppressed frames.

## 6. Preserve host metadata

Use `FramePacket` when the source has metadata that must survive the transform:

```python
from foveastream import FramePacket

packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp_s,
    metadata={
        "camera_id": camera_id,
        "frame_id": frame_id,
        "rtp_timestamp": rtp_timestamp,
    },
)
optimized = optimizer.process(packet)
```

The middleware treats metadata as opaque and copies it to `OptimizedFrame.metadata`.

Do not add provider-specific metadata fields to FoveaStream core.

## 7. Pick the output actuator that matches the consumer

### Consumer expects one ordinary frame

Use:

```python
optimized.frame_rgb
```

or:

```python
optimized.result.foveated_frame
```

### Vision API accepts multiple images

Use:

```python
[optimized.result.context, *optimized.result.roi_views]
```

This reduces actual decoded/model pixels rather than only reducing full-frame entropy.

### Encoder supports ROI/block-QP metadata

Use:

```python
optimized.result.qp_map
```

Prefer direct spatial encoder controls over RGB reconstruction when the target encoder supports them.

### Event/request consumer may skip frames

Use `emit_policy="when_send"` or inspect:

```python
optimized.result.decision.send
```

## 8. ROI proposals

ROI does not mean a semantic object class. It means spatial support whose detail is valuable to the current task.

Use signals the host already has before adding new detectors.

Examples:

```text
AR app                -> user tap / ray / anchor
smart glasses         -> gaze / head direction / hand interaction
inspection app        -> checklist target / OCR / motion
robot                  -> planner target / detector / depth
screen agent           -> cursor / text / task state
unknown generic video -> built-in residual-motion fallback
```

Python proposal format:

```python
from foveastream import Roi, RoiProposal

proposal = RoiProposal(
    roi=Roi(x, y, w, h),
    confidence=.95,
    priority=2.0,
    source="host-task",
    track_hint=stable_id,
)
```

Multiple proposal sources may coexist. The tracker deduplicates overlaps, associates them over time, predicts them and enforces the hard total ROI-area budget.

## 9. Pull-style source

For an iterable source:

```python
from foveastream import transform_source

for optimized in transform_source(source, optimizer):
    downstream.send(optimized.frame_rgb)
```

`source` may yield either:

```text
FramePacket
```

or:

```text
(timestamp_s, frame_rgb)
```

A synchronous source -> transform -> sink runner is also available as `InlinePipeline`.

## 10. Native Rust

Use `StreamMiddleware`:

```rust
use foveastream::{
    EmitPolicy, FrameInput, InnovationSignals, StreamMiddleware,
};

let mut optimizer = StreamMiddleware::aggressive();
optimizer.set_emit_policy(EmitPolicy::EveryFrame);

let output = optimizer.process_rgb8(
    FrameInput { rgb8: frame, width, height, timestamp_s },
    &proposals,
    &points,
    InnovationSignals::default(),
)?;

if let Some(result) = output {
    downstream.send(&result.foveated_rgb8)?;
}
```

The native middleware is synchronous and causal. Host-specific threads/queues/sockets remain outside the core.

## 11. Production platform rule

The Python reference path currently uses RGB. Do not force production camera stacks through RGB forever.

Target architecture for high-performance adapters:

```text
native camera YUV/NV12 buffer
        -> native relevance/tracking
        -> direct quality/QP/ROI actuator where possible
        -> existing hardware encoder/model path
```

Future platform adapters should minimize copies and format conversions while preserving the same logical middleware contract.

## 12. Validation after integration

Measure:

- source FPS;
- processed FPS;
- dropped stale input count;
- queue depth;
- processing latency p50/p95;
- output FPS;
- bytes/s;
- decoded/model pixels/s;
- active ROI count / ROI area;
- downstream task quality vs full-frame baseline;
- end-to-end latency.

Do not validate only encoded file size.

## 13. Current boundary

Implemented:

- same-size drop-in frame transform;
- persistent multi-ROI tracking;
- automatic class-agnostic motion proposals;
- external relevance injection;
- hard ROI-area budgets;
- preserved timestamps/metadata;
- synchronous pull/inline path;
- bounded asynchronous latest-frame bridge;
- explicit continuous-video vs SEND/SKIP semantics;
- native Rust middleware equivalent.

Still adapter/performance work:

- CameraX/Camera2 direct adapter;
- AVFoundation/CVPixelBuffer adapter;
- OpenXR camera adapter;
- native YUV/NV12 path;
- zero-copy platform buffers;
- direct MediaCodec/NVENC/VideoToolbox/VAAPI QP/ROI wiring;
- concrete WebRTC/RTP/GStreamer adapters;
- stateful C ABI wrapper.

## 14. Extension rule

New relevance source -> emit `RoiProposal`.

New downstream target -> implement a sink/adapter.

New camera source -> produce `FramePacket` / native `FrameInput`.

New spatial actuator -> consume quality/ROI state.

Do not modify provider-agnostic core just to accommodate one external service.
