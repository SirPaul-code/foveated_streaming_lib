# FoveaStream

**Predictive foveated transport for machine vision.**

FoveaStream is a cross-platform native SDK architecture for reducing the **bytes, pixels, visual tokens, latency and edge compute** required by camera-to-model pipelines while preserving the detail that matters to the downstream task.

It is designed to sit between **any camera/sensor stack** and **any vision transport/model stack**: Android/Samsung, Meta/OpenXR, iOS, Windows, Linux, robotics, smart glasses, body cameras or custom embedded hardware.

## Real video visualization

The animation below was generated from the two uploaded source videos using the actual automatic tracking/foveation pipeline. The attention region is **not manually drawn**.

![FoveaStream real video visualization](docs/assets/real_demo/foveastream_readme.gif)

`ORIGINAL` → automatic predicted attention field → `FOVEATED TRANSPORT`

The current visualizer estimates global camera motion, isolates residual scene motion, temporally propagates the attention state, builds a continuous quality field and applies spatially varying fidelity. Production device adapters can replace the final RGB transform with hardware QP/ROI maps or model-specific ROI/patch output.

## Measured results on the two uploaded videos

Both source videos were ~60 FPS H.264. For this reproducible CPU benchmark the pipeline sampled them at ~30 FPS. Baseline and foveated outputs were encoded with the same H.264 settings (`libx264`, CRF 23, same processed frame cadence).

| Video | Same-size H.264 bytes saved | Context + ROI model pixels saved | Preprocess time / processed frame |
|---|---:|---:|---:|
| `example1.mp4` | **58.44%** | **91.80%** | **16.14 ms** |
| `example2.mp4` | **40.44%** | **87.62%** | **15.88 ms** |

Raw benchmark output: [`docs/assets/real_demo/benchmark_summary.json`](docs/assets/real_demo/benchmark_summary.json)

These numbers measure two different actuators:

- **same-size H.264 saving** measures how much easier the foveated frame is to encode at the same codec settings;
- **context + ROI pixel saving** measures the actual image-pixel budget if a VLM receives a low-resolution global context plus full-resolution ROI instead of the complete frame.

They are not interchangeable, and neither is presented as downstream-task accuracy. The production benchmark target is a Pareto curve of **task quality vs bytes/tokens/latency/energy**.

## Core thesis

```text
manual ROI / task cue / gaze / software gaze / IMU / optical flow /
pose / depth / saliency / object & hand tracks / model feedback
                               |
                               v
              predictive relevance belief P(x,y,t+h)
                               |
                     uncertainty + latency model
                               |
                               v
                       quality field Q(x,y,t+h)
                               |
              +----------------+----------------+
              |                |                |
              v                v                v
       VLM context+ROI     encoder QP map   patch/token policy
              |                |                |
              +----------------+----------------+
                               |
                               v
                    adaptive send/skip scheduler
```

The quality map is the stable internal contract. Sensor sources and output backends are replaceable.

## Device-independent input model

The SDK must not depend on CameraX, AVFoundation, Meta APIs or OpenCV as its core abstraction. Platform stacks are adapters around timestamped generic evidence:

```text
Frame {
  timestamp_ns
  width, height, pixel_format
  plane pointers / strides / GPU handle
  camera_intrinsics
}

Evidence {
  timestamp_ns
  source_type
  point / rect / mask / pose / motion
  covariance
  confidence
  ttl
}
```

Possible adapters:

- Android: CameraX / Camera2 / AHardwareBuffer / MediaCodec / ARCore;
- Apple: CVPixelBuffer / IOSurface / Metal / AVFoundation / ARKit;
- Meta/OpenXR: OpenXR camera/pose/eye-gaze extensions where available;
- Windows/Linux: Media Foundation, DirectShow, GStreamer, V4L2, DMA-BUF, NVENC/VAAPI;
- robotics/embedded: raw YUV/RGB planes, shared memory, CUDA/Vulkan textures or application-owned buffers.

The runtime can expose `capture_fps`, `analysis_fps`, `output_fps`, latency horizon and compute budget as parameters rather than assuming one fixed cadence.

## Current native primitives

Rust core currently contains:

- spatial quality-field generation;
- same-size foveated RGB generation;
- context + high-resolution ROI views;
- encoder block QP-delta map generation;
- focus-source fusion and latency compensation;
- uncertainty-aware predictive attention filter;
- camera-ray gyro propagation;
- optical-flow velocity correction;
- fixation / pursuit / saccade / lost motion modes;
- future ROI generation with covariance dilation;
- adaptive send/skip scheduler with innovation, uncertainty and staleness gates;
- C ABI surface for native/mobile integration.

Python remains a reference, visualization and benchmark layer. The production performance target is native zero/low-copy execution.

## IMU is motion evidence, not gaze

Gyroscope/accelerometer data alone do not reveal where the eyes are looking. They are useful because they provide low-latency ego-motion evidence and can propagate an already selected world direction/ROI as the device moves.

Attention/relevance can instead be inferred from any weighted combination of:

- eye tracking;
- software-only egocentric gaze estimation;
- explicit UI point/rectangle;
- task-specific detector or prompt localizer;
- object/hand interaction;
- optical flow and persistent tracks;
- saliency;
- depth/autofocus;
- center prior;
- downstream model feedback.

The runtime treats each input as uncertain evidence rather than ground truth.

## Output modes

A blurred 1280×720 frame is still 1280×720, so peripheral degradation alone may not reduce visual-token cost. The attention policy is therefore separated from the output actuator.

| Output | Intended path | Main saving |
|---|---|---|
| Same-size spatial-fidelity frame | ordinary JPEG/WebRTC/video path | entropy / encoded bytes |
| Low-res global context + high-res ROI views | third-party VLM APIs | actual pixels and often visual tokens |
| QP/ROI block map | H.264/H.265/AV1 encoders | bitrate without RGB reconstruction |
| Tile / quadtree atlas | controlled decoder/server | transmitted pixels/bytes |
| Patch/token policy | models we control | visual tokens / prefill FLOPs |
| Send/skip decision | streaming systems | entire frames / model requests |

## Predictive attention state

The production state is not just an `(x, y)` gaze coordinate. A useful approximation is:

```text
x = [u, v, du/dt, dv/dt, log_scale, dlog_scale/dt, depth_or_inv_depth]
P = state covariance
```

The ROI expands automatically when uncertainty rises:

```text
R_safe = R_task + k_sigma * sqrt(P_uv) + motion_margin(latency)
```

This means confidence is converted directly into bandwidth. A stable track uses a narrow high-quality region; uncertainty spends more pixels instead of silently cropping away the target.

## Predictive API direction

Rust:

```rust
use foveastream::{
    AttentionMeasurement, CameraIntrinsics, PredictionConfig,
    PredictiveAttentionFilter,
};

let mut tracker = PredictiveAttentionFilter::new(PredictionConfig::default());
tracker.correct(AttentionMeasurement {
    center: (0.52, 0.43).into(),
    confidence: 0.92,
    variance: 0.0004,
    timestamp_s: 10.0,
});

tracker.apply_flow_observation(0.004, -0.001, 1.0/60.0, 0.8);
tracker.apply_gyro_rotation(
    0.07, -0.02, 1.0/200.0,
    CameraIntrinsics::from_fov(1920, 1080, 70.0, 50.0),
);

let roi = tracker.uncertainty_roi(0.18, 0.18, 2.5, 0.15);
```

Python reference:

```python
from foveastream.predictive import AttentionMeasurement, PredictiveAttentionFilter

p = PredictiveAttentionFilter()
p.correct(AttentionMeasurement(.52, .43, confidence=.92, timestamp_s=10.0))
p.apply_flow(.004, -.001, 1/60, confidence=.8)
p.apply_gyro(.07, -.02, 1/200)
roi = p.roi(base_w=.18, base_h=.18, sigma=2.5, horizon_s=.15)
```

## Foveation API

```python
import numpy as np
from foveastream import FoveationConfig, Roi, quality_map, foveate, context_and_roi_views

frame = np.zeros((720, 1280, 3), dtype=np.uint8)
roi = Roi(0.35, 0.30, 0.30, 0.35)
cfg = FoveationConfig(peripheral_scale=0.18)

q = quality_map(frame.shape, points=[(0.50, 0.48)], rois=[roi], config=cfg)
same_size = foveate(frame, q, cfg)
vlm_views = context_and_roi_views(frame, [roi], context_scale=0.25, roi_max_side=512)
```

## Low-level performance direction

Production implementations should avoid reconstructing full RGB frames whenever the downstream path exposes a cheaper actuator.

Priorities:

- operate quality maps on encoder block/tile grids where possible;
- stay in native YUV planes rather than YUV→RGB→YUV;
- zero-copy AHardwareBuffer / CVPixelBuffer / IOSurface / DMA-BUF / GPU texture paths;
- NEON on ARM and AVX2/AVX-512 on x86 where beneficial;
- Metal/Vulkan/CUDA compute only when dispatch/transfer overhead is justified;
- generate encoder QP maps directly from the coarse belief field;
- reuse AR/VIO pose, depth and optical-flow products when the platform already computes them;
- precompute radial/elliptical kernels and warp/translate them instead of rebuilding full-resolution fields;
- fixed-point or fp16 state math where error bounds allow it;
- use lightweight sparse KLT/feature tracking on the always-on path and reserve heavier flow/depth networks for devices where NPU/GPU budget supports them.

## Benchmarking

The benchmark suite is intended to measure **task-aware rate/distortion**, not visual prettiness.

Production metrics:

- bytes/s and requests/s;
- decoded pixels/s;
- actual/estimated visual tokens and billed model cost;
- CPU/GPU/NPU time and energy;
- p50/p95 end-to-end latency;
- target coverage / ROI miss probability;
- gaze/target tracking error;
- reacquisition time after occlusion or rapid motion;
- downstream task quality: defect recall, OCR recall, VQA quality, detector accuracy, etc.

Baselines should include full resolution, global downscale, fixed center crop, ground-truth gaze crop upper bound, software gaze prediction, foveation-only and adaptive frame selection.

## Prior art and product boundary

The broad concept is not novel by itself. Existing work includes gaze-conditioned egocentric MLLM input, software-only gaze prediction, variable-resolution VLM encoding, foveated tokenization/event vision, XR foveated transport and standard encoder ROI/QP controls.

The commercial thesis is the **predictive runtime/control plane** combining asynchronous sensor fusion, temporal propagation, uncertainty-aware budget allocation, hardware/model adapters, adaptive transmission and task-aware policy calibration under one portable API.

See [`docs/RESEARCH.md`](docs/RESEARCH.md) and [`docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`](docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md).

## Repository layout

- `src/` — dependency-light Rust/native core;
- `src/predictive.rs` — predictive attention state and uncertainty propagation;
- `src/scheduler.rs` — adaptive send/skip controller;
- `include/foveastream.h` — C ABI;
- `python/foveastream/` — Python reference/benchmark API;
- `bench/` — spatial/video/predictive benchmark tooling;
- `docs/assets/real_demo/` — measured real-video output and README animation;
- `docs/RESEARCH.md` — prior-art and product-gap research;
- `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md` — systems design;
- `docs/COMMERCIAL.md` — licensing/monetization without operating inference infrastructure;
- `docs/STATUS.md` — durable engineering handoff.

## Engineering status

This repository is an active engineering/research codebase aimed at a production SDK. Remaining work includes zero-copy platform camera/encoder adapters, full timestamp synchronization, VIO/depth integrations, hardware-specific acceleration, downstream-task benchmarks and commercial packaging.

The endpoint is a reusable production library, not a one-off filter or application-specific implementation.

## License

The repository is private and the code is currently **all rights reserved** while the commercial model is validated. The proposed product model is a proprietary optimized native core plus evaluation/reference tooling and annual commercial/OEM licensing. See [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).
