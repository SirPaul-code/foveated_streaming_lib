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

Feature branch: `feat/full-docs-benchmark-gallery`

Base main before this branch: `5f82efc524ffec124aae77ac58b4809a4a624f7e`.

Previous PR #6 (`Add configurable relevance tiles, full README and visual showcase`) is already merged and passed Python + Rust CI on Ubuntu/macOS/Windows. The generated nine-mode showcase GIFs are already on main.

This branch adds:

- advanced custom logical-tile policy hooks;
- reference realization of logical tile resolution loss;
- a curated all-modes arbitrary-video benchmark gallery;
- a complete Python API reference;
- a complete custom tile-policy guide;
- a benchmark-gallery guide;
- copyable custom policy examples;
- new tests and CLI smoke coverage;
- a rebuilt README that links the documentation set and makes the all-modes gallery the recommended first workflow.

Package version remains `0.3.0`.

Do not claim this branch is merged until its PR/CI state has been checked.

## Core/adaptive stack already on main

Implemented before this branch:

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

## Advanced logical tile policy API on this branch

Python: `python/foveastream/tiles.py`

The existing simple API remains valid:

```python
TilePlanner(
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
)
```

New extension context:

```python
TilePolicyContext
```

It exposes:

```text
index / row / column
x / y / w / h
center_x / center_y / area_fraction
relevance / distance
mean_relevance / max_relevance / p90_relevance
map_width / map_height
```

New custom hooks:

```python
TilePlanner(
    config,
    degradation_fn=distance_to_quality,                 # simple
)

TilePlanner(
    config,
    quality_fn=context_to_quality,                      # advanced quality
    resolution_fn=context_quality_to_resolution_scale, # optional
    qp_fn=context_quality_to_delta_qp,                  # optional
)
```

Rules:

- `degradation_fn` and `quality_fn` are mutually exclusive;
- raw quality is clipped to `[0,1]`;
- configured `min_quality` remains a floor after custom quality;
- custom resolution is clamped to `[min_resolution_scale,1]`;
- generic custom QP is clamped to signed int8;
- concrete codec adapters still need to enforce native codec limits.

New helper:

```python
apply_tile_plan(frame_rgb, plan)
```

It downscales each logical tile to its recommended `resolution_scale` and upsamples it back into the same-size output frame. It is a reference/visual implementation, not a production tiled transport.

Copyable policies:

`examples/custom_tile_policy.py`

- `gentle_distance`;
- `focus_cliff`;
- `context_aware_quality`;
- `stepped_resolution`;
- `tiered_qp`.

## All-modes benchmark gallery

New:

`bench/benchmark_gallery.py`

Recommended command:

```powershell
python bench\benchmark_gallery.py example.mp4
```

Standard matrix creates:

```text
output/gallery/example/
  INDEX.md
  report.json
  tile_policies.csv
  tile_matrix_runtime.json

  01_presets/          balanced/aggressive/extreme full codec+transport benchmarks
  02_core_actuators/   existing 9-mode showcase generated from the user's video
  03_tile_counts/      10/25/100/400 tile variants
  04_tile_curves/      linear/smoothstep/gaussian/exponential/power
  05_tile_aggregation/ mean/p90/max
  06_custom_policies/  five copyable custom callback policies
```

`--matrix full` additionally creates:

```text
07_strength_sweep/
08_min_scale_sweep/
```

User-provided callbacks may be loaded without editing FoveaStream:

```text
--custom-degradation FILE.py:function
--custom-quality FILE.py:function
--custom-resolution FILE.py:function
--custom-qp FILE.py:function
```

The user result is written under `09_user_policy/`.

Each tile-policy folder contains:

```text
preview.mp4
preview.gif
metrics.json
```

Tile preview:

```text
left  = actual `apply_tile_plan` spatial degradation
right = `render_tile_plan` heat/grid
```

Per-policy metrics:

- planner mean/p95;
- effective pixel fraction;
- mean tile quality;
- mean tile delta-QP;
- full policy config and artifact paths.

Top-level report additionally records:

- source SHA256/bytes/raster/FPS/duration;
- Python/platform/OpenCV/NumPy/FFmpeg;
- git HEAD when available;
- all preset benchmark records;
- every logical tile policy record.

The gallery is curated rather than Cartesian: every major algorithmic branch is represented, while `--matrix full` adds useful numeric sweeps without producing thousands of redundant combinations.

## Documentation on this branch

### `README.md`

Rebuilt as the primary user guide. It now starts with the all-modes gallery workflow and contains architecture, all actuators, custom tile hooks, API map, benchmarks, realtime integration and implementation boundaries.

### `docs/API_REFERENCE.md`

Documents the public Python API by purpose, including spatial primitives, ROI tracking, runtime, middleware, evidence fusion, caches, adaptive transport, encoder hints and logical tiles.

### `docs/TILE_POLICIES.md`

Documents exact tile semantics, all built-in curves/aggregations, all four custom policy levels, safety clipping/floors, `apply_tile_plan`, effective pixel accounting and user-policy benchmarking.

### `docs/BENCHMARK_GALLERY.md`

Documents gallery folder layout, standard/full matrices, visual semantics, metrics, custom callback loading and interpretation rules.

### `AGENTS.md`

Updated to make the new docs mandatory reading and preserve custom-policy / benchmark semantic invariants for future coding agents.

## Tests added on this branch

`tests/test_tile_policies.py` covers:

- context-aware quality + custom resolution + custom QP together;
- rejection of simultaneous `degradation_fn` + `quality_fn`;
- clipping/floor safety around custom callbacks;
- `apply_tile_plan` shape preservation and visible low-resolution degradation.

CI smoke checks now compile the new gallery/custom-policy files and invoke `benchmark_gallery.py --help`.

Before merge, verify the complete configured CI matrix:

- Python tests + CLI smoke — must PASS;
- Rust test/release Ubuntu — must PASS;
- Rust test/release macOS — must PASS;
- Rust test/release Windows — must PASS.

## Benchmark interpretation invariant

Different outputs measure different costs:

```text
same-size foveated H.264 -> actual encoded bytes
context + ROI            -> actual image raster pixels
logical tiles            -> estimated multiresolution pixels until a real adapter sends them
temporal reuse           -> high-resolution update frequency
QP map                    -> spatial encoder policy until a real encoder consumes it
SEND/SKIP                 -> emitted request/frame count
```

Never report `TilePlan.effective_pixel_fraction` as encoded-byte savings.

Existing authoritative codec measurements remain:

- `example1` aggressive: **71.30% H.264 saving**, **96.26% context+ROI pixel saving**;
- `example2` aggressive: **31.34% H.264 saving**, **84.08% context+ROI pixel saving**.

These are reference-video measurements, not universal task-quality/device-latency claims.

## Still adapter/platform work

Do NOT claim these as complete yet:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI spatial-QP integration;
- actual WebRTC/RTP/GStreamer logical-tile packetization;
- real network/codec application of `TilePlan.resolution_scale`;
- full native NV12/YUV hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- CameraX/AVFoundation/OpenXR direct camera adapters;
- world-locked background cache for freely moving cameras;
- stateful C ABI for the high-level adaptive runtime;
- target-device p50/p95/energy matrix;
- broad downstream task-quality validation.

## Next priorities

### P0 — concrete tiled transport adapter

Consume `TilePlan` and truly send/store/encode the reduced-resolution tile payload instead of only generating policy/reference visualization.

### P1 — concrete encoder adapter

Translate `EncoderSpatialHints` into one real encoder API and compare against global CRF/QP reduction at equivalent downstream task quality.

### P2 — native NV12/YUV + low-copy buffers

Remove RGB round trips from production hot paths.

### P3 — downstream task-quality benchmark

Compare full resolution, global quality reduction, global downscale, same-size foveation, layered context+ROI, temporal reuse, logical tiles and encoder QP at equivalent downstream model quality.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.
