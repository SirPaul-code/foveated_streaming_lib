# FoveaStream

**Predictive attention transport for machine vision.**

FoveaStream is being built as a production-grade, cross-platform native SDK for reducing the **bytes, pixels, visual tokens, latency and edge energy** required by continuous vision systems while preserving task-relevant detail.

This is not intended to stop at a demo, radial blur filter, or gaze cropper. The target runtime fuses asynchronous evidence at different rates, predicts where useful visual information will be at downstream consumption time, represents uncertainty explicitly, and allocates transport/model budget accordingly.

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

The important time is not capture time. We predict for:

```text
t_capture + local_processing + encode + network + queue + model_ingest
```

so the high-quality support is where the target is expected to be when the downstream consumer sees it.

## Why 1 FPS does not mean 1 Hz perception

A device can capture locally at 30-120 FPS and ingest IMU at 100-1000 Hz while only sending ~1 FPS or less to an expensive VLM.

FoveaStream separates:

- **fast local propagation** — IMU integration, sparse flow, pose, tracks;
- **medium-rate perception** — lightweight object/hand/saliency/depth updates;
- **slow semantic refresh** — VLM or heavier detector;
- **adaptive transmission** — send only when innovation/staleness/uncertainty requires it.

That prevents the classic failure mode where a 1 FPS system independently rediscovers/crops the scene every second and produces temporal jumps, clipped targets, and redundant requests.

## Current native primitives

Rust core currently contains:

- spatial quality-field generation;
- same-size foveated RGB generation;
- context + high-resolution ROI views;
- encoder block QP-delta map generation;
- simple focus fusion/latency compensation;
- uncertainty-aware predictive attention filter;
- camera-ray gyro propagation;
- local optical-flow velocity correction;
- fixation / pursuit / saccade / lost motion modes;
- future ROI generation with covariance dilation;
- adaptive send/skip scheduler with innovation, uncertainty and maximum-staleness gates;
- C ABI surface for native/mobile integration.

Python is kept as a reference/benchmark layer. The performance target is native zero/low-copy execution, not Python image processing.

## Important design rule: IMU is not gaze

Gyroscope/accelerometer data alone do **not** reveal where the eyes are looking. IMU is valuable because it provides very low-latency ego-motion evidence and can propagate an already selected world direction/ROI between image/semantic updates.

Actual relevance may come from:

- eye tracking;
- software-only egocentric gaze estimation;
- explicit UI point/rectangle;
- task-specific detector/prompt localizer;
- hand/object interaction;
- optical flow and track identity;
- saliency;
- depth/autofocus;
- center prior;
- downstream model feedback.

The runtime combines these as uncertain evidence rather than pretending one sensor is ground truth.

## Output modes

A blurred 1280x720 frame is still 1280x720; for many VLMs that may not reduce visual-token cost. FoveaStream therefore separates the **attention policy** from the **actuator**.

| Output | Use | Saves |
|---|---|---|
| Same-size spatial fidelity frame | ordinary JPEG/WebRTC pipelines | entropy / encoded bytes |
| Low-res global context + high-res ROI views | third-party VLM APIs | actual pixels and often visual tokens |
| QP/ROI block map | H.264/H.265/AV1 hardware/software encoders | bitrate without RGB reconstruction |
| Future patch/token adapter | models we control | prefill FLOPs/tokens |
| Send/skip decision | streaming systems | entire frames / requests |

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

// ROI widened automatically by predicted uncertainty at the downstream horizon.
let roi = tracker.uncertainty_roi(0.18, 0.18, 2.5, 0.15);
```

Python reference:

```python
from foveastream.predictive import (
    AttentionMeasurement,
    PredictiveAttentionFilter,
)

p = PredictiveAttentionFilter()
p.correct(AttentionMeasurement(.52, .43, confidence=.92, timestamp_s=10.0))
p.apply_flow(.004, -.001, 1/60, confidence=.8)
p.apply_gyro(.07, -.02, 1/200)
roi = p.roi(base_w=.18, base_h=.18, sigma=2.5, horizon_s=.15)
```

## Existing foveation API

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

## Benchmarking

The benchmark suite is designed to measure **task-aware rate/distortion**, not just whether an image looks blurred.

Metrics to collect on real workloads:

- bytes/s and frame/request rate;
- decoded pixels/s;
- actual/estimated visual tokens and billed model cost;
- CPU/GPU/NPU time and energy;
- p50/p95 end-to-end latency;
- target coverage / ROI miss probability;
- gaze/target tracking error;
- reacquisition time after occlusion or rapid camera motion;
- downstream task quality: defect recall, OCR recall, VQA/description quality, detector accuracy, etc.

Synthetic spatial benchmark already showed that the context+ROI representation can reduce actual pixel count far more than same-size peripheral degradation. A separate predictive benchmark models 1 Hz semantic reacquisition with 60 Hz local tracking; it exists to validate systems behavior before real eye-tracked/task datasets are wired in. Synthetic numbers are not treated as product claims.

Run on a real feed:

```bash
python bench/benchmark_video.py input.mp4 \
  --frames 300 \
  --roi 0.35 0.30 0.30 0.35 \
  --context-scale 0.25 \
  --roi-max-side 512 \
  --peripheral-scale 0.18 \
  --output-json result.json \
  --preview-dir preview
```

Predictive systems benchmark:

```bash
python bench/benchmark_predictive.py --seconds 8 --local-fps 60 --semantic-fps 1
```

## Prior art and product boundary

The broad concept is not novel by itself. Existing work includes:

- GazeLLM — gaze-conditioned egocentric MLLM input;
- EgoGazeLite — software-only on-device gaze prediction + crop;
- FAVE — variable-resolution visual encoding;
- Foveated Instance Segmentation / foveated tokenization;
- StreamingTOM and adaptive frame/token pruning;
- foveated event vision;
- XR foveated transport and standard encoder ROI/QP controls.

The commercial thesis is therefore the **predictive runtime/control plane** that combines asynchronous sensor fusion, temporal propagation, uncertainty-aware budget allocation, adaptive transmission, hardware/model adapters, and task-aware policy calibration.

See [`docs/RESEARCH.md`](docs/RESEARCH.md) and [`docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`](docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md).

## Repository layout

- `src/` — dependency-light Rust/native core.
- `src/predictive.rs` — predictive attention state and uncertainty propagation.
- `src/scheduler.rs` — adaptive frame send/skip controller.
- `include/foveastream.h` — C ABI.
- `python/foveastream/` — Python reference/benchmark API.
- `bench/` — spatial/video/predictive benchmark tooling.
- `docs/RESEARCH.md` — prior-art and product-gap research.
- `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md` — deep systems design.
- `docs/COMMERCIAL.md` — commercial licensing without operating inference infrastructure.
- `docs/STATUS.md` — durable engineering handoff and next work.

## Engineering status

This repository is an active engineering/research codebase aimed at a full SDK. It is **not yet production-ready**: the native algorithms need benchmark-driven refinement, zero-copy platform camera/encoder adapters are not complete, real sensor timestamp synchronization and VIO/depth integrations still need implementation, and downstream task benchmarks must validate the savings/accuracy frontier.

The intended endpoint is a production library, not an MVP stopping point.

## License

The repository is private and the code is currently **all rights reserved** while the commercial model is validated. The proposed product model is a proprietary optimized native core plus evaluation/reference tooling and annual commercial/OEM licensing. See [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).
