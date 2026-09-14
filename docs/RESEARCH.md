# Prior art and product gap — 2026-09-14

## Conclusion

The broad idea already exists: foveated XR streaming is established, encoder ROI/QP maps are standard primitives, and 2026 VLM research is applying foveation/adaptive pixel budgets directly to vision-language models.

The viable product is therefore not radial blur. The gap worth targeting is a **model- and hardware-agnostic edge SDK** that converts heterogeneous attention signals into one quality field `Q(x,y,t)` and emits the representation the downstream path can exploit:

- same-size entropy-reduced frame;
- low-resolution global context + high-resolution ROI views;
- hardware encoder QP/ROI map;
- later, model-specific patch/token selection.

I did not find a mature cross-platform SDK combining manual ROI override, gaze/task/depth/saliency evidence, IMU latency compensation, arbitrary quality maps, VLM crop output, encoder maps, and task-aware benchmarking. This is not proof that no proprietary/internal implementation exists.

## Prior art

### XR foveated streaming

- NVIDIA CloudXR documents Apple's visionOS Foveated Streaming framework, which uses eye tracking to preserve focus-region quality while reducing peripheral cost: https://docs.nvidia.com/cloudxr-sdk/latest/usr_guide/foveated_streaming/index.html
- Tobii markets "Foveated transport" for split-rendering XR: https://www.tobii.com/solutions/extended-reality/foveation-technology

These target human display streaming rather than generic machine-vision/VLM preprocessing.

### Encoder ROI/QP primitives

- Android MediaCodec exposes QP offset map/rectangle parameters on recent APIs: https://developer.android.com/reference/android/media/MediaCodec
- NVIDIA NVENC exposes emphasis/QP maps at block/macroblock granularity: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/
- FFmpeg/x264/x265/VAAPI paths support ROI/QP metadata.

Therefore a production video backend should map `Q` to native encoder controls instead of always reconstructing blurred frames.

### Open-source foveated encoders

- `lukehsiao/fvideo`: Rust low-latency foveated H.264 research prototype using EyeLink/libx264, tested on older Ubuntu/FFmpeg combinations: https://github.com/lukehsiao/fvideo
- `soenning-ai/fnvision`: 2026 Python foveated-vision encoder for agents/robotics with gaze dynamics and calibration. Its README reports a representative 86.2% pixel reduction / 7.25x compression example: https://github.com/soenning-ai/fnvision

This means "Python library that foveates around gaze" is already occupied.

### VLM adaptive pixels/tokens

- LLMind (CVPR 2026) presents training-free bio-inspired adaptive visual sampling for VLMs and reports strong retained performance at very small pixel budgets: https://openaccess.thecvf.com/content/CVPR2026/html/Debnath_LLMind_Bio-inspired_Training-free_Adaptive_Visual_Representations_for_Vision-Language_Models_CVPR_2026_paper.html
- FAVE (2026-09-03) explicitly separates where to look from what to encode and uses variable-resolution vision encoding: https://arxiv.org/abs/2609.04392
- FocusLLaVA uses coarse-to-fine visual token compression: https://arxiv.org/abs/2411.14228

## Product thesis

The stable abstraction is:

```text
Attention evidence -> Q(x,y,t) -> transport/model-specific actuator
```

Attention evidence can be explicit UI point/rect, eye tracker, task detector, prompt-localizer, depth/autofocus, saliency/motion, center prior, and temporal prediction. IMU should not be marketed as gaze estimation; it can predict how an already selected world direction moves in the image as the device rotates.

Outputs should include RGB foveation, context+ROI views, encoder QP maps, and eventually model-specific patch/token selection.

## What can become a moat

- low-latency composition of asynchronous attention sources;
- task-accuracy-vs-budget auto-calibration;
- zero/low-copy native adapters;
- device-specific encoder capability detection/fallback;
- latency prediction so ROI is correct at encode/inference time;
- presets/benchmarks for inspection, OCR, AR and robotics;
- model adapters that know how dimensions/crops translate to visual tokens/cost.

## Decision

**GO only with this narrower thesis.** A generic peripheral blur/downsample library is not commercially differentiated. An attention-budget router for machine vision potentially is.
