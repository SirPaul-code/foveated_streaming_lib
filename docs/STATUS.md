# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a provider-agnostic adaptive relevance transport SDK that can sit between an existing camera/frame source and an existing encoder, transport, model or callback.

Stable boundary:

```text
source
  -> timestamped frame + optional relevance evidence
  -> persistent predictive multi-ROI relevance state
  -> one or more transport/encoder actuators
  -> existing consumer
```

Do not turn core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

PR #6 `Add configurable relevance tiles, full README and visual showcase` is merged.

- squash merge: `9baaec5bad51e5501fef01b9851678684c833d8d`
- showcase asset generation commit: `1afbe6480e9c66ee624c8da338a056fa3e479f78`
- `AGENTS.md` tile/showcase handoff update: `140cc89476192c80a647bb935846d202468af99d`
- package version: `0.3.0`

PR #6 CI passed before merge:

- Python tests + CLI smoke checks — PASS;
- Rust tests + release build on Ubuntu — PASS;
- Rust tests + release build on macOS — PASS;
- Rust tests + release build on Windows — PASS.

Showcase workflow run `34895753871` also passed and committed all generated documentation GIFs to `main`.

## Core/adaptive stack now on main

Implemented:

- causal frame-by-frame middleware;
- `FoveaStreamTransform` same-size drop-in output;
- bounded `RealtimeBridge` with latest-frame semantics;
- `EvidenceBus` multi-source relevance fusion with TTL;
- low-resolution proposal analysis;
- persistent predictive multi-ROI tracking;
- latency-aware future ROI prediction;
- hard total ROI-area budget;
- context + multiple high-resolution ROI views;
- temporal high-resolution ROI cache;
- opt-in image-space background tile cache;
- layered context + changed-ROI payload;
- packed one-image context+ROI atlas;
- portable encoder delta-QP hints;
- adaptive bitrate/pixel-budget controller;
- SEND/SKIP recommendation;
- exact-count logical tile planner in Python and Rust;
- arbitrary-video preview/benchmark workflows;
- reproducible nine-mode visual showcase.

## Logical tile planner

Python: `python/foveastream/tiles.py`

Native: `src/tiles.rs`

Canonical Python config:

```python
TilePlannerConfig(
    target_tiles=100,
    curve="gaussian",
    curve_strength=3.0,
    aggregation="max",
    min_quality=0.04,
    min_resolution_scale=0.125,
    fovea_qp_delta=-4,
    periphery_qp_delta=18,
)
```

Properties:

- exact requested tile count, including 10/25/100/137/400;
- full-frame coverage with near-rectangular layout following frame aspect ratio;
- aggregation modes: `max`, `p90`, `mean`;
- built-in curves: `linear`, `smoothstep`, `gaussian`, `exponential`, `power`;
- Python custom `distance -> fidelity` callback;
- per-tile relevance, distance, quality, resolution scale and delta-QP recommendation;
- `effective_pixel_fraction = sum(tile_area * resolution_scale^2)` estimate;
- rasterization/debug visualization helpers.

Important invariant:

```text
continuous relevance field
    +--> logical TilePlan       exact N application/transport tiles
    +--> encoder QP map         codec block grid, e.g. 16x16
```

These are separate actuators and may coexist.

A logical tile plan is policy metadata until a downstream adapter actually transmits/rasterizes/encodes the tiles at requested scale/quality.

A QP map is policy metadata until a real encoder consumes it.

## Arbitrary-video usage

Full benchmark:

```powershell
python bench\benchmark_suite.py example3.mp4 --preset aggressive --tiles 100 --tile-curve gaussian
```

Adaptive preview:

```powershell
python examples\adaptive_transport.py --video example3.mp4 --preset aggressive --tiles 100 --tile-curve gaussian
```

Generate all visual examples from the user's own video:

```powershell
python examples\generate_showcase.py example3.mp4 --outdir output\showcase_example3
```

## Benchmark suite

`bench/benchmark_suite.py` combines:

1. same-decoded-frames, same-encoder H.264 baseline vs FoveaStream RGB;
2. adaptive transport benchmark.

Outputs include:

- source hash/metadata;
- H.264 byte saving;
- context+ROI pixel saving;
- codec processing time;
- adaptive mean/p50/p95/p99;
- ROI counts and temporal reuse;
- layered/atlas pixel fraction;
- QP hint statistics;
- controller state;
- logical tile count/curve;
- logical-tile effective pixel fraction and mean quality/QP.

Existing authoritative codec measurements remain:

- `example1` aggressive: **71.30% H.264 saving**, **96.26% context+ROI pixel saving**;
- `example2` aggressive: **31.34% H.264 saving**, **84.08% context+ROI pixel saving**.

These are reference-video measurements, not universal task-quality/device-latency claims.

## Visual showcase

`examples/generate_showcase.py` supports either a real video or a deterministic synthetic documentation scene.

The docs showcase currently contains:

1. `docs/assets/showcase/01_multi_roi.gif`
2. `docs/assets/showcase/02_tiles_10_linear.gif`
3. `docs/assets/showcase/03_tiles_25_smoothstep.gif`
4. `docs/assets/showcase/04_tiles_100_gaussian.gif`
5. `docs/assets/showcase/05_tiles_100_exponential.gif`
6. `docs/assets/showcase/06_tiles_400_gaussian.gif`
7. `docs/assets/showcase/07_qp_map.gif`
8. `docs/assets/showcase/08_temporal_reuse.gif`
9. `docs/assets/showcase/09_roi_atlas.gif`
10. `docs/assets/showcase/manifest.json`

`.github/workflows/showcase.yml` regenerates these from the deterministic scene after relevant main-branch changes and commits only GIFs + manifest.

## README / agent handoff

`README.md` is now the complete user-facing source for:

- installation/pull commands;
- integration modes;
- all actuators;
- tile count/curves/custom degradation;
- tiles vs codec QP blocks;
- realtime queue semantics;
- current benchmark numbers;
- arbitrary-video benchmark recipes;
- visual showcase;
- native usage;
- implementation boundary/licensing.

`AGENTS.md` explicitly mirrors the important integration invariants for another coding agent.

## Still adapter/platform work

Do NOT claim these as complete yet:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI spatial-QP integration;
- actual WebRTC/RTP/GStreamer logical-tile packetization;
- actual transport/codec application of `TilePlan.resolution_scale`;
- full native NV12/YUV hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- CameraX/AVFoundation/OpenXR direct camera adapters;
- world-locked background cache for freely moving cameras;
- stateful C ABI for the high-level adaptive runtime;
- target-device p50/p95/energy matrix;
- broad downstream task-quality validation.

## Next priorities

### P0 — concrete encoder adapter

Translate `EncoderSpatialHints` into one real hardware/software encoder API and compare against global CRF/QP reduction at equivalent downstream task quality.

### P1 — concrete tiled transport adapter

Consume `TilePlan` and truly transmit/encode tiles at their requested resolution/quality rather than only planning/visualizing them.

### P2 — native NV12/YUV + low-copy buffers

Remove RGB round trips from production hot paths.

### P3 — downstream task-quality benchmark

Compare full resolution, global quality reduction, global downscale, same-size foveation, layered context+ROI, temporal reuse, logical tiles and encoder QP at equivalent downstream model quality.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.
