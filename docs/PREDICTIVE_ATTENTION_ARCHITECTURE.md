# Predictive Attention Transport — deep architecture

Last updated: 2026-09-14

## Product definition

FoveaStream is an **asynchronous predictive attention transport engine** for machine vision.

The core problem is:

> Given a camera stream, optional sensors and local perception signals, and a downstream vision path with a finite byte/pixel/token/latency budget, predict what spatial-temporal information will matter when the downstream consumer actually uses it.

The internal state is therefore not just `ROI_t`; it is a probability distribution over useful image support at a future horizon:

```text
P(attention / task relevance at image position x,y | evidence up to t)
                                      evaluated at t + processing + encode + network + queue + ingest
```

The emitted quality field `Q(x,y,t+h)` is the control surface for transport and model-input allocation.

## Configurable temporal architecture

Capture, sensor, analysis and output cadence are independent runtime parameters.

```text
IMU / pose / eye data --------+
                              |
camera frames -> ego-motion / sparse flow / tracks ----+
                              |                          |
manual/gaze/task evidence ----+--> predictive belief ---+--> quality field
                                                         |
semantic/model evidence ---------------------------------+     |
                                                               v
                                                      scheduler / actuator
                                                               |
                                                               v
                                               frame / crop / tile / QP-map
```

The tracker can update whenever any evidence arrives. The output cadence is then chosen separately from the local analysis cadence.

## Device-independent evidence model

Every measurement carries a timestamp and uncertainty:

```text
Evidence {
  timestamp_ns,
  source_type,
  geometry_or_motion,
  covariance,
  confidence,
  ttl
}
```

Platform adapters map native data into this form:

- Android/ARCore/Camera2/CameraX;
- iOS/ARKit/AVFoundation;
- Meta/OpenXR;
- Windows Media Foundation/DirectShow;
- Linux V4L2/GStreamer;
- robotics/custom embedded feeds.

The core must not depend on any one camera API.

## State representation

A practical state for each attention hypothesis / task ROI:

```text
x = [u, v, du/dt, dv/dt, log_scale, dlog_scale/dt, depth_or_inv_depth]
P = state covariance
class/task id
confidence
age
support mask or rectangle
```

For multiple hypotheses use a mixture or raster belief field.

### Why covariance matters

A hard crop is unsafe when predicted position is uncertain. Spatial budget should expand with uncertainty:

```text
R_safe = R_task + k_sigma * sqrt(P_uv) + motion_margin(latency)
```

Confidence therefore maps directly to bandwidth: stable tracks use narrow high-quality support; uncertain tracks spend more pixels rather than clipping the target.

## Motion model

### IMU / camera rotation propagation

For calibrated intrinsics `K`, propagate an image ray using timestamped device rotation:

```text
r_t = normalize(K^-1 [u,v,1]^T)
r_t+h = R(t -> t+h)^-1 r_t
p_t+h ~ K r_t+h
```

Use quaternion integration and calibrated camera↔IMU extrinsics. Do not approximate image motion as `u += gyro * dt` except as a crude fallback.

Rotation is depth-independent. Translation requires depth or an inverse-depth estimate.

### Translation with depth

For a tracked 3-D point `X`:

```text
X' = R X + t
p' = pi(K X')
```

Depth uncertainty should be carried into image-space uncertainty; nearby objects need larger translational motion margins.

### Optical-flow correction

Optical flow is evidence of how existing image support moved, not a direct gaze estimate.

Fast-path options:

- pyramidal Lucas–Kanade/KLT sparse tracks;
- FAST/ORB + robust affine/homography/essential-matrix background motion;
- residual object flow after ego-motion compensation;
- dense learned flow only where accelerator/energy budget justifies it.

Useful decomposition:

```text
observed flow = background ego-motion + residual object motion
```

The background term updates camera motion; coherent residual flow updates task/object motion.

### Hand/object/task priors

Egocentric relevance is often correlated with hand/object interaction, task state and head motion. Treat those as additional probabilistic evidence instead of assuming image saliency alone is sufficient.

## Behaviour modes

A single smooth constant-velocity model is inadequate. A switching model can maintain probabilities over:

- `FIXATION`: low process noise, narrow quality support;
- `PURSUIT`: velocity/flow-driven propagation;
- `SACCADE`: high process noise and wider predicted endpoint support;
- `LOST`: increased uncertainty and fallback to other evidence sources.

An interacting multiple-model filter or lightweight HMM is a natural extension.

## Time alignment

All measurements must be mapped into one monotonic time domain. Camera exposure midpoint, sensor latency and rolling shutter matter.

Without timestamp calibration, high-rate IMU or pose data can make prediction worse rather than better.

For rolling-shutter cameras, row-time gyro correction can be added on platforms that expose enough metadata.

## Spatial relevance propagation

When a semantic/task pass produces relevance map `Q_t`, do not rebuild it independently at the next output frame.

Conceptually:

```text
Q^-_{t+1}(x + flow(x)) <- Q_t(x)
```

Then:

1. warp/advection using ego-motion + residual motion;
2. decay stale evidence;
3. merge new gaze/task/saliency/depth/track observations;
4. dilate by uncertainty;
5. correct when higher-confidence evidence arrives.

This is a predict/correct filter over a spatial relevance field.

## Avoiding temporal artifacts

Independent hard crops cause support teleportation, target clipping and inconsistent context. Use:

- ROI hysteresis;
- motion corridors along predicted trajectories;
- covariance-driven support dilation;
- persistent low-resolution global context;
- persistent track IDs;
- gradual support resizing;
- reacquisition gates when innovation or uncertainty rises.

## Adaptive scheduler

The scheduler decides whether to emit a new output and how much budget to spend.

Example innovation score:

```text
I_t = w_pose * pose_novelty
    + w_roi  * prediction_error
    + w_flow * residual_motion
    + w_scene* scene_change
    + w_sem  * semantic_uncertainty
    + w_age  * staleness
```

Emit when the score, uncertainty, hard task trigger or maximum staleness policy requires it.

A stronger formulation is constrained rate-distortion control:

```text
min_policy   E[task_loss]
subject to   E[bytes/s] <= B
             E[tokens/s] <= T
             P(target missed) <= epsilon
             latency <= L
```

or a Lagrangian:

```text
J = task_loss + lambda_b*bytes + lambda_t*tokens + lambda_l*latency + lambda_e*energy
```

This is the long-term auto-calibration target for each device/model/task combination.

## Output actuators

### Third-party VLM APIs

Best practical representation:

- low-resolution global context;
- one or more full-detail ROI crops;
- optional full-frame keyframes when uncertainty or task policy requires them.

This changes actual input pixels and can reduce visual-token cost where provider tokenization depends on image dimensions/patching.

### Controlled model/server

More aggressive options:

- quadtree tile atlas;
- dirty-tile temporal updates;
- log-polar/foveal pyramid;
- selected vision patches/tokens;
- cached unchanged embeddings when the model architecture permits it.

### Video transport

Use native encoder controls whenever available:

- H.264/H.265/AV1 block QP maps;
- ROI rectangles;
- tile/slice controls;
- keyframe scheduling coordinated with relevance uncertainty.

Do not reconstruct a degraded RGB image if the encoder can directly allocate bits spatially.

## Low-level performance targets

- quality maps on encoder/tile grids rather than full resolution when possible;
- native YUV processing to avoid RGB conversion;
- zero-copy AHardwareBuffer / CVPixelBuffer / IOSurface / DMA-BUF / GPU textures;
- NEON on ARM, AVX2/AVX-512 on x86;
- Metal/Vulkan/CUDA kernels only where dispatch/transfer overhead is worthwhile;
- direct QP-map generation from coarse belief fields;
- precomputed radial/elliptical kernels and warped lookup maps;
- fp16/fixed-point state math where bounded error permits it;
- reuse AR/VIO pose, depth and optical-flow products already computed by the platform;
- sparse KLT/feature tracking as a cheap always-on path;
- learned flow/depth only on hardware profiles where the NPU/GPU budget supports them.

## Benchmark methodology

Compression-only benchmarks are insufficient.

Measure:

- transmitted bytes/s;
- decoded pixels/s;
- estimated/actual visual tokens/s;
- preprocessing CPU/GPU/NPU time;
- joules/frame where measurable;
- p50/p95 end-to-end latency;
- target coverage probability;
- ROI tracking error;
- downstream task accuracy/recall;
- reacquisition time after motion/occlusion.

Compare against:

1. full resolution;
2. global downscale;
3. fixed center crop;
4. ground-truth gaze/task ROI upper bound;
5. software gaze prediction;
6. spatial foveation without prediction;
7. adaptive frame selection only;
8. full FoveaStream predictive policy.

## Commercial differentiation

The defensible product is not the radial quality function or one gaze model. It is the runtime control plane that combines sensor fusion, temporal propagation, uncertainty-aware budget allocation, portable hardware/model adapters and task-aware calibration under a single API.

The buyer-facing objective should look like:

```text
Preserve >= 99.5% task recall while minimizing uplink MB, model pixels/tokens and device energy.
```

The benchmark/calibration tooling should derive a policy for the target device, camera stack, model and application.
