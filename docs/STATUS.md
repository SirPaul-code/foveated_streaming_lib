# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

Build a provider-agnostic adaptive relevance transport SDK that can sit between an existing camera/frame source and an existing encoder, transport, model or callback.

Stable product boundary:

```text
existing source
    -> timestamped frame + optional relevance evidence
    -> persistent relevance / predictive multi-ROI state
    -> one or more transport/encoder actuators
    -> existing consumer
```

Do not turn core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

Feature branch: `docs/readme-adaptive-video-quickstart`

Base before this branch: `main` at `e4ea068cbbde82035c2b586e3682d0c64121b676`.

This branch adds a complete README rewrite, arbitrary-video benchmark quickstart, exact-count logical tile planning, visual showcase generation and CI coverage for the new CLIs.

At the time this handoff was written the branch had not yet been merged. Check the PR/CI state before claiming these additions are on `main`.

Package version remains `0.3.0`.

## Previously merged adaptive stack

Already on main before this branch:

- causal drop-in `FoveaStreamTransform` middleware;
- bounded `RealtimeBridge` with latest-frame semantics;
- multi-source relevance `EvidenceBus`;
- low-resolution proposal analysis;
- persistent predictive multi-ROI tracking;
- latency-aware future ROI prediction;
- hard total ROI-area budget;
- same-size foveated frame output;
- context + multiple high-resolution ROI views;
- temporal ROI cache;
- opt-in background tile cache;
- layered context + changed-ROI payload;
- packed one-image context+ROI atlas;
- portable encoder delta-QP hints;
- adaptive bitrate/pixel-budget controller;
- SEND/SKIP recommendation;
- native Rust control-plane primitives.

## New logical tile planner

### Python: `python/foveastream/tiles.py`

The logical tile planner is a new actuator derived from the existing continuous relevance field.

It is intentionally separate from codec-native QP blocks.

Configuration:

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

- `target_tiles` is exact, including non-square counts such as 10, 25, 100, 137 or 400;
- tiles cover the whole frame and are near-rectangular with layout following source aspect ratio;
- per-tile relevance comes from the continuous quality/relevance map;
- aggregation modes: `max`, `p90`, `mean`;
- built-in degradation curves: `linear`, `smoothstep`, `gaussian`, `exponential`, `power`;
- Python callers may provide a custom `distance -> quality` degradation callback;
- each tile receives quality, resolution scale and delta-QP recommendation;
- `effective_pixel_fraction` estimates multi-resolution raster cost as `sum(area * scale^2)`;
- `rasterize_tile_plan(...)` converts the discrete tile plan back to an HxW field;
- `render_tile_plan(...)` produces a visual debug overlay.

Important invariant:

```text
continuous relevance field
    +--> logical TilePlan       exact N transport/application tiles
    +--> encoder QP map         codec block grid, e.g. 16x16
```

A 100-tile logical plan does not mean the codec has only 100 coding blocks. Both actuators may coexist.

### Native Rust: `src/tiles.rs`

Exports:

- `TileCurve`;
- `TileAggregation`;
- `TilePlannerConfig`;
- `TileDecision`;
- `TilePlan`;
- `plan_tiles(...)`.

Rust currently provides built-in curves. Arbitrary user callback degradation is a Python/API-level feature; native hosts can post-process or add their own adapter policy if needed.

## Arbitrary-video quickstart

`examples/adaptive_transport.py` now accepts either a live camera or an ordinary video file:

```powershell
python examples\adaptive_transport.py --video example3.mp4 --preset aggressive
```

Tile visualization is enabled by default with 100 Gaussian logical tiles and may be configured through:

```text
--tiles
--tile-curve
--tile-strength
--tile-aggregation
--tile-min-quality
--tile-min-scale
--no-tile-overlay
```

The example prints mean processing time, effective FPS, ROI count, temporal reuse, layered pixel fraction and logical-tile effective pixel fraction.

## One-command benchmark suite

New:

`bench/benchmark_suite.py`

Recommended command:

```powershell
python bench\benchmark_suite.py example3.mp4 --preset aggressive --tiles 100 --tile-curve gaussian
```

It runs both:

1. same-encoder H.264 baseline vs foveated RGB benchmark;
2. adaptive transport benchmark.

Combined report includes:

- source hash and metadata;
- H.264 bytes saved;
- context+ROI pixel saving;
- codec processing time;
- adaptive mean/p50/p95/p99 processing time;
- active and changed ROI counts;
- temporal ROI reuse fraction;
- layered payload pixel fraction;
- atlas pixel fraction;
- portable delta-QP statistics;
- controller state;
- logical tile count/curve;
- logical-tile effective pixel fraction and mean tile quality/QP.

## Showcase generator

New:

`examples/generate_showcase.py`

Real input:

```powershell
python examples\generate_showcase.py example3.mp4 --outdir output\showcase_example3
```

Deterministic documentation input:

```powershell
python examples\generate_showcase.py --synthetic
```

It produces nine MP4/GIF views:

1. multi-ROI relevance;
2. 10 tiles / linear;
3. 25 tiles / smoothstep;
4. 100 tiles / Gaussian;
5. 100 tiles / exponential;
6. 400 tiles / Gaussian;
7. encoder delta-QP map;
8. temporal ROI SEND vs REUSE;
9. packed context+changed-ROI atlas.

`.github/workflows/showcase.yml` regenerates documentation GIFs from the deterministic synthetic scene after relevant changes land on `main`, removes MP4 intermediates and commits only `docs/assets/showcase/*.gif` plus `manifest.json`.

## README

`README.md` was rewritten from scratch around current v0.3 behavior.

It now documents:

- pull/install/test commands for `example3.mp4`;
- all current actuators;
- ROI semantics and evidence sources;
- realtime middleware and latest-frame queue;
- temporal/background caching;
- layered payload and atlas;
- exact-count logical tiles;
- built-in and custom degradation curves;
- logical tiles vs codec QP blocks;
- adaptive control;
- existing measured H.264 benchmark numbers;
- benchmark metric definitions;
- custom-video benchmark recipes;
- visual showcase GIFs;
- native Rust usage;
- current implementation boundary and remaining platform work;
- commercial-contact licensing.

## Existing authoritative real-video codec numbers

Checked-in real-video benchmark remains authoritative for the current same-encoder H.264 results:

- `example1`, aggressive: **71.30%** H.264 saving and **96.26%** context+ROI pixel saving;
- `example2`, aggressive: **31.34%** H.264 saving and **84.08%** context+ROI pixel saving.

Those are reference-video measurements, not universal task-quality or target-device latency claims.

## Verification on this branch

Tests added/updated:

- Python exact requested tile count;
- relevant tile gets more quality, resolution and better QP;
- custom degradation callback;
- effective pixel fraction/rasterization bounds;
- Rust exact tile count;
- Rust relevant-vs-irrelevant tile quality/QP behavior;
- CLI syntax/help smoke checks for adaptive video, showcase and benchmark suite.

Before merge this branch must pass:

- Python tests on Ubuntu;
- Rust `cargo test --all-targets` + release build on Ubuntu;
- Rust test/release on Windows;
- Rust test/release on macOS.

## Exact implementation boundary

Valid to claim after this branch merges:

- all previously merged adaptive transport capabilities;
- exact-count logical tile planning in Python and Rust;
- built-in spatial degradation curves;
- Python custom tile degradation function;
- tile quality/resolution/QP recommendations;
- tile effective-pixel estimate;
- tile preview/rasterization;
- arbitrary-video adaptive preview;
- one-command combined benchmark suite;
- reproducible nine-mode visual showcase generator.

Still adapter/platform work:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI QP wiring;
- actual tiled WebRTC/RTP/GStreamer packetization;
- actual codec/transport application of logical tile resolution scale;
- native NV12/YUV full hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- CameraX/AVFoundation/OpenXR direct adapters;
- world-locked background cache for freely moving cameras;
- stateful C ABI for the full adaptive runtime;
- target-device p50/p95/energy characterization;
- broad downstream task-quality validation.

A tile plan is policy metadata until a downstream adapter consumes its scale/QP recommendations.

A portable QP map is policy metadata until a concrete encoder consumes it.

## Next engineering priorities

### P0 — one concrete encoder adapter

Translate `EncoderSpatialHints` to one real encoder family and compare against global CRF/QP reduction at equivalent downstream task quality.

### P1 — one concrete tiled transport adapter

Consume `TilePlan` and actually transmit/encode tiles at their requested resolution/quality instead of only visualizing/planning them.

### P2 — native NV12/YUV and low-copy buffers

Remove RGB round trips from production hot paths.

### P3 — downstream task-quality benchmark

Compare full resolution, global quality reduction, global downscale, same-size foveation, layered context+ROI, temporal reuse, logical tiles and encoder QP at equivalent downstream model quality.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.
