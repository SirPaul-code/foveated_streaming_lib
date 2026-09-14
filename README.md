# FoveaStream

**Sensor- and ROI-driven foveated preprocessing for vision models and video encoders.**

FoveaStream is an edge-side SDK experiment for sending **fewer useful pixels/bytes** to vision models without throwing away the detail that matters. The core idea is to decouple:

1. **Where should quality go?** — manual ROI, gaze/eye tracking, task detectors, saliency, depth, center prior, IMU-compensated focus tracking, or an application-provided quality map.
2. **How should that quality budget be emitted?** — a same-size entropy-reduced frame, a low-resolution context + high-resolution ROI views for VLMs, or a per-block QP map for hardware/video encoders.

The repository intentionally does **not** try to own camera capture. A frame buffer is the portable ABI boundary; CameraX/MediaCodec, AVFoundation, OpenCV, GStreamer, WebRTC, ARCore/ARKit, smart-glasses SDKs, etc. are adapters around it.

> Status: v0.1 research/prototype. Python reference backend is tested. The Rust core and C ABI are included and validated in CI; hardware encoder adapters are on the roadmap.

## Why this is not just “blur the edges”

A 1280x720 blurred image is still 1280x720. Many VLMs derive visual-token cost mainly from dimensions/patching, so peripheral blur may save JPEG/video bytes but **not model tokens**. FoveaStream therefore exposes three output modes:

| Mode | Output | What it can save |
|---|---|---|
| `foveate()` | same-size RGB frame with spatially varying fidelity | JPEG/video entropy and network bytes |
| `context_and_roi_views()` | small global context + one or more high-res crops | actual input pixels and often VLM visual tokens |
| `qp_delta_map()` | signed block map | H.264/H.265/AV1 encoder bitrate while preserving ROI quality |

## Current API

```python
import numpy as np
from foveastream import FoveationConfig, Roi, quality_map, foveate, context_and_roi_views

frame = np.zeros((720, 1280, 3), dtype=np.uint8)
roi = Roi(0.35, 0.30, 0.30, 0.35)
cfg = FoveationConfig(peripheral_scale=0.18)

q = quality_map(
    frame.shape,
    points=[(0.50, 0.48)],       # gaze / cursor / center prior
    rois=[roi],                  # hard user/task ROI
    config=cfg,
    custom_map=None,             # optional depth/saliency/task map
)

same_size = foveate(frame, q, cfg)
vlm_views = context_and_roi_views(frame, [roi], context_scale=0.25, roi_max_side=512)
```

For camera-motion compensation and noisy focus sources:

```python
from foveastream import FocusCandidate, FocusTracker

focus = FocusTracker.fuse([
    FocusCandidate(0.50, 0.50, confidence=0.6, weight=0.4),  # center prior
    FocusCandidate(0.62, 0.44, confidence=0.9, weight=1.0),  # eye tracker / UI ROI center
])

tracker = FocusTracker()
predicted = tracker.update(focus, dt_s=1/30)
predicted = tracker.compensate_imu(predicted, gyro_yaw_rad_s=0.08, gyro_pitch_rad_s=-0.02, dt_s=1/30)
```

**Important:** a gyroscope/accelerometer does not tell us where the user is looking. IMU is used to predict how an already selected direction/ROI moves as the camera or head rotates. Actual attention should come from eye tracking, explicit UI, task semantics/detections, focus/depth, saliency, or a center prior.

## Synthetic smoke benchmark

The committed benchmark is deliberately a reproducible smoke test, not a claim about real-world VLM accuracy. On 60 synthetic 1280x720 frames, one 30%x35% ROI, `context_scale=0.25`, `roi_max_side=512`, `peripheral_scale=0.18`:

- same-size foveated JPEG: **26.55% fewer bytes** than baseline JPEG;
- context + ROI representation: **83.25% fewer pixels** (**5.97x** pixel reduction);
- context + ROI JPEG payload: **70.91% fewer bytes**;
- pixels inside the protected ROI are unchanged in this configuration;
- Python reference quality-map + foveation path: **~10.3 FPS** on the current test container, so it is a correctness/reference backend, not the production performance target.

See [`docs/BENCHMARKING.md`](docs/BENCHMARKING.md) and [`docs/benchmark_synthetic_720p.json`](docs/benchmark_synthetic_720p.json). For a real product decision, run the same sweep on representative video **and measure downstream VLM task accuracy**, not just compression.

## Architecture

```text
camera / video / screen / XR frame
              |
              v
      Focus / ROI policy
 manual rect | gaze | task | saliency | depth | center prior
              + IMU motion compensation / temporal filter
              |
              v
       Quality field Q(x,y)
              |
       +------+------+----------------+
       |             |                |
       v             v                v
 same-size RGB   context + ROI    block QP map
 JPEG/WebRTC     VLM multi-image  NVENC/MediaCodec/
 transport       or atlas         FFmpeg/VAAPI/etc.
```

The quality map is the stable internal contract. Sensor and encoder integrations remain replaceable.

## Repository layout

- `src/` — dependency-light Rust core.
- `include/foveastream.h` — C ABI for Android/iOS/C++/Unity/native apps.
- `python/foveastream/` — Python reference API + optional native loader.
- `bench/` — video benchmark and synthetic generator.
- `examples/live_camera.py` — interactive webcam preview.
- `docs/RESEARCH.md` — prior-art analysis and product gap.
- `docs/ARCHITECTURE.md` — design, focus policy, output modes, platform adapters.
- `docs/COMMERCIAL.md` — monetization/licensing plan without operating our own server.
- `docs/STATUS.md` — durable handoff/current state/next steps.

## Install reference backend

```bash
python -m pip install -e '.[video,dev]'
pytest -q
```

## Benchmark a real feed

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

The next benchmark layer should call the actual target VLM and report **task success / accuracy / latency / visual-token or billed-image cost** versus pixel budget.

## License

This repository is currently private and the code is **all rights reserved** while the commercial model is validated. Do not publish it under MIT/Apache if the goal is paid commercial SDK licensing; that would remove most technical leverage over downstream commercial use. See [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).
