# FoveaStream

**Drop-in predictive foveation and relevance middleware for camera-to-AI/video pipelines.**

FoveaStream is designed to sit **between an existing frame source and an existing consumer**.

You keep your camera stack, transport, encoder and model client. FoveaStream receives frames, maintains task-relevant spatial state, reduces detail where it matters less, and returns either an ordinary same-size optimized frame or lower-level spatial outputs.

```text
existing source
camera / decoder / RTSP / WebRTC / app-owned frames
        |
        v
   FramePacket
        |
        v
FoveaStreamTransform
 multi-ROI tracking
 prediction / uncertainty
 hard spatial budget
 quality / QP policy
        |
        v
  OptimizedFrame
        |
        +--> ordinary same-size frame
        +--> context + N ROI views
        +--> quality map
        +--> encoder QP map
        +--> SEND/SKIP recommendation
        |
        v
existing consumer
encoder / WebRTC / model / queue / recorder / callback
```

FoveaStream is **not** tied to Gemini, OpenAI, WebRTC, CameraX, AVFoundation, one detector class or one model provider.

## Five-line integration

Before:

```python
frame = camera.read()
downstream.send(frame)
```

After:

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(preset="aggressive")
frame = camera.read()
optimized = optimizer.transform(frame)
downstream.send(optimized.frame_rgb)
```

`optimized.frame_rgb` has the same width/height as the input frame, so an ordinary image/video consumer can treat it as a replacement frame.

The complete spatial/runtime state is attached as `optimized.result`.

## Callback-driven realtime source

For a camera callback, do not build an unbounded queue if capture can outrun processing.

```python
from foveastream import CallbackFrameSink, FoveaStreamTransform, RealtimeBridge

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="every_frame",
)

sink = CallbackFrameSink(
    lambda packet: downstream.send(packet.frame_rgb)
)

bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)


def on_camera_frame(frame_rgb, timestamp_s):
    bridge.submit(frame_rgb, timestamp_s)
```

With `drop_policy="latest"`, the currently-processing frame is allowed to finish, but stale pending frames are replaced by the newest camera state. That prevents a `60 FPS source -> 30 FPS processor` mismatch from turning into seconds of queued latency.

Use `drop_policy="block"` only when every input frame must be processed and source backpressure is acceptable.

## Continuous video vs SEND/SKIP

These are deliberately separate concepts.

### Continuous camera/video/encoder path

Default:

```python
emit_policy="every_frame"
```

One optimized output is produced for every submitted input frame.

### Model/API/event-driven path

Optional:

```python
emit_policy="when_send"
```

Redundant frames may return `None` according to the adaptive scheduler.

Do **not** silently use request-style SEND/SKIP semantics on a video track that expects continuous cadence.

## Preserve source metadata

For real integrations, frame metadata often matters as much as pixels.

```python
from foveastream import FramePacket

packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp_s,
    metadata={
        "camera_id": "left",
        "frame_id": 1842,
        "rtp_timestamp": 9182374,
    },
)

optimized = optimizer.process(packet)
```

The middleware treats metadata as opaque and carries it to `optimized.metadata` unchanged.

## Measured result: aggressive preset

Two real ~60 FPS H.264 phone videos were processed at ~30 FPS. Baseline and foveated outputs were encoded with identical settings: `libx264`, `veryfast`, CRF 23, `yuv420p`.

| Video | Same-encoder baseline | Aggressive foveated | H.264 bytes saved | Context + ROI pixels saved | Reference processing | Active ROIs |
|---|---:|---:|---:|---:|---:|---:|
| `example1.mp4` | 824.3 kB | **236.6 kB** | **71.30%** | **96.26%** | 20.16 ms/frame (~49.6 FPS) | 1.13 mean / 3 max |
| `example2.mp4` | 3.720 MB | **2.554 MB** | **31.34%** | **84.08%** | 29.28 ms/frame (~34.2 FPS) | 3.30 mean / 6 max |

Across both clips together, aggressive outputs were **2.790 MB vs 4.544 MB same-encoder baseline**, a **38.59% byte reduction**.

The reference timings above were measured on an AMD EPYC 9V74 host using Python 3.13.5, OpenCV 4.13.0 and FFmpeg 7.1.5. They are not mobile-device latency claims.

Machine-readable benchmark data: [`docs/assets/real_demo/benchmark_presets_2026-09-14.json`](docs/assets/real_demo/benchmark_presets_2026-09-14.json).

## Real video visualization

The existing GIFs show the basic BEFORE -> optimized-payload idea. The current runtime supports zero, one or many simultaneous ROIs; these GIF assets are intentionally kept unchanged.

<p align="center">
  <img src="docs/assets/real_demo/example1.gif" width="100%" alt="Foveated streaming demo 1">
</p>

<p align="center">
  <img src="docs/assets/real_demo/example2.gif" width="100%" alt="Foveated streaming demo 2">
</p>
 

## Presets

| Preset | Peripheral source | Context scale | Max ROIs | Hard ROI-area budget | Peripheral quality floor |
|---|---:|---:|---:|---:|---:|
| `balanced` | 1/8 per axis | 0.25 | 8 | 30% | `0.04 + uncertainty` |
| `aggressive` | **1/16 per axis** | **0.15** | **6** | **22%** | `0.01 + uncertainty` |
| `extreme` | 1/24 per axis | 0.10 | 4 | 15% | `0.00 + uncertainty` |

`context_scale=0.15` means 15% width × 15% height, so the global context itself is only **2.25% of full-frame pixels** before ROI crops are added.

The ROI-area budget is a hard cap. Oversized predicted ROIs are center-preserving/aspect-preserving shrunk to fit the remaining budget.

## Full preset benchmark

### `example1.mp4`

| Preset | Foveated size | H.264 saving vs same encoder | Context + ROI pixel saving | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 293.1 kB | **64.45%** | **92.26%** | 1.49% | 6.25% |
| aggressive | **236.6 kB** | **71.30%** | **96.26%** | 1.49% | 6.25% |
| extreme | 218.6 kB | **73.48%** | **97.50%** | 1.49% | 6.25% |

### `example2.mp4`

| Preset | Foveated size | H.264 saving vs same encoder | Context + ROI pixel saving | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 2.847 MB | **23.46%** | **75.54%** | 18.19% | **30.00% cap** |
| aggressive | **2.554 MB** | **31.34%** | **84.08%** | 13.66% | **22.00% cap** |
| extreme | 2.138 MB | **42.53%** | **89.61%** | 9.38% | **15.00% cap** |

### Why compare to a re-encoded baseline?

The source video may have been encoded by a phone with unrelated codec settings. The primary byte metric therefore compares identical decoded frames under identical encoder settings:

```text
same decoded frames
     |
     +--> ordinary libx264 CRF23 ---------- baseline bytes
     |
     +--> FoveaStream --> libx264 CRF23 --- optimized bytes
```

The context+ROI pixel metric is a different actuator: it estimates actual image pixels when a consumer accepts low-resolution context plus separate high-resolution ROIs.

Neither byte saving nor pixel saving alone proves downstream task accuracy.

## ROI is relevance, not an object class

FoveaStream deliberately does not define ROI as a face, car, person or any fixed semantic category.

An ROI means:

> Spatial support whose loss of detail would disproportionately harm the current downstream task.

Possible evidence sources include:

- residual motion;
- user tap/rectangle;
- eye gaze or software gaze;
- AR anchors;
- hand/object interaction;
- OCR/text regions;
- saliency;
- depth/autofocus;
- detector/segmenter output;
- task-specific rules;
- downstream model feedback;
- remote operator input.

The built-in generic fallback is class-agnostic residual motion:

```text
frame t-1 + frame t
        |
        v
sparse KLT feature tracking
        |
        v
estimate global camera motion
        |
        v
warp previous frame
        |
        v
residual temporal motion
        |
        v
robust threshold + connected components
        |
        +--> proposal 1
        +--> proposal 2
        +--> proposal N
```

Those proposals enter `MultiRoiTracker`, which deduplicates, associates, predicts, expands uncertain support, ranks tracks and enforces the hard spatial budget.

Motion is only a fallback proposal source. A static but task-critical defect cannot be inferred from motion alone, so applications should inject stronger task evidence whenever available.

## Inject external ROI evidence

```python
from foveastream import FramePacket, Roi, RoiProposal

packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp_s,
    proposals=(
        RoiProposal(
            Roi(.10, .20, .18, .16),
            confidence=.95,
            priority=2.0,
            source="task-target",
        ),
        RoiProposal(
            Roi(.65, .55, .20, .22),
            confidence=.80,
            priority=1.0,
            source="external-model",
        ),
    ),
)

optimized = optimizer.process(packet)
```

Automatic and external proposal sources can coexist.

## Pull-style integration

Any iterable source may yield either `FramePacket` or `(timestamp_s, frame_rgb)`:

```python
from foveastream import FoveaStreamTransform, transform_source

optimizer = FoveaStreamTransform(preset="aggressive")

for optimized in transform_source(source, optimizer):
    downstream.send(optimized.frame_rgb)
```

Or run a synchronous source -> transform -> sink pipeline:

```python
from foveastream import CallbackFrameSink, InlinePipeline

pipeline = InlinePipeline(
    optimizer,
    CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb)),
)

stats = pipeline.run(source)
```

## Advanced outputs

`OptimizedFrame.result` exposes:

| Output | Best fit | Primary benefit |
|---|---|---|
| `foveated_frame` | ordinary image/video path | lower spatial entropy / encoded bytes |
| `context + roi_views` | multi-image VLM/API | fewer actual model pixels |
| `quality_map` | custom spatial pipeline | continuous relevance field |
| `qp_map` | ROI/QP-capable encoder | allocate bits without RGB strategy |
| `decision` | model/API/event path | suppress whole requests/frames |
| `rois/tracks` | custom tiling/metadata | application-specific spatial state |

A same-size frame remains the same number of decoded pixels even if its periphery is low detail. That is why context+ROI and direct QP paths are separate actuators.

## Native Rust middleware

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
    downstream.send(&result.foveated_rgb8)?;
}
```

The Rust middleware is synchronous and causal. Platform-specific camera threads, bounded queues, encoder threads and sockets stay in the host adapter rather than core.

## Live camera example

```powershell
python examples\live_webcam.py --preset aggressive
```

The example now uses the same drop-in `FoveaStreamTransform` API intended for real host integrations.

For a callback/async bridge example:

```powershell
python examples\custom_sink_adapter.py
```

## Install

### Windows

```powershell
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Manual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### Linux / macOS

```bash
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
```

FFmpeg with `libx264` is required only for the MP4 encoding benchmark, not for the middleware contract itself.

## Reproduce the real-video benchmark

```powershell
python bench\real_video_visualization.py example1.mp4 example2.mp4 `
  --outdir output\aggressive `
  --preset aggressive
```

All presets:

```powershell
'balanced','aggressive','extreme' | ForEach-Object {
  python bench\real_video_visualization.py example1.mp4 example2.mp4 `
    --outdir "output\$_" `
    --preset $_
}
```

## Realtime semantics

The processing path is causal: frame `N` does not require future frame `N+1`.

Capture FPS, optimizer FPS and downstream/model FPS may be different.

Example:

```text
camera source:          60 FPS
bounded pending queue:   1 frame
optimizer:              30-60 FPS depending on device/path
model requests:          1-10 FPS or event-driven
```

The Python/OpenCV path is the reference/integration implementation. Production hot paths should progressively move toward:

- native Rust;
- NV12/YUV input instead of RGB round trips;
- zero/low-copy platform buffers;
- direct hardware encoder ROI/QP controls;
- platform-specific acceleration only where measured beneficial.

## Current readiness

Implemented:

- causal frame-by-frame runtime;
- same-size drop-in optimized frame;
- persistent multi-ROI tracking;
- class-agnostic automatic motion proposals;
- external ROI evidence injection;
- hard total ROI-area budgeting;
- preserved timestamps and opaque source metadata;
- inline pull/synchronous pipeline;
- bounded callback-driven latest-frame bridge;
- explicit continuous-video vs SEND/SKIP semantics;
- native Rust middleware equivalent;
- context+ROIs, quality map and QP map outputs;
- Linux/Windows/macOS Rust CI and Python CI.

Not yet universally production-complete:

- native YUV/NV12 hot path;
- CameraX/Camera2 direct adapter;
- AVFoundation/CVPixelBuffer direct adapter;
- OpenXR/Meta direct adapter;
- zero-copy AHardwareBuffer / IOSurface / DMA-BUF;
- direct MediaCodec/NVENC/VideoToolbox/VAAPI ROI/QP wiring;
- concrete WebRTC/RTP/GStreamer adapters;
- stateful C ABI wrapper for `StreamMiddleware`;
- target-device p50/p95/energy benchmark matrix;
- broad downstream task-accuracy validation.

## Repository layout

```text
src/
  middleware.rs           native drop-in StreamMiddleware
  streaming.rs            native StreamRuntime / ProcessResult
  roi.rs                  multi-ROI tracker and hard spatial budget
  predictive.rs           predictive attention primitives
  scheduler.rs            SEND/SKIP controller

python/foveastream/
  middleware.py           FramePacket / Transform / RealtimeBridge
  streaming.py            reference runtime / multi-ROI / motion proposals

examples/
  live_webcam.py          inline camera -> transform -> preview
  custom_sink_adapter.py  callback camera -> bounded bridge -> custom sink

bench/
  real_video_visualization.py

docs/
  FRAME_MIDDLEWARE.md
  AGENT_INTEGRATION.md
  REALTIME_STREAMING_STATUS.md
  PREDICTIVE_ATTENTION_ARCHITECTURE.md
  STATUS.md

AGENTS.md                  durable coding-agent contract
```

## For coding agents

Start with [`AGENTS.md`](AGENTS.md), then [`docs/FRAME_MIDDLEWARE.md`](docs/FRAME_MIDDLEWARE.md).

The integration rule is simple:

> New source -> produce a frame packet. New relevance source -> produce ROI evidence. New destination -> implement a sink. Do not couple provider-specific code into the core.

## License

See [`LICENSE`](LICENSE) for the current repository license. FoveaStream contains no license server, telemetry requirement, paywall or runtime billing mechanism.
