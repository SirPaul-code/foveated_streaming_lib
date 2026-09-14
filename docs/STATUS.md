# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a production-grade, provider-agnostic predictive relevance transport SDK for machine-vision/VLM workloads. The runtime should reduce bytes, pixels, visual tokens, requests and edge compute while preserving measurable downstream task quality.

The stable product boundary is:

```text
arbitrary timestamped frame source + arbitrary relevance evidence
        -> persistent future relevance / multi-ROI state
        -> quality field + payload alternatives + send/skip decision
        -> arbitrary transport / encoder / model consumer
```

The project must not become a Gemini/OpenAI/WebRTC-specific client or a one-off blur filter.

## Current branch / PR at this checkpoint

Feature branch: `feat/universal-streaming-multiroi`

PR: `#2 Add universal streaming runtime and multi-ROI tracking`

After merge, use `main` and check `git log -1 --oneline` for the exact final merge SHA.

## Implemented native Rust core

### Existing spatial/predictive primitives

- normalized points/ROIs;
- continuous quality-field generation;
- same-size foveated RGB generation;
- context + high-resolution ROI views;
- per-block signed QP delta map;
- focus-source fusion;
- predictive attention state with covariance/confidence;
- optical-flow correction;
- gyro camera-ray propagation;
- future uncertainty-dilated ROI;
- adaptive send/skip scheduler;
- C ABI for existing low-level foveation/QP primitives.

### New multi-ROI runtime

`src/roi.rs`:

- `RoiProposal` is class-agnostic relevance evidence;
- `MultiRoiTracker` maintains zero/one/many simultaneous ROI tracks;
- overlapping proposal deduplication;
- temporal association via IoU / center distance / optional track hint;
- rectangle velocity estimation and prediction;
- stale/confidence decay;
- uncertainty/motion margin expansion;
- confidence × priority ranking;
- `max_tracks` cap;
- normalized total ROI pixel budget.

`src/streaming.rs`:

- `FrameInput`;
- `StreamRuntimeConfig`;
- named `balanced`, `aggressive`, `extreme` presets;
- `StreamRuntime::process_rgb8(...)`;
- `ProcessResult`;
- generic `StreamSink` trait;
- per-frame quality floor tied to uncertainty;
- same-size foveated output;
- context + multiple ROI views;
- QP map;
- scheduler decision.

The Rust runtime is causal and synchronous. The host owns threads/queues/networking.

## Implemented Python reference/integration layer

`python/foveastream/streaming.py`:

- `RoiProposal`;
- `MultiRoiTracker`;
- `ClassAgnosticMotionRoiDetector`;
- `FoveaStreamRuntime`;
- `StreamRuntimeConfig.preset(...)`;
- `ProcessResult`;
- `CallbackSink`;
- `run_stream(...)`;
- Python adaptive scheduler mirror.

The residual-motion detector compensates global camera motion and can produce multiple connected-component proposals. It is only a fallback proposal source. It is NOT the semantic definition of ROI.

External applications should inject better evidence when available: task targets, UI selections, gaze, anchors, detectors, depth, OCR regions, hand interaction, model feedback, etc.

## Multi-ROI design decision

Do not hardcode faces, cars, people or any particular object category in core.

An ROI means only:

> spatial support whose loss would be disproportionately harmful to the current downstream task.

Multiple sources may propose multiple regions. The tracker fuses/deduplicates them and keeps several active foveae under a budget.

This avoids the failure mode where one "best" ROI destroys a second simultaneously important object/region.

## Real-video benchmark / demo

`bench/real_video_visualization.py` now supports multiple simultaneous automatic ROIs.

Reference command:

```bash
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive
```

Additional controls:

```bash
--max-rois 10
--roi-budget-fraction 0.30
--peripheral-downscale 20
--context-scale 0.12
--falloff-strength 5.2
--quality-floor 0.005
--uncertainty-floor 0.015
```

The aggressive preset currently uses:

- peripheral downscale: 16x;
- context scale: 0.15;
- max ROIs: 6;
- ROI pixel budget: 0.22 normalized frame area;
- lower peripheral quality floor than balanced.

The visualization draws every active ROI and records mean/max active ROI counts in benchmark JSON.

## Installation / dependencies

Canonical Python metadata remains in `pyproject.toml`.

Convenience files now exist:

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

FFmpeg remains a system dependency for MP4 benchmark encoding; setup scripts check/install it where supported.

## Agent integration docs

Coding agents should start from root `AGENTS.md`, then read:

- `docs/AGENT_INTEGRATION.md`;
- `docs/REALTIME_STREAMING_STATUS.md`;
- this file;
- `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`.

`AGENTS.md` explicitly documents provider boundaries, proposal sources, output representations, multi-rate processing, test commands and handoff rules.

## Examples

- `examples/live_webcam.py` — live camera -> aggressive multi-ROI runtime -> preview;
- `examples/custom_sink_adapter.py` — minimal template showing how an existing workflow/model/transport consumes `ProcessResult` without changing core.

## License

Root `LICENSE` is a proprietary evaluation/non-commercial R&D license.

Commercial production, paid service, OEM, redistribution and other commercial use require a separate written commercial license.

Commercial contact: `p.duplinsky@gmail.com`

## Realtime readiness: exact claim

Implemented and valid to claim:

- causal frame-by-frame processing;
- persistent multi-ROI tracking;
- transport/provider-agnostic push-frame runtime;
- generic sink/callback contract;
- output alternatives for full foveated frame, context+ROIs and QP maps;
- local analysis rate decoupled conceptually from output/model rate;
- Python live source example;
- native Rust runtime contract.

Not yet valid to claim as universally production-complete:

- CameraX/AVFoundation/OpenXR direct adapters;
- native YUV/NV12 hot path;
- zero-copy platform buffers;
- hardware MediaCodec/NVENC/VideoToolbox/VAAPI ROI wiring;
- concrete WebRTC/RTP/GStreamer/provider SDK adapters;
- target-device p50/p95/energy measurements;
- production downstream task-accuracy validation under aggressive settings.

These are edge adapters/performance work, not reasons to couple the core to a provider.

## CI / verification

CI is configured to run:

- Rust tests/build on Ubuntu, Windows and macOS;
- Python tests on Ubuntu.

At the time this handoff text was written, PR #2 was still open and its latest CI result had not yet been recorded here. Before declaring this checkpoint final, inspect PR #2 workflow results and fix failures. Update this section after merge.

## External prior-art sanity

Do not claim multi-ROI or ROI-QP encoding as novel by itself. Existing encoders and research already support spatial QP/ROI policies, and recent work includes multi-foveated/multi-target acquisition and token-efficient egocentric attention. Differentiation remains the portable predictive control/runtime layer combining evidence fusion, persistent uncertainty-aware ROI state, multiple output actuators and adaptive scheduling.

## Next engineering priorities

### P0 — verification / API stability

1. Get PR #2 green on Rust Linux/Windows/macOS and Python CI.
2. Add semantic-versioned API tests for `RoiProposal`, `ProcessResult` and presets.
3. Add benchmark regression fixtures for zero, one and multiple ROIs.
4. Add malformed timestamp/frame tests to native and Python layers.

### P1 — timestamp/evidence bus

1. Replace ad-hoc proposal calls with a timestamped multi-rate `Evidence` bus.
2. Add source clock mapping, TTL, covariance and explicit source IDs.
3. Support point/rect/mask/pose/flow evidence consistently.
4. Add explicit occlusion/reacquisition state and stronger track identity handling.

### P2 — production platform adapters

1. Android Camera2/CameraX + AHardwareBuffer + MediaCodec.
2. Apple CVPixelBuffer/IOSurface/Metal + AVFoundation.
3. Linux V4L2/GStreamer/DMA-BUF.
4. NVENC/VAAPI/other direct ROI-QP actuator adapters where available.
5. Native YUV/NV12 path; avoid RGB round trips.

### P3 — relevance sources

1. cheap saliency proposal adapter;
2. hand/object interaction adapter;
3. software gaze adapter;
4. task detector / prompt localizer adapter;
5. downstream model feedback adapter;
6. multi-hypothesis masks where rectangles are too crude.

### P4 — benchmark science

Compare full resolution, global downscale, fixed crop, single ROI, multi ROI, adaptive frame selection and full predictive FoveaStream using:

- bytes/s;
- decoded/model pixels/s;
- visual tokens / billed cost where measurable;
- CPU/GPU/NPU time and energy;
- end-to-end latency p50/p95;
- important-region coverage/miss rate;
- downstream task quality.

## Product win condition

> lower total bytes/tokens/latency/energy at equivalent downstream task quality and bounded important-region miss probability.

Anything less is an image-processing demo, not the commercial SDK target.
