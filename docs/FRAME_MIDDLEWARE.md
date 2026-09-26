# Drop-in frame middleware

This is the preferred integration boundary for FoveaStream.

The application should be able to insert FoveaStream between an existing frame producer and an existing consumer without coupling either side to a model provider, network protocol, camera API, or encoder.

```text
existing source
(camera / decoder / RTSP / WebRTC / app frames)
        |
        v
FramePacket
        |
        v
FoveaStreamTransform
        |
        v
OptimizedFrame
        |
        v
existing consumer
(encoder / WebRTC / model / queue / file / callback)
```

## Contract

### Input: `FramePacket`

- `frame_rgb`: HxWx3 `uint8` RGB reference frame;
- `timestamp_s`: monotonic source timestamp;
- `metadata`: opaque application metadata carried through unchanged;
- optional external `RoiProposal` values;
- optional point evidence;
- optional scheduler innovation signals.

The middleware does not interpret transport-specific metadata. A caller may put frame IDs, camera IDs, RTP timestamps, trace IDs, intrinsics handles or other application state in `metadata`.

### Output: `OptimizedFrame`

- `frame_rgb`: same-size optimized RGB frame suitable as a drop-in replacement for the input frame;
- `timestamp_s`: preserved source timestamp;
- `metadata`: preserved application metadata;
- `result`: complete `ProcessResult` containing ROI state, context/ROI views, quality map, QP map and scheduler decision.

This gives simple consumers a normal frame while advanced consumers can use lower-level FoveaStream outputs.

## Simplest inline integration

Before:

```python
frame = camera.read()
downstream.send(frame)
```

After:

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
)

frame = camera.read()
optimized = optimizer.transform(frame)
downstream.send(optimized.frame_rgb)
```

The default `emit_policy="every_frame"` is intentional. A continuous video pipeline generally needs one output for every submitted input frame.

## Model/request pipeline with SEND/SKIP

A model API or event-driven consumer may prefer to suppress redundant frames:

```python
optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="when_send",
)

optimized = optimizer.transform(frame, timestamp_s)
if optimized is not None:
    model.send(optimized.frame_rgb)
```

Do not enable `when_send` blindly for a codec/video track that expects continuous frame cadence.

## Callback-driven camera integration

A camera callback must not accumulate an unbounded queue when capture outruns optimization.

Use `RealtimeBridge`:

```python
from foveastream import CallbackFrameSink, FoveaStreamTransform, RealtimeBridge

optimizer = FoveaStreamTransform(preset="aggressive")
sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)


def on_camera_frame(frame_rgb, timestamp_s):
    bridge.submit(frame_rgb, timestamp_s)
```

`drop_policy="latest"` means:

1. the frame already being processed is allowed to finish;
2. the pending queue stays bounded;
3. when the pending queue is full, the oldest queued frame is replaced by the newest one;
4. processing therefore follows the freshest available camera state instead of building latency.

This is the recommended policy for realtime perception/video optimization when source FPS can exceed processing FPS.

Use `drop_policy="block"` only when every submitted frame must be processed and it is acceptable to apply backpressure to the source thread.

## Pull-style source

Any iterable yielding either `FramePacket` or `(timestamp_s, frame_rgb)` can be wrapped:

```python
from foveastream import FoveaStreamTransform, transform_source

optimizer = FoveaStreamTransform(preset="aggressive")

for packet in transform_source(my_source, optimizer):
    downstream.send(packet.frame_rgb)
```

For a synchronous source -> transform -> sink pipeline with counters:

```python
from foveastream import CallbackFrameSink, InlinePipeline

pipeline = InlinePipeline(
    optimizer,
    CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb)),
)
stats = pipeline.run(my_source)
```

## External relevance evidence

The source adapter may inject task-specific ROIs without modifying the middleware:

```python
from foveastream import FramePacket, Roi, RoiProposal

packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp,
    metadata={"camera": "left"},
    proposals=(
        RoiProposal(
            Roi(.25, .30, .20, .15),
            confidence=.95,
            priority=2.0,
            source="task-detector",
        ),
    ),
)

optimized = optimizer.process(packet)
```

Automatic class-agnostic motion proposals and external proposals can coexist.

## Output choices

`OptimizedFrame.frame_rgb` is the easiest drop-in path, but the attached `result` also exposes:

```text
result.foveated_frame   same-size optimized frame
result.context          low-resolution global context
result.roi_views        zero or more high-resolution ROI images
result.quality_map      continuous relevance/fidelity field
result.qp_map           encoder block delta-QP policy
result.rois             active spatial metadata
result.decision         SEND/SKIP recommendation
```

An encoder integration should prefer direct QP/ROI controls where supported instead of reconstructing RGB merely because the reference middleware exposes a frame.

## Native Rust

The native equivalent is `StreamMiddleware`:

```rust
use foveastream::{
    EmitPolicy, FrameInput, InnovationSignals, StreamMiddleware,
};

let mut optimizer = StreamMiddleware::aggressive();
optimizer.set_emit_policy(EmitPolicy::EveryFrame);

let output = optimizer.process_rgb8(
    FrameInput {
        rgb8: frame,
        width,
        height,
        timestamp_s,
    },
    &proposals,
    &points,
    InnovationSignals::default(),
)?;

if let Some(result) = output {
    encoder.send(&result.foveated_rgb8)?;
}
```

The Rust middleware deliberately does not own camera threads, queues or sockets. Production applications should keep platform-specific capture/backpressure/transport outside the core and use FoveaStream as a synchronous transform.

## Realtime rules

- Use monotonic timestamps.
- Preserve temporal state; do not instantiate a new transform for every frame.
- Prefer a queue depth of 1 for latency-sensitive camera callbacks.
- Do not use unbounded queues.
- `EveryFrame` is the safe default for continuous video.
- `WhenSend` is intended for request/event suppression.
- Capture FPS, optimizer FPS and downstream/model FPS do not need to be equal.
- The Python path is a reference/integration implementation; production device hot paths should move to native YUV/NV12 and direct encoder controls where possible.

## Adapter implementation checklist

A new platform/source adapter normally needs only to:

1. expose a monotonic frame timestamp;
2. provide the frame in a supported pixel format or convert at the adapter boundary;
3. call the persistent FoveaStream transform instance;
4. forward the optimized frame/result to the existing consumer;
5. use bounded latest-frame buffering if capture is asynchronous;
6. preserve source metadata needed by downstream code.

Do not add provider credentials, network retry logic, model SDKs or camera-specific APIs to FoveaStream core.
