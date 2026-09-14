# FoveaStream

**Provider-agnostic predictive foveation and relevance transport for machine vision.**

FoveaStream sits between a camera/frame source and a vision consumer. It continuously decides **where detail matters, how much detail to preserve, and whether a frame needs to be sent at all**.

It is not tied to Gemini, OpenAI, WebRTC, a particular camera API, a particular detector class, or even a particular output representation.

```text
camera / video / glasses / robot / RTSP / app-owned frames
                         +
        arbitrary relevance evidence (optional)
                         |
                         v
                FoveaStream runtime
          multi-ROI tracking + prediction
          uncertainty + pixel budget
          quality field + send/skip policy
                         |
         +---------------+----------------+
         |               |                |
         v               v                v
  foveated frame    context + N ROIs    QP map
         |               |                |
         +---------------+----------------+
                         |
                         v
       any model / encoder / transport / callback
```

The current implementation has:

- a native Rust core;
- a Python reference/integration runtime;
- persistent **multi-ROI** tracking;
- class-agnostic automatic motion proposals;
- external ROI injection from any application/model/sensor;
- `balanced`, `aggressive`, and `extreme` presets;
- same-size foveated frames;
- low-resolution global context + multiple high-resolution ROI views;
- per-block QP-delta maps;
- adaptive SEND/SKIP scheduling;
- a generic sink/callback integration contract;
- real-video benchmark and live webcam examples.

## Measured result: aggressive preset

Two real ~60 FPS H.264 phone videos were processed at ~30 FPS. Baseline and foveated outputs were re-encoded with **identical** settings: `libx264`, `veryfast`, CRF 23, `yuv420p`.

| Video | Same-encoder baseline | Aggressive foveated | H.264 bytes saved | Context + ROI pixels saved | Processing | Active ROIs |
|---|---:|---:|---:|---:|---:|---:|
| `example1.mp4` | 824.3 kB | **236.6 kB** | **71.30%** | **96.26%** | 20.16 ms/frame (~49.6 FPS) | 1.13 mean / 3 max |
| `example2.mp4` | 3.720 MB | **2.554 MB** | **31.34%** | **84.08%** | 29.28 ms/frame (~34.2 FPS) | 3.30 mean / 6 max |

Across both clips together, the aggressive same-encoder outputs were **2.790 MB vs 4.544 MB baseline**, a byte reduction of **38.59%**. The two videos are intentionally very different: the first usually contains one small relevant region; the second produces several simultaneous regions and therefore spends substantially more ROI budget.

The `aggressive` preset uses a **1/16 peripheral source scale**, 15%-per-axis global context, at most 6 active ROIs, and a **hard 22% total ROI-area budget**.

> `context_scale=0.15` means 15% width × 15% height, so the global context itself contains only **2.25% of full-frame pixels** before ROI crops are added.

Timing above is a reference measurement on an AMD EPYC 9V74 host using Python 3.13.5, OpenCV 4.13.0 and FFmpeg 7.1.5. It is **not** a mobile-device latency claim. Byte and pixel measurements are the primary reproducible results.

Machine-readable benchmark data, exact input SHA256 hashes, environment and all preset results: [`docs/assets/real_demo/benchmark_presets_2026-09-14.json`](docs/assets/real_demo/benchmark_presets_2026-09-14.json).

## Real video visualization

The existing animations below are kept as a visual explanation of the basic BEFORE → optimized-payload idea. The current runtime has advanced beyond these original single-focus previews: it can maintain **zero, one, or many simultaneous ROIs** and enforce an explicit total ROI budget.

<p align="center">
  <img src="docs/assets/real_demo/example1.gif" width="100%" alt="Foveated streaming demo 1">
</p>

<p align="center">
  <img src="docs/assets/real_demo/example2.gif" width="100%" alt="Foveated streaming demo 2">
</p>
 

The current MP4 benchmark visualization draws every active ROI and shows the final same-size foveated transport frame.

## Full preset benchmark

### `example1.mp4`

| Preset | Foveated size | H.264 bytes saved vs same encoder | Context + ROI pixels saved | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 293.1 kB | **64.45%** | **92.26%** | 1.49% | 6.25% |
| aggressive | **236.6 kB** | **71.30%** | **96.26%** | 1.49% | 6.25% |
| extreme | 218.6 kB | **73.48%** | **97.50%** | 1.49% | 6.25% |

### `example2.mp4`

| Preset | Foveated size | H.264 bytes saved vs same encoder | Context + ROI pixels saved | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 2.847 MB | **23.46%** | **75.54%** | 18.19% | **30.00% cap** |
| aggressive | **2.554 MB** | **31.34%** | **84.08%** | 13.66% | **22.00% cap** |
| extreme | 2.138 MB | **42.53%** | **89.61%** | 9.38% | **15.00% cap** |

The benchmark also exposed and fixed an important edge case: an oversized first predicted ROI could previously bypass `pixel_budget_fraction`. The tracker now shrinks an oversized highest-priority ROI around its center while preserving aspect ratio, so the configured ROI-area budget is a real hard cap in both Python and Rust.

### Why compare against a re-encoded baseline?

The original source MP4 may have been encoded by a phone using completely different codec settings. Comparing only `source.mp4` bytes to a CRF 23 output mixes **FoveaStream savings with encoder-setting differences**.

The primary codec metric is therefore:

```text
same decoded frames
     |
     +--> ordinary libx264 CRF 23 ---------- baseline bytes
     |
     +--> FoveaStream --> libx264 CRF 23 --- foveated bytes
```

`H.264 bytes saved` is the difference between those two outputs. Source-vs-foveated size is still recorded in the benchmark JSON, but it is secondary.

The **context + ROI pixel saving** is a different actuator: it estimates the actual decoded image-pixel budget when a model/API can receive a small global context plus separate ROI images instead of a full-resolution frame.

Neither metric by itself proves downstream model accuracy. The real product target is a Pareto curve of **task quality vs bytes/tokens/latency/energy**.

## How ROI selection works

FoveaStream deliberately does **not** define ROI as “a face”, “a person”, “a car”, or any other hardcoded semantic class.

An ROI means:

> Spatial support whose loss of detail would be disproportionately harmful to the current downstream task.

Anything can propose relevance:

- residual motion;
- user tap or rectangle;
- eye gaze;
- software gaze;
- AR anchor or tracked object;
- hand-object interaction;
- OCR/text region;
- saliency;
- depth/autofocus;
- detector/segmenter output;
- task-specific rules;
- downstream model feedback;
- remote operator input.

The built-in automatic fallback is intentionally cheap and class-agnostic:

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
robust median/MAD threshold
        |
        v
connected components
        |
        +--> proposal 1
        +--> proposal 2
        +--> proposal 3 ...
```

Those proposals enter `MultiRoiTracker`, which performs:

1. proposal deduplication;
2. association with persistent tracks using IoU / center distance / optional track hint;
3. rectangle velocity estimation;
4. temporal prediction;
5. confidence decay when evidence disappears;
6. uncertainty/motion expansion;
7. ranking by confidence × priority;
8. hard total ROI-area budgeting.

Motion is only one proposal source. A static but task-critical defect cannot be discovered from motion alone, so applications are expected to inject stronger task evidence when available.

## Presets

| Preset | Peripheral source | Context scale | Max ROIs | Hard ROI-area budget | Peripheral quality floor |
|---|---:|---:|---:|---:|---:|
| `balanced` | 1/8 per axis | 0.25 | 8 | 30% | `0.04 + uncertainty` |
| `aggressive` | **1/16 per axis** | **0.15** | **6** | **22%** | `0.01 + uncertainty` |
| `extreme` | 1/24 per axis | 0.10 | 4 | 15% | `0.00 + uncertainty` |

The presets are starting points, not fixed protocol modes. Every relevant value can be overridden.

## Install

### Windows

```powershell
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Or manually:

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

FFmpeg with `libx264` is required for the MP4 benchmark. `requirements.txt` installs the Python reference/live-video dependencies from the canonical `pyproject.toml` metadata.

## Reproduce the video benchmark

Aggressive:

```powershell
python bench\real_video_visualization.py example1.mp4 example2.mp4 `
  --outdir output\aggressive `
  --preset aggressive
```

Run all presets in PowerShell:

```powershell
'balanced','aggressive','extreme' | ForEach-Object {
  python bench\real_video_visualization.py example1.mp4 example2.mp4 `
    --outdir "output\$_" `
    --preset $_
}
```

Useful overrides:

```powershell
python bench\real_video_visualization.py example1.mp4 `
  --outdir output\custom `
  --preset aggressive `
  --max-rois 10 `
  --roi-budget-fraction 0.30 `
  --peripheral-downscale 20 `
  --context-scale 0.12 `
  --falloff-strength 5.2 `
  --quality-floor 0.005 `
  --uncertainty-floor 0.015
```

Each run produces:

- `*_baseline_crf23.mp4` — ordinary same-settings H.264 baseline;
- `*_foveated_crf23.mp4` — same-size foveated frame output;
- `*_visualization.mp4` — original / multi-ROI attention / foveated side-by-side;
- `*_benchmark.json` — bytes, model-pixel budget, processing time, throughput and ROI statistics;
- `benchmark_summary.json` — all videos from that invocation.

## Generic streaming integration

The streaming API is deliberately a **frame processor + result contract**, not a network protocol.

### Python

```python
from foveastream import FoveaStreamRuntime, StreamRuntimeConfig

runtime = FoveaStreamRuntime(
    StreamRuntimeConfig.preset(
        "aggressive",
        auto_motion_proposals=True,
    )
)

# frame_rgb: uint8 HxWx3 RGB
# timestamp_s: monotonic timestamp in seconds
result = runtime.process(frame_rgb, timestamp_s)

if result.decision.send:
    # Pick the representation your downstream path supports.
    result.foveated_frame   # same-size frame
    result.context          # low-resolution global context
    result.roi_views        # 0..N high-resolution ROI images
    result.quality_map      # continuous 0..1 quality field
    result.qp_map           # encoder block delta-QP map
    result.rois             # spatial metadata
```

### Push/callback sink

```python
from foveastream import CallbackSink

sink = CallbackSink(
    lambda result: my_transport.send(result),
    only_when_send=True,
)

runtime.push(frame_rgb, timestamp_s, sink)
```

`my_transport` may be a WebSocket client, WebRTC wrapper, gRPC stream, HTTP request, queue, shared-memory writer, local inference call, encoder, or something application-specific. Provider credentials and provider SDKs belong in that adapter, **not in FoveaStream core**.

A complete adapter is conceptually only:

```text
FoveaStream ProcessResult
       |
       v
choose payload representation
       |
       v
encode / serialize
       |
       v
existing transport or model client
```

See [`examples/custom_sink_adapter.py`](examples/custom_sink_adapter.py) and [`docs/AGENT_INTEGRATION.md`](docs/AGENT_INTEGRATION.md).

## Inject your own ROI evidence

Automatic residual-motion proposals can be disabled or combined with application evidence.

```python
from foveastream import Roi, RoiProposal

proposals = [
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
]

result = runtime.process(
    frame_rgb,
    timestamp_s,
    proposals=proposals,
)
```

Multiple sources can coexist. Overlapping proposals are deduplicated; independent regions remain independent tracks.

## Live camera example

```powershell
python examples\live_webcam.py --preset aggressive
```

This exercises an actual causal loop:

```text
camera frame
   -> automatic proposals
   -> persistent multi-ROI tracker
   -> foveation / views / metadata
   -> next frame
```

No future frames are required.

## Rust/native runtime

The native runtime exposes the same conceptual contract:

```rust
use foveastream::{
    FrameInput, InnovationSignals, RoiProposal,
    StreamRuntime, StreamRuntimeConfig,
};

let mut runtime = StreamRuntime::new(StreamRuntimeConfig::aggressive());

let result = runtime.process_rgb8(
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

if result.should_send() {
    transport.send(&result)?;
}
```

The native runtime is synchronous and causal. The host application should own capture threads, bounded queues, backpressure, encoder threads and network I/O.

The existing C ABI currently exposes lower-level foveation and QP-map primitives. A complete C ABI wrapper for the new stateful `StreamRuntime` is still future work.

## Output modes

FoveaStream separates **relevance policy** from **output actuator** because different consumers optimize different costs.

| Output | Best fit | What it reduces |
|---|---|---|
| `foveated_frame` | ordinary JPEG/video/WebRTC-style path | entropy / encoded bytes |
| `context + roi_views` | VLM/image APIs accepting multiple images | actual decoded pixels, often visual tokens |
| `quality_map` | custom image/model pipelines | continuous spatial fidelity policy |
| `qp_map` | encoders exposing ROI/QP controls | bitrate without requiring the same RGB strategy |
| `decision` | event-driven / redundant-frame suppression | complete frames or model requests |
| `rois/tracks` | custom tiling, metadata, overlays, model prompting | application-specific |

A same-size 1024×576 image is still 1024×576 pixels even if most of it is blurred. That is why the context+ROI path and encoder-QP path exist separately from same-size foveated RGB.

## Multi-rate operation

Capture FPS, local analysis FPS and downstream model FPS do not need to match.

For example:

```text
camera capture:        60 FPS
local ROI propagation: 60 FPS
IMU:                  200 Hz
network/model output:   1-10 FPS or event-driven
```

The local state can update continuously while the adaptive scheduler sends only frames that are sufficiently novel, uncertain, stale, or explicitly triggered.

## What is realtime-ready today?

Implemented:

- causal frame-by-frame processing;
- persistent multi-ROI state;
- prediction and uncertainty expansion;
- hard ROI pixel budgeting;
- provider-agnostic `ProcessResult`;
- generic sink/callback contract;
- Python live webcam path;
- native Rust runtime;
- same-size frame, context+ROIs and QP outputs;
- adaptive SEND/SKIP policy;
- Windows/Linux/macOS Rust CI and Python CI.

Not yet universally production-complete:

- CameraX/Camera2 direct camera adapter;
- AVFoundation/CVPixelBuffer adapter;
- OpenXR/Meta direct camera adapter;
- native NV12/YUV hot path;
- zero-copy AHardwareBuffer / IOSurface / DMA-BUF paths;
- direct MediaCodec/NVENC/VideoToolbox/VAAPI ROI/QP wiring;
- concrete WebRTC/RTP/GStreamer adapters;
- C ABI for the stateful streaming runtime;
- target-device p50/p95 latency and energy characterization;
- downstream task-accuracy benchmarks across production workloads.

So the current code is a **real provider-agnostic streaming preprocessing/runtime contract**, but it should not be described as a finished zero-copy transport stack for every platform.

## Performance direction

The Python/OpenCV implementation is the reference, demo and integration layer. Production hot paths should move toward:

- YUV/NV12-native processing;
- coarse quality fields matching encoder block/tile grids;
- zero/low-copy camera buffers;
- hardware encoder ROI/QP controls;
- reusable AR/VIO/IMU/depth products when already available;
- bounded queues and explicit overload/drop policy;
- native SIMD/GPU/NPU acceleration only where it beats transfer/dispatch overhead.

## Benchmark target beyond bytes

The real production benchmark should compare full resolution, global downscale, fixed crop, single ROI, multi-ROI, adaptive frame selection and predictive FoveaStream using:

- bytes/s;
- decoded pixels/s;
- visual tokens / billed model cost where measurable;
- requests/s;
- CPU/GPU/NPU time and energy;
- end-to-end latency p50/p95;
- important-region coverage / miss rate;
- reacquisition time;
- downstream task quality such as defect recall, OCR recall, VQA quality or detector accuracy.

The win condition is:

> **Lower bytes/tokens/latency/energy at equivalent downstream task quality and bounded important-region miss probability.**

## Repository layout

```text
src/
  roi.rs                  native multi-ROI tracker
  streaming.rs            native StreamRuntime / ProcessResult / StreamSink
  predictive.rs           predictive attention primitives
  scheduler.rs            adaptive SEND/SKIP controller
  quality.rs              quality and QP-map generation

python/foveastream/
  streaming.py            Python multi-ROI runtime and motion proposal source

bench/
  real_video_visualization.py

examples/
  live_webcam.py
  custom_sink_adapter.py

include/
  foveastream.h           current low-level C ABI

docs/
  AGENT_INTEGRATION.md
  REALTIME_STREAMING_STATUS.md
  PREDICTIVE_ATTENTION_ARCHITECTURE.md
  RESEARCH.md
  STATUS.md
  assets/real_demo/

AGENTS.md                  durable integration/handoff instructions for coding agents
requirements.txt
requirements-dev.txt
LICENSE
```

## For coding agents

Start with [`AGENTS.md`](AGENTS.md). It defines the integration boundary, multi-ROI semantics, provider separation, output representations, test commands and handoff requirements so an agent can integrate FoveaStream into another workflow without depending on chat history.

## Prior art / product boundary

Foveated rendering, encoder ROI/QP controls, saliency, gaze-driven transport and multi-region attention are not individually novel concepts.

The product thesis is the portable **predictive relevance control plane** that combines replaceable evidence sources, persistent uncertainty-aware multi-ROI state, explicit spatial budgets, multiple output actuators and adaptive transmission behind one transport-independent API.

See [`docs/RESEARCH.md`](docs/RESEARCH.md) and [`docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`](docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md).

## License

FoveaStream is distributed under the proprietary evaluation/non-commercial R&D license in [`LICENSE`](LICENSE).

Commercial production, paid services, customer deployments, OEM/device integration, commercial redistribution and commercial hosted/SaaS use require a separate written commercial license.

**Commercial licensing:** `p.duplinsky@gmail.com`
