# AGENTS.md — FoveaStream integration contract

This file is the durable handoff for coding agents integrating FoveaStream into another application or transport. Do not depend on chat history.

## Product goal

FoveaStream is a transport/provider-agnostic predictive foveation runtime for machine vision. It reduces bytes, pixels, visual tokens and edge compute while preserving task-relevant spatial detail.

The core contract is:

```text
arbitrary frame source + arbitrary relevance evidence
        -> persistent multi-ROI state
        -> quality field + payload alternatives + send/skip decision
        -> arbitrary consumer/transport/model
```

Do NOT make the core depend on Gemini, OpenAI, WebRTC, GStreamer, CameraX, AVFoundation, Meta APIs, a particular detector class, or a particular vision model.

## Start here

Read, in order:

1. `README.md`
2. `docs/AGENT_INTEGRATION.md`
3. `docs/STATUS.md`
4. `docs/REALTIME_STREAMING_STATUS.md`
5. `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`

Install the Python reference/integration layer:

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Native validation:

```bash
cargo test --all-targets
cargo build --release
pytest -q
```

## Python integration: minimum path

```python
from foveastream import FoveaStreamRuntime, StreamRuntimeConfig, CallbackSink

runtime = FoveaStreamRuntime(StreamRuntimeConfig(auto_motion_proposals=True))

# frame_rgb: uint8 HxWx3 RGB
result = runtime.process(frame_rgb, timestamp_s)

if result.decision.send:
    # Choose whichever representation your downstream consumer supports.
    full_frame = result.foveated_frame
    context = result.context
    roi_images = result.roi_views
    qp_map = result.qp_map
```

For a push/callback workflow:

```python
sink = CallbackSink(lambda result: my_transport(result), only_when_send=True)
runtime.push(frame_rgb, timestamp_s, sink)
```

The sink owns provider/transport-specific encoding and I/O. Do not add provider credentials or provider SDKs to FoveaStream core.

## External ROI evidence

An ROI is NOT a semantic class. It means only: "this spatial support is currently more valuable to the downstream task".

Any subsystem may emit `RoiProposal`:

- explicit user tap/rectangle/gaze;
- residual motion;
- saliency;
- detector or segmenter output;
- hand-object interaction;
- depth/autofocus;
- OCR/text proposal;
- task-specific rules;
- downstream model feedback;
- a remote controller;
- a future learned relevance model.

Example:

```python
from foveastream import Roi, RoiProposal

proposals = [
    RoiProposal(Roi(.10, .20, .18, .16), confidence=.95, priority=2.0, source="task-A"),
    RoiProposal(Roi(.65, .55, .20, .22), confidence=.80, priority=1.0, source="external-detector"),
]
result = runtime.process(frame_rgb, timestamp_s, proposals=proposals)
```

Multiple proposal sources may overlap. `MultiRoiTracker` deduplicates, associates and temporally propagates them, maintains independent track IDs, expands stale/uncertain support, and selects active ROIs under `pixel_budget_fraction`.

Do not replace this abstraction with hardcoded face/person/car recognition in the core.

## Rust/native integration

Use:

- `RoiProposal`
- `MultiRoiTracker`
- `StreamRuntime`
- `FrameInput`
- `ProcessResult`
- `StreamSink`

Minimal shape:

```rust
let mut runtime = StreamRuntime::new(StreamRuntimeConfig::default());
let result = runtime.process_rgb8(
    FrameInput { rgb8: frame, width, height, timestamp_s },
    &proposals,
    &points,
    signals,
)?;

if result.should_send() {
    transport.send(&result)?;
}
```

The native runtime is synchronous and causal. For production applications, the host should own capture threads, bounded queues, backpressure and transport threads rather than hiding platform-specific threading inside the core.

## Output representations

`ProcessResult` intentionally exposes multiple actuators because different consumers benefit from different payloads:

- `foveated_frame` / `foveated_rgb8`: same-size image suitable for ordinary image/video encoders;
- `context + roi_views`: low-resolution global context plus one or more high-resolution ROIs for image/VLM APIs;
- `quality_map`: continuous spatial relevance/fidelity map;
- `qp_map`: encoder block delta-QP policy;
- `decision`: SEND/SKIP scheduling decision;
- `rois/tracks`: metadata for custom transports, overlays, tiling, logging or model prompts.

Do not force all consumers through the same representation.

## Streaming adapter rule

A downstream adapter should be thin:

```text
FoveaStream ProcessResult
      -> adapter-specific serialization/encode
      -> WebSocket / HTTP / WebRTC / RTP / gRPC / queue / local inference / shared memory
```

Provider-specific frame-rate limits, authentication, MIME types, connection lifetime and retry behavior belong in that adapter, not in the FoveaStream runtime.

## Multi-rate rule

Never assume capture FPS == analysis FPS == output/model FPS.

Example:

```text
camera:      60 FPS
local ROI:   60 FPS
IMU:        200 Hz
output:       5 FPS or event-driven
model:        1-5 requests/s
```

The local state may update on every frame while the scheduler emits only selected frames.

## Performance rule

Python/OpenCV is the reference, benchmark and integration layer. Production hot paths should prefer the Rust/native core, YUV/NV12 buffers, coarse encoder-block quality fields, zero/low-copy platform buffers and hardware encoder ROI/QP controls where available.

Do not claim production zero-copy or hardware-encoder integration unless the actual platform adapter exists and is benchmarked.

## Demo / regression commands

Single/multi-ROI real-video demo:

```bash
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive
```

More ROI capacity:

```bash
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive --max-rois 10 --roi-budget-fraction 0.30
```

The generated `*_visualization.mp4` must show every active ROI and the foveated result.

## Engineering invariants

- Preserve global context unless the caller explicitly selects a crop-only mode.
- An ROI set may contain zero, one or many regions.
- Uncertainty must spend bandwidth rather than silently crop away a target.
- Keep proposal generation replaceable.
- Keep transport/provider code outside the core.
- Use monotonic timestamps.
- Avoid independent stateless per-frame crops; preserve temporal state.
- Prefer zero bytes (skip) over compressed bytes when the frame is redundant.
- Benchmark downstream task quality, not only visual appearance or byte savings.

## Before handing work to another agent

Update `docs/STATUS.md` with:

- exact branch/commit;
- what is actually implemented;
- tests/CI status;
- known limitations;
- next concrete engineering steps.

Never mark untested platform support as complete.
