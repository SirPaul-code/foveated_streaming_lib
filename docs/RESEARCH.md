# Prior art and product gap — 2026-09-14

## Executive conclusion

The broad idea already exists. Foveated XR streaming is established, encoder ROI/QP maps are standard primitives, gaze-conditioned VLM input already exists, software-only gaze prediction already exists, and 2025-2026 research is aggressively attacking temporal/frame/token redundancy.

Therefore the product **must not be positioned as a foveated blur/crop library or gaze predictor**.

The viable thesis is narrower and deeper:

> **A model- and hardware-agnostic predictive attention transport runtime that continuously estimates a future relevance distribution from asynchronous sensors/local vision, then spends pixels/bytes/tokens only where they have expected downstream task value.**

The stable abstraction is:

```text
heterogeneous evidence
(manual ROI / task cue / eye gaze / software gaze / IMU / flow / pose / depth / saliency / tracks)
        |
        v
predictive belief P(relevance at x,y,t+h | evidence <= t)
        |
        v
quality field Q(x,y,t+h) + uncertainty
        |
        +-------------------+--------------------+------------------+
        v                   v                    v
context + ROI views     encoder QP/ROI map    model token/patch policy
(VLM API)               (H26x/AV1)            (controlled model)
        |
        v
adaptive send / skip / refresh scheduler
```

I did **not** find a mature cross-platform SDK combining all of: asynchronous manual/gaze/task/depth/saliency evidence, high-rate IMU/visual propagation between low-rate semantic frames, uncertainty-aware future ROI prediction, adaptive frame transmission, native encoder control, VLM crop output, and downstream-task calibration. That is a product gap, not proof that no proprietary/internal implementation exists.

---

## Closest prior art

### 1. Gaze-conditioned MLLM input is already real

**GazeLLM (2025)** decomposes first-person video around measured gaze and reports roughly **10x fewer input pixels** while maintaining or improving task comprehension versus full-resolution input.

Source: https://arxiv.org/abs/2504.00221

**EgoGazeLite (2026-08-16)** removes the eye-tracker requirement. It predicts egocentric gaze on-device and then performs gaze-centered cropping for MLLM input. The paper reports:

- 15.7M parameters;
- 6.71 GFLOPs;
- ~21.6 ms/frame full gaze+crop pipeline on consumer accelerator hardware;
- predicted-gaze crops statistically equivalent to ground-truth-gaze crops in the evaluated downstream description setting.

Source: https://arxiv.org/abs/2608.15614

**Consequence:** "predict gaze in software and crop for the VLM" is already prior art. Our moat cannot be a gaze network.

### 2. Causal egocentric gaze prediction is becoming sophisticated

CVPRW 2026, **How Much Future Helps?**, studies causal egocentric gaze estimation and future-privileged training. It finds that causal models benefit from future-aware supervision and that optimal future context lies in a bounded multi-second range on the evaluated datasets.

Source: https://openaccess.thecvf.com/content/CVPR2026W/GAZE/html/Li_How_Much_Future_Helps_A_Controlled_Study_of_Future-Privileged_Supervision_CVPRW_2026_paper.html

Classic work already showed that egocentric gaze can be predicted from behavioral cues such as **head motion and hand location**, with fixation dynamics modeled as latent variables.

Source: https://openaccess.thecvf.com/content_iccv_2013/html/Li_Learning_to_Predict_2013_ICCV_paper.html

**Consequence:** head motion is useful evidence, but IMU/head orientation must be treated as a probabilistic cue, not a deterministic eye-gaze sensor.

### 3. Foveated processing of downstream vision tasks already exists

**Foveated Instance Segmentation (CVPR 2025)** uses gaze to limit expensive segmentation to the instance of interest in AR/VR.

Source: https://openaccess.thecvf.com/content/CVPR2025/html/Zeng_Foveated_Instance_Segmentation_CVPR_2025_paper.html

**Segment This Thing (CVPR 2025)** applies foveated tokenization to prompt-based segmentation; the paper reports roughly **24x token reduction** and about **42x latency reduction vs SAM-H**, while retaining small-object capability around the prompt.

Source: https://openaccess.thecvf.com/content/CVPR2025/papers/Schmidt_Segment_This_Thing_Foveated_Tokenization_for_Efficient_Point-Prompted_Segmentation_CVPR_2025_paper.pdf

**FAVE (2026-09-03)** explicitly separates **where to look** from **what to encode**, using a variable-resolution ViT local branch. It reports large compute savings and gains on fine-grained tasks with a very small local-token budget.

Source: https://arxiv.org/abs/2609.04392

**Consequence:** variable spatial resolution is not novel by itself. The opportunity is the real-time cross-device control plane deciding where/when/how much quality to allocate.

### 4. Streaming video token redundancy is already a major research target

**StreamingTOM (CVPR 2026)** targets causal streaming VLMs. It constrains a per-frame token budget and uses adjacent-frame changes + token saliency to reduce prefill cost rather than only compressing KV cache.

Source: https://openaccess.thecvf.com/content/CVPR2026/html/Chen_StreamingTOM_Streaming_Token_Compression_for_Efficient_Video_Understanding_CVPR_2026_paper.html

**Adaptive Greedy Frame Selection (2026)** builds a candidate pool around 1 FPS and selects frames using query relevance plus semantic coverage under a fixed frame budget.

Source: https://arxiv.org/abs/2603.20180

**Adaptive Two-Stage Visual Token Pruning (2026-08)** first removes redundant frames and then adaptively prunes tokens inside retained frames according to content redundancy.

Source: https://arxiv.org/abs/2608.03112

**Consequence:** fixed-rate 1 FPS sampling is itself a weak baseline. FoveaStream should decide whether a frame should exist at all, not only how it should be spatially compressed.

### 5. Temporal propagation of high-resolution foveal information already exists

**Cross-Resolution Flow Propagation for Foveated Video Super-Resolution (WACV 2023)** explicitly retains past high-resolution foveal context and propagates information through time, partly to handle noisy gaze coordinates.

Source: https://openaccess.thecvf.com/content/WACV2023/papers/Lee_Cross-Resolution_Flow_Propagation_for_Foveated_Video_Super-Resolution_WACV_2023_paper.pdf

**Consequence:** temporal memory must be first-class. Independent frame-by-frame crops are architecturally wrong for low-rate transmission.

### 6. Event vision proves how far sparse sensing can go

**Reading in the Dark with Foveated Event Vision (CVPRW 2025)** combines gaze and an event stream for OCR and reports around **98% bandwidth reduction** in its foveated event representation, with very large savings versus wearable RGB video in the tested setup.

Source: https://openaccess.thecvf.com/content/CVPR2025W/EventVision/html/Brander_Reading_in_the_Dark_with_Foveated_Event_Vision_CVPRW_2025_paper.html

**Egocentric Event-Based Vision for Ping Pong Ball Trajectory Prediction (CVPRW 2025)** uses gaze-foveated event processing and reports a 10.81x reduction factor on collected trajectories, with millisecond-class latency in a high-speed task.

Source: https://openaccess.thecvf.com/content/CVPR2025W/EventVision/html/Alberico_Egocentric_Event-Based_Vision_for_Ping_Pong_Ball_Trajectory_Prediction_CVPRW_2025_paper.html

**Consequence:** the long-term ceiling is not merely compressed frames. The abstraction should be able to consume asynchronous/event-like evidence and eventually output sparse updates.

### 7. Visual + inertial fusion is well-established

RGB-D + inertial scene-flow work shows the value of tightly coupling image/depth motion with IMU rather than relying on either alone.

Source: https://openaccess.thecvf.com/content/CVPR2024W/VISOD/html/Cerezo_Camera_Motion_Estimation_from_RGB-D-Inertial_Scene_Flow_CVPRW_2024_paper.html

**Consequence:** IMU is best used for low-latency ego-motion propagation and prediction between image updates. Translation needs depth/inverse-depth or a 3-D track; pure gyro rotation is the easy part.

---

## XR / encoder prior art

### XR foveated streaming

- NVIDIA CloudXR documents Apple's visionOS Foveated Streaming integration.
- Tobii markets foveated transport for split-rendering XR.

These optimize human visual display quality, not generic machine-vision task utility.

### Encoder ROI/QP primitives

Production video stacks already expose spatial bit-allocation primitives:

- Android MediaCodec: platform-dependent QP offset map / rectangle controls;
- NVIDIA NVENC: emphasis / QP maps at coding-block granularity;
- FFmpeg/x264/x265/VAAPI paths: ROI/QP metadata depending encoder.

Therefore a production video backend should map `Q` directly to native encoder controls whenever possible instead of reconstructing a blurred RGB frame first.

### Open-source foveated encoders

- `lukehsiao/fvideo`: Rust low-latency foveated H.264 research prototype using EyeLink/libx264, targeted at an older Ubuntu/FFmpeg environment.
- `soenning-ai/fnvision`: 2026 Python foveated-vision encoder for agents/robotics with gaze dynamics and calibration. Its README reports a representative 86.2% pixel reduction example.

This means a generic "foveated image Python package" is commercially weak.

---

## What is actually worth building

### Predictive relevance state, not a point gaze coordinate

Maintain a state distribution, e.g.:

```text
x = [u, v, du/dt, dv/dt, log_scale, dlog_scale/dt, inverse_depth, ...]
P = covariance / uncertainty
mode in {fixation, pursuit, saccade, lost}
```

The output support is expanded according to uncertainty and downstream latency horizon.

### High-rate local propagation, low-rate semantics

Example target architecture:

```text
IMU              100-1000 Hz
camera/local flow    30-120 Hz
object/hand track      5-30 Hz
small saliency/depth   2-30 Hz
VLM/task semantics    0.2-2 Hz
uplink frames          adaptive, often <= 1 Hz
```

A 1 FPS VLM feed does **not** imply 1 Hz internal vision. The semantic ROI can be propagated at local camera/IMU rate and only periodically corrected.

### Latency-aware prediction

The relevant target is where attention/task support will be at:

```text
t_capture + preprocessing + encode + network + queue + model ingest
```

not where it was when the source frame was captured.

### Uncertainty-aware bandwidth

Use covariance to control the transmitted support:

```text
ROI_safe = ROI_task + k * sqrt(P_xy) + latency_motion_corridor
```

A confident tracker spends very few pixels. A lost tracker automatically widens context rather than silently cropping the object.

### Adaptive frame scheduler

Send a new expensive frame only when innovation is high enough:

```text
innovation =
  w_pose  * camera_pose_novelty +
  w_roi   * predicted_ROI_error +
  w_flow  * residual_object_motion +
  w_scene * scene_change +
  w_sem   * semantic_uncertainty +
  w_age   * staleness
```

with hard maximum staleness and task-trigger overrides.

This is potentially more valuable than spatial foveation because **zero bytes** is cheaper than a compressed frame.

---

## Candidate technical moat

The pieces individually are known. The moat has to be integration + control quality:

1. **Asynchronous sensor fusion** across different clocks/rates.
2. **Predict-to-consumption-time** instead of predict-to-current-frame.
3. **Uncertainty-to-bandwidth conversion** with bounded miss probability.
4. **Temporal propagation** of semantic relevance, not independent frame inference.
5. **Adaptive send/skip scheduling** with a task-loss constraint.
6. **Zero-copy platform adapters** and direct encoder QP maps.
7. **Model-specific pixel/token cost adapters** for VLM APIs and local models.
8. **Closed-loop calibration**: choose policy parameters to minimize dollars/bytes/energy while maintaining measured task accuracy.
9. **Domain packs** for inspection/OCR/AR/robotics that encode task-specific priors.
10. **Benchmark corpus + policy optimizer**. The data mapping budget to downstream accuracy may become more defensible than the foveation kernel itself.

---

## Research formulation

The actual optimization problem should be framed as constrained control / rate-distortion-for-task:

```text
min_pi   E[L_task(pi)]
subject to
         E[bytes/s]  <= B
         E[tokens/s] <= T
         E[energy/s] <= E
         p(target outside transmitted support) <= epsilon
         p95 latency <= L
```

or the Lagrangian:

```text
J(pi) = L_task
      + lambda_b * bytes
      + lambda_t * visual_tokens
      + lambda_e * energy
      + lambda_l * latency
      + lambda_m * miss_risk
```

The runtime exposes the policy; the benchmark/calibration suite estimates the lambdas / operating point for a device-model-task tuple.

---

## Decision

**GO, but only on the predictive attention transport thesis.**

Do not sell:

- radial blur;
- a gaze cropper;
- a single gaze model;
- a generic image-resize library.

Sell:

> **"Preserve a target downstream vision accuracy while minimizing transmitted bytes, visual tokens, latency and edge energy."**

That is a measurable enterprise SDK claim and is materially broader/deeper than existing gaze-crop work.
