# Predictive Attention Transport — deep architecture

Last updated: 2026-09-14

## Product redefinition

FoveaStream is not a blur/crop library and not merely a gaze predictor. The target system is an **asynchronous predictive attention transport engine** for machine vision.

The core problem is:

> Given expensive semantic inference at low rate (often ~1 FPS or less), cheap sensors and local vision at much higher rate, and a downstream vision model with a finite byte/pixel/token budget, predict what spatial-temporal information will matter at the time the downstream model actually consumes it.

The state we care about is therefore not `ROI_t`; it is a probability distribution over useful image support at a future horizon:

```text
P(attention / task relevance at image position x,y | evidence up to t)
                                      evaluated at t + encode + network + queue + inference latency
```

The emitted quality field `Q(x,y,t+h)` is the control surface for transport and model-input allocation.

## Why 1 FPS must NOT mean 1 Hz tracking

A device can ingest camera frames at 30/60 FPS and IMU at 100-1000 Hz while transmitting only 1 FPS to the expensive model.

Architecture:

```text
IMU 100-1000 Hz -----------+
                            |
local camera 30-60 FPS -> ego-motion / sparse flow / tracks ----+
                            |                                    |
manual/gaze/task cue -------+--> predictive belief state --------+--> quality field
                                                                 |
VLM semantic refresh ~1 Hz --------------------------------------+     |
                                                                       v
                                                      transmission scheduler
                                                           | send / skip
                                                           v
                                                frame / crop / tile / QP-map
```

The low-rate VLM acts as a semantic anchor. Local tracking propagates that anchor through time instead of rediscovering it every second.

## State representation

A practical state for each attention hypothesis / task ROI:

```text
x = [u, v, du/dt, dv/dt, log_scale, dlog_scale/dt, depth_or_inv_depth]
P = state covariance
class/task id
confidence
last_semantic_refresh_timestamp
support mask or rectangle
```

For multiple hypotheses use a mixture:

```text
B_t = sum_k w_k * N(x_k, P_k)
```

or a raster belief map for arbitrary shapes.

### Why covariance matters

If predicted gaze/ROI center is uncertain by sigma pixels, a hard crop is unsafe. The spatial budget should expand with uncertainty:

```text
R_safe = R_task + k_sigma * sqrt(P_uv) + motion_margin(latency)
```

This converts uncertainty directly into bandwidth. When the tracker is confident, the stream gets narrower; when uncertain, it automatically spends more pixels instead of catastrophically cropping the target.

## Motion model

### 1. IMU / camera rotation propagation

Gyroscope samples provide a low-latency rotation estimate. For calibrated intrinsics `K`, a ray corresponding to image point `p` is propagated by:

```text
r_t = normalize(K^-1 [u,v,1]^T)
r_t+h = R(t -> t+h)^-1 r_t
p_t+h ~ K r_t+h
```

Use timestamped quaternion integration, not naive `u += gyro*dt`, and compensate camera-to-IMU extrinsics.

This works for rotation at effectively any scene depth. Translation requires depth.

### 2. Translation using depth / inverse depth

For a tracked 3-D point `X`, camera motion gives:

```text
X' = R X + t
p' = pi(K X')
```

If only monocular depth is available, keep depth uncertainty in the state. Near targets get larger translational pixel displacement than distant targets.

### 3. Optical-flow correction

Optical flow is not itself gaze prediction. It is an observation of how existing image support moved.

Use robust statistics inside and around the ROI:

- pyramidal Lucas-Kanade / KLT for sparse keypoints;
- FAST/ORB + robust homography/essential matrix for background ego-motion;
- dense flow only if accelerator budget permits;
- RAFT-class models are generally too expensive for an always-on mobile fast path unless an NPU/GPU profile justifies them.

Decompose observed flow:

```text
flow = background ego-motion + residual object motion
```

The background component updates camera motion; coherent residual flow updates object/ROI motion.

### 4. Hand/object/task priors

Egocentric gaze is correlated with head motion and hand/object interaction. Classic work already exploited head motion + hands for gaze prediction; future systems can add hand keypoints, object tracks and task semantics as evidence rather than treating gaze as purely image saliency.

## Eye/head behaviour

Head motion is useful but not equivalent to eye gaze. SGaze reported a statistical relationship between gaze position and head angular velocity and a temporal offset between eye and head movement. A 2026 VR paper likewise fuses HMD motion and visual saliency for gaze prediction without eye tracking.

Therefore use head/IMU evidence as a learned conditional prior:

```text
P(gaze_future | head angular velocity/history, visual saliency, task state)
```

not as a deterministic gaze sensor.

## Fixation / saccade mode model

A single smooth Kalman model is wrong during saccades. Use a switching model / IMM:

- `FIXATION`: low process noise, small ROI, aggressive bandwidth reduction;
- `PURSUIT`: constant-velocity / optical-flow driven;
- `SACCADE`: high process noise, predicted endpoint distribution, widen quality field temporarily;
- `LOST`: fall back to center/task/saliency and increase budget.

A Hidden Markov Model or interacting multiple-model filter can maintain mode probabilities.

## Time alignment

Every measurement must carry monotonic capture time:

```text
Evidence {
  timestamp_ns,
  sensor_time_domain,
  source,
  geometry,
  covariance,
  confidence,
  ttl
}
```

Before fusion, map all clocks into one time domain. Camera exposure midpoint matters; rolling-shutter frames can optionally be row-time corrected using gyro integration.

Without time synchronization, high-rate IMU can make prediction worse rather than better.

## Flow-advection of quality maps

When an expensive semantic pass produces relevance map `Q_t`, do not recreate it from zero on the next transmitted frame.

For local frames:

```text
Q^-_{t+1}(x + flow(x)) <- Q_t(x)
```

Then:

1. warp/advection by ego-motion + residual flow;
2. decay stale evidence;
3. merge new local saliency/tracks/manual evidence;
4. expand by state uncertainty;
5. correct using the next semantic/VLM refresh.

This is analogous to a predict/correct filter on a spatial relevance field.

## Avoiding temporal artifacts at low output FPS

At 1 FPS, hard independent crops cause ROI teleporting, clipping and inconsistent context. Use:

### ROI hysteresis
Do not move/resize output support for small belief changes.

### Motion corridor
Allocate quality along the predicted trajectory during the downstream latency interval, not only at the endpoint.

### Uncertainty dilation
Dilate ROI by covariance ellipse. As confidence falls, quality degrades smoothly rather than hard-cropping.

### Context persistence
Keep a low-resolution global context image every transmitted step even when ROI views are narrow.

### Track identity persistence
Use object/feature tracks so ROI IDs survive across outgoing frames.

### Semantic refresh gate
Run expensive local semantic detection only when the cheap tracker reports innovation/loss, not on every local frame.

## Send / skip scheduler

The strongest saving may come from not sending a frame at all.

Define an innovation score:

```text
I_t = w_pose * pose_novelty
    + w_roi  * ROI_prediction_error
    + w_flow * residual_motion
    + w_scene* scene_change
    + w_sem  * semantic_uncertainty
    + w_age  * time_since_refresh
```

Transmit if:

```text
I_t > threshold
OR uncertainty > budgeted maximum
OR a hard event/task trigger fires
OR max_refresh_interval expires
```

This turns fixed 1 FPS into adaptive `0 .. N FPS` while retaining a maximum staleness bound.

A stronger formulation is constrained control / rate-distortion optimization:

```text
min_policy   E[task_loss]
subject to   E[bytes/s] <= B
             E[tokens/s] <= T
             P(target cropped) <= epsilon
             latency <= L
```

or equivalently optimize a Lagrangian:

```text
J = task_loss + lambda_b*bytes + lambda_t*tokens + lambda_l*latency + lambda_e*energy
```

The SDK can auto-tune policy parameters against downstream task feedback.

## Representations / actuators

### Third-party VLM API
Best practical representation:

- low-res global context image;
- one or more high-res ROI crops;
- optional occasional full-frame keyframe.

This genuinely changes pixel/token budget without requiring a custom decoder.

### Controlled server / local model
More aggressive representations become possible:

- quadtree tile atlas with independently sampled levels;
- dirty-tile temporal updates;
- log-polar/foveal pyramid;
- selected vision patches/tokens;
- cached unchanged visual embeddings if model architecture allows it.

### Video transport
Use native encoder controls:

- H.264/H.265/AV1 block QP map;
- ROI rectangles;
- tile/slice controls if available;
- keyframe scheduling coordinated with ROI uncertainty.

Do not reconstruct a blurred RGB image if the hardware encoder can directly allocate bits spatially.

## Proposed fast/slow paths

### Fast path (always on)
Target sub-millisecond to a few milliseconds on mobile native code:

- IMU integration;
- sparse feature/flow tracking;
- ROI propagation;
- state filter;
- quality map at coarse tile resolution;
- send/skip decision.

### Medium path (5-30 Hz depending device)

- object/hand tracker;
- compact saliency net;
- optional lightweight monocular depth updates.

### Slow semantic path (~0.2-2 Hz)

- VLM / larger detector;
- task relevance refresh;
- re-acquisition after track loss.

## Low-level optimization targets

- Operate quality maps on encoder block/tile grid, not full resolution, unless an RGB backend requires it.
- SIMD: NEON on ARM, AVX2/AVX-512 where available.
- GPU/Metal/Vulkan compute only when transfer overhead is lower than CPU path.
- Zero-copy camera buffers: YUV/IOSurface/CVPixelBuffer/AHardwareBuffer/DMA-BUF.
- Keep foveation in native YUV planes where possible; avoid RGB conversion.
- Generate QP maps directly from coarse belief maps.
- Precompute radial/elliptic kernels; translate/warp rather than recompute.
- Fixed-point / fp16 state math where accuracy is sufficient.
- Use integral images / tile histograms for fast saliency/motion energy.
- Reuse optical-flow pyramids already produced by camera stabilization/AR stacks if platform exposes them.
- On ARCore/ARKit/OpenXR, consume existing pose/depth/tracking rather than duplicate SLAM.

## Benchmark methodology

Compression-only benchmarks are insufficient.

For each policy measure:

- transmitted bytes/s;
- decoded pixels/s;
- estimated/actual visual tokens/s;
- preprocessing CPU/GPU/NPU time;
- joules/frame where measurable;
- p50/p95 end-to-end latency;
- target coverage probability;
- ROI tracking error vs eye-tracking or annotated target;
- downstream task accuracy / hallucination / OCR recall / inspection defect recall;
- reacquisition time after fast camera motion or occlusion.

Compare against:

1. full resolution at fixed FPS;
2. global downscale;
3. fixed center crop;
4. ground-truth gaze crop upper bound;
5. software gaze predictor such as EgoGazeLite-style approach;
6. fixed-rate foveation;
7. adaptive frame selection only;
8. FoveaStream full predictive policy.

## Commercial differentiation

The defensible product is not the gaze network. It is the runtime control plane that combines sensor fusion, temporal propagation, uncertainty-aware bitrate allocation, adaptive frame scheduling, hardware encoder/model adapters and task-aware calibration under a single API.

The buyer should be able to ask:

```text
"Preserve >= 99.5% defect recall while minimizing uplink MB and VLM cost."
```

and the benchmark/calibration tooling should derive a policy for that target/device/model combination.
