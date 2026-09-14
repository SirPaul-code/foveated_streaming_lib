# AGENTS.md — FoveaStream integration contract

This is the durable handoff for coding agents. Do not depend on chat history.

## Product boundary

FoveaStream is a provider-agnostic frame optimization/relevance middleware for machine vision.

The preferred integration shape is:

```text
existing frame source
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

The core must NOT depend on Gemini, OpenAI, WebRTC, GStreamer, CameraX, AVFoundation, Meta APIs, one detector class, one camera vendor, or one vision model.

## Read first

1. `README.md`
2. `docs/FRAME_MIDDLEWARE.md`
3. `docs/AGENT_INTEGRATION.md`
4. `docs/STATUS.md`
5. `docs/REALTIME_STREAMING_STATUS.md`
6. `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`

## Install / validate

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
cargo test --all-targets
cargo build --release
```

## Primary Python integration

For an existing synchronous frame loop:

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="every_frame",
)

frame = camera.read()
optimized = optimizer.transform(frame, timestamp_s)
downstream.send(optimized.frame_rgb)
```

`optimized.frame_rgb` is the same width/height as the submitted frame. The complete runtime state is available as `optimized.result`.

For callback-driven capture:

```python
from foveastream import CallbackFrameSink, RealtimeBridge

sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)

camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

The latest-frame queue is deliberate: realtime camera pipelines must not accumulate stale frames and grow latency when capture FPS exceeds processing FPS.

## Emit-policy invariant

`every_frame` is the default and is the safe mode for continuous camera/video/encoder paths.

`when_send` is opt-in for request/event pipelines where redundant frames may be suppressed:

```python
optimizer = FoveaStreamTransform(emit_policy="when_send")
```

Do not silently apply SEND/SKIP to a transport that requires continuous cadence.

## `FramePacket` metadata

`FramePacket.metadata` is opaque and must be preserved through the middleware. Source adapters may store camera IDs, frame IDs, RTP timestamps, tracing IDs or platform handles there.

Do not teach FoveaStream core about provider-specific metadata.

## External ROI evidence

An ROI is NOT a semantic class. It means only: spatial support whose loss of detail would disproportionately harm the current downstream task.

Any subsystem may emit `RoiProposal`:

- explicit user tap/rectangle/gaze;
- residual motion;
- saliency;
- detector or segmenter output;
- hand/object interaction;
- depth/autofocus;
- OCR/text proposal;
- task-specific logic;
- downstream model feedback;
- remote operator/controller;
- future learned relevance model.

Example:

```python
from foveastream import FramePacket, Roi, RoiProposal

packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp_s,
    metadata={"camera": "left"},
    proposals=(
        RoiProposal(Roi(.10, .20, .18, .16), confidence=.95, priority=2.0, source="task"),
        RoiProposal(Roi(.65, .55, .20, .22), confidence=.80, priority=1.0, source="detector"),
    ),
)
optimized = optimizer.process(packet)
```

Multiple proposal sources may overlap. `MultiRoiTracker` deduplicates, associates, predicts and budgets them. Do not replace this abstraction with hardcoded face/person/car recognition in core.

## Native Rust integration

Use `StreamMiddleware` as the drop-in native wrapper:

```rust
use foveastream::{EmitPolicy, FrameInput, InnovationSignals, StreamMiddleware};

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

The native core remains synchronous. Platform capture threads, bounded queues, backpressure, encoder threads and sockets belong to the host application/adapters.

## Output representations

The attached `ProcessResult` intentionally exposes multiple actuators:

- same-size `foveated_frame` / `foveated_rgb8` for ordinary frame pipelines;
- `context + roi_views` for multi-image VLM/API paths;
- `quality_map` for custom spatial fidelity control;
- `qp_map` for encoders exposing block/ROI QP controls;
- `decision` for optional SEND/SKIP request suppression;
- `rois/tracks` for custom tiling, metadata, overlays and model prompting.

Do not force every consumer through the same actuator.

## Multi-rate rule

Never assume capture FPS == optimizer FPS == output/model FPS.

Example:

```text
camera:      60 FPS
optimizer:   30-60 FPS depending on device/path
IMU:        200 Hz
model:        1-10 requests/s or event-driven
```

For asynchronous camera capture, keep queues bounded. Prefer newest-state processing to unbounded backlog for latency-sensitive perception.

## Performance rule

Python/OpenCV is the reference, benchmark and integration layer. Production hot paths should progressively use:

- Rust/native core;
- YUV/NV12 instead of RGB round trips;
- coarse encoder-block quality fields;
- zero/low-copy platform buffers;
- hardware encoder ROI/QP controls where available.

Do not claim zero-copy/hardware encoder support until an actual platform adapter is implemented and measured.

## Demo / regression

```bash
python examples/live_webcam.py --preset aggressive
python examples/custom_sink_adapter.py
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive
pytest -q
cargo test --all-targets
cargo build --release
```

## Engineering invariants

- Source and sink stay replaceable.
- Preserve source timestamps and metadata.
- Use monotonic timestamps.
- Preserve temporal state; do not instantiate the runtime per frame.
- Zero/one/many ROIs are all valid.
- ROI pixel budget is a hard cap.
- Uncertainty spends bandwidth instead of silently losing the target.
- `EveryFrame` is the safe default for continuous video.
- `WhenSend` is explicit and opt-in.
- Realtime queues are bounded; no unbounded frame backlog.
- Keep provider auth/network/model SDKs outside core.
- Benchmark downstream task quality, not only visual appearance/bytes.

## Before handing work to another agent

Update `docs/STATUS.md` with:

- exact branch/commit;
- what is actually implemented;
- tests/CI status;
- known limitations;
- precise next engineering steps.

Never mark untested platform support as complete.
