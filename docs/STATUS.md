# STATUS / durable engineering handoff

Last updated: 2026-09-14

## Goal

FoveaStream is a provider-agnostic adaptive relevance transport SDK/middleware that sits between an existing frame source and an existing encoder, transport, model or callback.

```text
source
  -> timestamped frame + optional relevance evidence
  -> persistent predictive multi-ROI relevance state
  -> one or more transport/encoder actuators
  -> existing consumer
```

Do not turn core into a Gemini/OpenAI/WebRTC/CameraX-specific client.

## Current checkpoint

Branch: `main`

Latest merged feature PR: **#7 — Add advanced tile policies and all-modes benchmark gallery**

PR #7 squash merge commit:

`27416c5b5b5531cb89db883a22e09a7a4a3612fa`

Previous relevant merges:

- PR #6 — configurable logical relevance tiles + visual showcase;
- PR #5 — adaptive relevance transport stack;
- PR #4 — drop-in source/transform/sink middleware.

Package version remains `0.3.0`.

## Verification

Final PR #7 CI passed:

- Python unit tests — **PASS** (`32 passed`);
- Python CLI syntax/help smoke checks — **PASS**;
- FFmpeg/libx264 dependency check — **PASS**;
- real runtime smoke of `bench/benchmark_gallery.py` over a deterministic synthetic MP4 — **PASS**;
- gallery smoke verified `INDEX.md`, `report.json`, and `tile_policies.csv` generation;
- Rust `cargo test --all-targets` + `cargo build --release` Ubuntu — **PASS**;
- Rust test/release macOS — **PASS**;
- Rust test/release Windows — **PASS**.

A first test iteration exposed only an invalid test assumption (`mean <= p90` for sparse tile data); the test invariant was corrected before the final green run. A later runtime-smoke iteration exposed that the clean Ubuntu Python job did not include FFmpeg by default; CI now installs FFmpeg explicitly and verifies `libx264` before running the benchmark gallery.

## Current implemented capabilities

### Drop-in realtime middleware

- causal frame-by-frame processing;
- `FoveaStreamTransform` same-size RGB transform;
- `FramePacket` / `OptimizedFrame` metadata envelope;
- bounded `RealtimeBridge`;
- `latest` frame replacement policy for latency-sensitive capture;
- `every_frame` continuous-video default;
- opt-in `when_send` scheduler suppression.

Recommended latency-sensitive topology:

```text
camera callback
  -> bounded queue depth 1 / latest
  -> FoveaStream
  -> encoder / model / transport
```

### Relevance and tracking

- class-agnostic camera-motion-compensated residual-motion proposal source;
- arbitrary external ROI proposals;
- `EvidenceBus` timestamped/TTL relevance fusion;
- point, ROI and dense relevance evidence;
- `max`, `noisy_or`, and additive dense-map fusion;
- low-resolution proposal analysis with normalized coordinates;
- persistent multi-ROI tracking;
- proposal dedupe/association;
- ROI velocity prediction;
- latency-aware prediction horizon;
- confidence decay and uncertainty growth;
- hard total normalized ROI-area budget;
- zero/one/many active ROIs.

### Output actuators

The same persistent relevance state can drive:

- same-size foveated RGB;
- low-resolution whole-scene context + N high-resolution ROI views;
- temporal ROI SEND/REUSE cache;
- optional mostly-static background tile delta cache;
- layered context + changed-ROI payload;
- packed one-image context+ROI atlas;
- exact-count logical relevance tiles;
- portable encoder block delta-QP map;
- adaptive SEND/SKIP recommendation;
- adaptive bitrate/pixel-budget controller.

Do not force all consumers through one actuator.

## Logical tile planner

Python: `python/foveastream/tiles.py`

Rust: `src/tiles.rs`

Basic configuration:

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

- `target_tiles` is exact, including counts such as 10, 25, 100, 137 or 400;
- tiles cover the whole frame;
- per-tile relevance is derived from the continuous relevance field;
- aggregations: `max`, `p90`, `mean`;
- built-in curves: `linear`, `smoothstep`, `gaussian`, `exponential`, `power`;
- each tile receives relevance, quality, resolution scale and delta-QP recommendation;
- `effective_pixel_fraction` estimates multiresolution raster cost as `sum(area * scale^2)`;
- `rasterize_tile_plan(...)` creates an HxW field;
- `render_tile_plan(...)` creates a quality/grid visualization;
- `apply_tile_plan(...)` provides a reference same-size realization of the requested tile resolution loss.

Important invariant:

```text
continuous relevance field
    +--> logical TilePlan       exact N application/transport tiles
    +--> encoder QP map         codec block grid, e.g. 16x16
```

Logical tiles and codec coding blocks are separate abstractions and may coexist.

## Custom tile policy API

Python supports four deliberate policy stages.

### Simple degradation callback

```python
def degradation(distance: float) -> float:
    ...

TilePlanner(config, degradation_fn=degradation)
```

`distance` is relevance-distance in `[0,1]` (`0 = highly relevant`).

### Context-aware quality callback

```python
def quality(ctx: TilePolicyContext) -> float:
    ...

TilePlanner(config, quality_fn=quality)
```

`TilePolicyContext` exposes:

```text
index / row / column
x / y / w / h
center_x / center_y / area_fraction
relevance / distance
mean_relevance / max_relevance / p90_relevance
map_width / map_height
```

`degradation_fn` and `quality_fn` are mutually exclusive because both own the raw-quality stage.

### Custom resolution callback

```python
def resolution(ctx, quality) -> float:
    ...

TilePlanner(config, resolution_fn=resolution)
```

`min_resolution_scale` remains a hard floor after the callback.

### Custom QP callback

```python
def qp(ctx, quality) -> int:
    ...

TilePlanner(config, qp_fn=qp)
```

Generic output is clamped to signed int8; real codec adapters must apply native codec limits.

Quality, resolution and QP hooks may be combined.

Copyable examples:

`examples/custom_tile_policy.py`

- `gentle_distance`;
- `focus_cliff`;
- `context_aware_quality`;
- `stepped_resolution`;
- `tiered_qp`.

Full contract: `docs/TILE_POLICIES.md`.

## Reference tile realization

```python
apply_tile_plan(frame_rgb, plan)
```

This CPU/reference path:

1. crops each logical tile;
2. downsamples it to `resolution_scale`;
3. upsamples it back into the original tile rectangle;
4. preserves the input raster dimensions.

Use it for visual verification, documentation and generic fallback pipelines.

It is **not** a real tiled network transport. A production tiled transport should transmit/store the tile at the lower native resolution instead of upscaling before transport.

## All-modes benchmark gallery

Entry point:

`bench/benchmark_gallery.py`

Recommended:

```powershell
python bench\benchmark_gallery.py example.mp4
```

Standard output:

```text
output/gallery/example/
  INDEX.md
  report.json
  tile_policies.csv
  tile_matrix_runtime.json

  01_presets/
    balanced/
    aggressive/
    extreme/

  02_core_actuators/
    multi-ROI relevance
    representative tile modes
    encoder QP map
    temporal ROI reuse
    packed atlas

  03_tile_counts/
    10 / 25 / 100 / 400

  04_tile_curves/
    linear / smoothstep / gaussian / exponential / power

  05_tile_aggregation/
    mean / p90 / max

  06_custom_policies/
    distance degradation
    protected-focus cliff
    context-aware quality
    stepped resolution
    tiered QP
```

`--matrix full` also adds:

```text
07_strength_sweep/
08_min_scale_sweep/
```

User policy callbacks can be loaded directly from a Python file:

```text
--custom-degradation FILE.py:function
--custom-quality FILE.py:function
--custom-resolution FILE.py:function
--custom-qp FILE.py:function
```

A user policy is written under `09_user_policy/`.

Every tile-policy folder contains:

```text
preview.mp4
preview.gif
metrics.json
```

Tile preview semantics:

```text
left  = actual `apply_tile_plan` spatial degradation
right = quality heat/grid
```

Per-policy metrics include:

- planner mean/p95 latency;
- effective pixel fraction;
- mean tile quality;
- mean tile delta-QP;
- exact configuration and artifact paths.

Top-level report records source SHA256/bytes/video metadata, git commit, Python/platform/OpenCV/NumPy/FFmpeg versions, preset benchmark records and every logical tile policy.

The gallery is curated rather than a full Cartesian parameter explosion. `standard` covers every major algorithmic branch; `full` adds representative strength/min-scale sweeps.

Full guide: `docs/BENCHMARK_GALLERY.md`.

## Documentation set

Primary user/agent documentation:

1. `README.md` — canonical architecture, quickstart, usage, benchmark numbers and implementation boundary;
2. `docs/API_REFERENCE.md` — public Python API grouped by purpose;
3. `docs/TILE_POLICIES.md` — custom tile policy model and callback contracts;
4. `docs/BENCHMARK_GALLERY.md` — all-modes benchmark/gallery workflow;
5. `docs/ADAPTIVE_TRANSPORT.md`;
6. `docs/FRAME_MIDDLEWARE.md`;
7. `docs/AGENT_INTEGRATION.md`;
8. `AGENTS.md` — engineering invariants for future coding agents;
9. this `docs/STATUS.md` — exact durable checkpoint.

## Existing authoritative real-video codec benchmark

Checked-in same-decoded-frame H.264 benchmark remains:

- `example1`, aggressive: **71.30% H.264 byte saving**, **96.26% context+ROI pixel saving**, **20.16 ms/frame** reference processing;
- `example2`, aggressive: **31.34% H.264 byte saving**, **84.08% context+ROI pixel saving**, **29.28 ms/frame** reference processing.

Machine-readable source:

`docs/assets/real_demo/benchmark_presets_2026-09-14.json`

These are reference-video measurements, not universal target-device/task-quality claims.

## Benchmark interpretation invariant

Different outputs measure different costs:

```text
same-size foveated H.264 -> actual encoded bytes
context + ROI            -> actual image raster pixels
logical tiles            -> estimated multiresolution pixels until a real tiled adapter consumes them
temporal reuse           -> high-resolution ROI update frequency
QP map                    -> encoder policy until a real encoder consumes it
SEND/SKIP                 -> emitted request/frame count
```

Never report `TilePlan.effective_pixel_fraction` as encoded-byte saving.

Never report generic delta-QP metadata as actual encoder byte saving until a real encoder adapter consumes it.

## Still adapter/platform work

Do NOT claim these as complete yet:

- direct MediaCodec/NVENC/VideoToolbox/VAAPI ROI/QP integration;
- actual WebRTC/RTP/GStreamer logical-tile/layer packetization;
- real network/codec application of `TilePlan.resolution_scale`;
- full native NV12/YUV hot path;
- zero-copy AHardwareBuffer/CVPixelBuffer/DMA-BUF;
- CameraX/Camera2 direct adapter;
- AVFoundation direct adapter;
- OpenXR/Meta direct camera adapter;
- moving-camera world-locked background cache;
- stateful C ABI for the full adaptive runtime;
- target-device p50/p95/energy characterization;
- broad downstream task-quality validation.

## Next engineering priorities

### P0 — concrete tiled transport adapter

Consume `TilePlan` and actually transmit/store tiles at their requested native resolution/quality.

### P1 — concrete encoder adapter

Translate `EncoderSpatialHints` into one real encoder family and compare against global CRF/QP reduction at equivalent downstream task quality.

### P2 — native pixel formats / low copy

Add NV12/YUV + explicit stride/pixel-format contracts and remove RGB round trips from production hot paths.

### P3 — downstream quality benchmark

Compare at equivalent task quality:

- original full resolution;
- global CRF/QP reduction;
- global downscale;
- same-size foveation;
- layered context+ROI;
- temporal reuse;
- logical tiles;
- encoder spatial-QP mode.

## Product win condition

> Lower bytes / decoded pixels / visual tokens / requests / latency / energy at equivalent downstream task quality and bounded important-region miss probability.
