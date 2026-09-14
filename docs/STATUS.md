# STATUS / handoff

Last updated: 2026-09-14

## Goal

Build a **production-grade predictive attention transport SDK** for machine-vision/VLM workloads. The runtime must combine asynchronous relevance evidence and local motion sensing, predict future task-relevant support at downstream consumption time, and reduce bytes/pixels/tokens/energy while preserving a measurable task-quality constraint.

This project must not stop at an MVP/demo or generic foveated image filter.

## Research conclusion

The broad idea has substantial prior art:

- XR foveated transport is established.
- Encoder ROI/QP primitives already exist.
- GazeLLM already shows gaze-conditioned MLLM crops.
- EgoGazeLite (2026-08-16) already provides software-only on-device egocentric gaze prediction for token-efficient MLLM input.
- FAVE, StreamingTOM, foveated tokenization, adaptive frame pruning, and foveated event vision attack adjacent parts of the same efficiency problem.

Therefore differentiation must be the **predictive systems/control layer**, not the crop/falloff function:

```text
asynchronous evidence
-> future relevance belief + covariance
-> quality field / send-skip decision
-> VLM / encoder / token actuator
-> task-aware calibration
```

See `docs/RESEARCH.md` and `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`.

## Implemented now

### Rust native core

Existing spatial path:

- normalized points/ROIs;
- configurable falloff;
- quality-field generation;
- same-size RGB foveation;
- context + ROI views;
- per-block signed QP delta map;
- C ABI.

Predictive path added:

- `AttentionState` with position, velocity, scale, covariance/confidence and motion mode;
- `PredictionConfig` with mode-dependent process noise;
- causal predict/correct attention filter;
- fixation / pursuit / saccade / lost modes;
- optical-flow velocity observation;
- gyro camera-ray propagation through calibrated pinhole intrinsics;
- future-state prediction at explicit latency horizon;
- covariance-dilated future ROI;
- adaptive send/skip scheduler;
- innovation score from pose, ROI error, residual flow, scene change, semantic uncertainty and age;
- hard trigger, maximum-staleness and uncertainty-limit send gates.

Main source additions:

- `src/predictive.rs`
- `src/scheduler.rs`

### Python reference / benchmark layer

- original quality map/foveation/context+ROI/QP tools;
- `python/foveastream/predictive.py` mirrors the predictive concepts for experimentation;
- `bench/benchmark_video.py` measures spatial/payload reduction on real video;
- `bench/benchmark_predictive.py` models low-rate semantic reacquisition plus high-rate local propagation.

## Current measured results

### Spatial synthetic benchmark already run

1280x720, 60 frames, one protected ROI:

- same-size foveated JPEG: **26.55% fewer bytes**;
- context + ROI representation: **83.25% fewer pixels** / **5.97x** reduction;
- context + ROI JPEG payload: **70.91% fewer bytes**;
- protected ROI stayed pixel-identical in that configuration;
- Python reference qmap+foveation was only ~10 FPS in the execution environment, confirming Python is not the production path.

### Predictive systems simulation

A synthetic 8 s trajectory with:

- local tracking: 60 Hz;
- semantic reacquisition: 1 Hz;
- noisy semantic center measurements;
- noisy local flow updates;
- base ROI side: 0.18 normalized frame width/height;

produced in the current simulation:

- predictive covariance-dilated ROI target coverage: **100%**;
- stale 1 Hz fixed ROI coverage at the same 18%x18% base support: **58.96%**;
- predictive mean area: **10.17% of frame**;
- stale 1 Hz square support needed to match 100% coverage in the same generated trace: **23.44% of frame**;
- predictive support therefore used about **56.6% less image area at equal coverage** in this synthetic scenario.

This is a systems sanity check only, **not a real-world gaze/product claim**. It demonstrates why high-rate local propagation can outperform independent 1 Hz reacquisition. Real eye-tracked/task datasets are mandatory before commercial claims.

## CI status

`.github/workflows/ci.yml` was added for:

- Rust tests/build on Ubuntu, Windows and macOS;
- Python tests on Ubuntu.

At the latest check GitHub's Actions API returned **zero workflow runs**, so Rust cross-platform compilation is **not yet verified**. Do not claim CI success until a run actually appears and passes.

## Architecture decisions that are now fixed unless evidence changes them

1. **IMU is not gaze.** It propagates an existing direction/track cheaply between visual updates.
2. **The stable internal contract is future relevance + uncertainty**, not a fixed radial blur mask.
3. **1 FPS uplink must still permit high-rate local tracking.** Camera/IMU processing and transmitted/VLM frame rate are decoupled.
4. **Prediction horizon includes encode/network/queue/model latency.**
5. **Uncertainty maps to bandwidth.** Wider covariance -> wider quality support.
6. **Independent per-frame crops are not acceptable** for sparse temporal feeds; track identity and relevance state persist.
7. **Native encoder maps beat RGB reconstruction** when QP/ROI controls are available.
8. **Zero bytes is better than compressed bytes.** Adaptive frame skipping is first-class.
9. **Business value is downstream task quality per euro/byte/joule**, not visual appearance.

## Required next engineering work

### P0 — correctness and systems foundation

1. Get real CI running; fix all Rust compile/test errors on Linux/Windows/macOS.
2. Replace the current lightweight filter with a timestamped multi-rate `Evidence` bus:
   - monotonic timestamps;
   - source clock mapping;
   - confidence/covariance;
   - TTL;
   - point/rect/mask/pose/flow observation types.
3. Implement proper quaternion IMU pre-integration and camera-to-IMU extrinsics.
4. Add rolling-shutter-aware gyro projection option.
5. Implement robust ego-motion / residual-flow decomposition:
   - sparse KLT/ORB path first;
   - RANSAC homography/essential matrix;
   - residual object motion into tracked ROI state.
6. Add explicit track IDs and occlusion/reacquisition state.
7. Implement quality-map advection/warping between semantic refreshes.
8. Add motion-corridor support along predicted future trajectories.

### P1 — low-level production performance

1. Keep quality fields at encoder block/tile resolution whenever possible.
2. ARM NEON and x86 AVX2 kernels.
3. Native YUV/NV12 processing; avoid RGB conversion.
4. Android AHardwareBuffer / ImageReader / CameraX zero-copy path.
5. Apple CVPixelBuffer / IOSurface / Metal path.
6. Linux DMA-BUF / GStreamer path where possible.
7. Reuse ARCore/ARKit/OpenXR pose/depth instead of duplicating SLAM.
8. Direct hardware encoder adapters:
   - Android MediaCodec;
   - NVENC;
   - VideoToolbox if ROI controls permit/useful fallback otherwise;
   - VAAPI/x264/x265/FFmpeg.

### P2 — relevance inference

1. Software gaze adapter interface; benchmark EgoGazeLite-class approaches rather than reinventing blindly.
2. Hand/object interaction prior.
3. compact saliency model;
4. optional lightweight depth / use platform depth;
5. task-detector and prompt-localizer adapters;
6. multi-hypothesis attention mixture when one point estimate is unsafe.

### P3 — model-side efficiency

1. Provider/API adapters that estimate actual visual-token/billing behavior by dimensions and number of images.
2. context + ROI multi-image planner.
3. dirty-tile/quadtree atlas for controlled server/local model.
4. patch/token selector for models whose vision encoder can be modified.
5. optional embedding cache / temporal token reuse only where model architecture permits it.

### P4 — benchmark science

Benchmark against:

1. full-resolution fixed FPS;
2. global downscale;
3. fixed center crop;
4. stale 1 FPS ROI;
5. ground-truth gaze upper bound;
6. software gaze crop baseline;
7. adaptive frame selection only;
8. spatial foveation only;
9. FoveaStream full predictive policy.

Metrics:

- bytes/s;
- transmitted frames/s;
- decoded pixels/s;
- visual tokens / billed image cost;
- CPU/GPU/NPU ms;
- power/energy where measurable;
- p50/p95 end-to-end latency;
- target coverage / miss risk;
- tracking error;
- reacquisition latency;
- downstream task metric.

Target datasets should include egocentric gaze + at least one actual monetizable task such as OCR or industrial inspection.

## Commercial plan

Recommended model:

- edge-only runtime, no hosted inference;
- proprietary optimized native core;
- free/evaluable benchmark/reference layer;
- annual commercial SDK license;
- OEM/fleet rights negotiated separately;
- Lemon Squeezy can provide checkout/license activation without operating our own backend;
- fully offline enterprise customers get locally verified Ed25519-signed license files.

See `docs/COMMERCIAL.md` for concrete packaging/pricing hypotheses and the honest limits of offline license enforcement.

## Current product checkpoint

The project remains a **GO** only if real downstream benchmarks show a Pareto improvement over simple baselines and close prior art.

Win condition:

> lower total bytes/tokens/latency/energy at equivalent application accuracy and bounded miss probability.

Anything less is only image processing, not a defensible commercial SDK.
