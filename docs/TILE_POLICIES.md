# FoveaStream logical tile policies

This document is the complete guide to the logical tile planner and its extension points.

Logical tiles are an **application/transport actuator** derived from the same continuous relevance field used by same-size foveation, ROI layers and encoder QP hints. They are not the codec's own coding blocks.

```text
continuous relevance field
        |
        +--> logical TilePlan       exact N application/transport tiles
        |
        +--> encoder QP map         codec block grid, e.g. 16x16
```

A host may use either or both.

---

## 1. Minimal example

```python
from foveastream import TilePlanner, TilePlannerConfig

planner = TilePlanner(
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

plan = planner.plan(out.process_result.quality_map)
```

`target_tiles` is exact. The planner supports non-square counts such as 10, 25, 100, 137 or 400 while keeping tile shapes reasonably aligned with the source aspect ratio.

Each `TileDecision` contains:

```python
tile.index
tile.row
tile.column

tile.x
tile.y
tile.w
tile.h

tile.relevance
tile.distance

tile.quality
tile.resolution_scale
tile.qp_delta
```

Coordinates are normalized to `[0,1]`.

---

## 2. What the fields mean

### `relevance`

The selected aggregate relevance of all quality-map pixels covered by the logical tile.

Range:

```text
0.0 = irrelevant
1.0 = maximally relevant
```

### `distance`

Defined as:

```text
distance = 1 - relevance
```

So:

```text
distance 0.0 = at / inside high relevance
distance 1.0 = maximally far in relevance-space
```

This is a **relevance distance**, not Euclidean pixel distance to the nearest ROI rectangle.

### `quality`

Normalized tile fidelity in `[0,1]` after applying the selected curve/custom policy and the configured `min_quality` floor.

### `resolution_scale`

Suggested per-axis tile resolution scale.

Examples:

```text
1.000 -> full tile width and height
0.500 -> half width and half height -> ~25% tile pixels
0.250 -> quarter width and height   -> ~6.25% tile pixels
0.125 -> one eighth per axis        -> ~1.56% tile pixels
```

### `qp_delta`

Suggested signed spatial QP offset. Lower values mean more encoder quality, higher values mean less.

This is policy metadata until a real codec/transport adapter consumes it.

---

## 3. Exact tile count

```python
TilePlannerConfig(target_tiles=10)
TilePlannerConfig(target_tiles=25)
TilePlannerConfig(target_tiles=100)
TilePlannerConfig(target_tiles=400)
```

Fewer tiles:

- lower planning/transport bookkeeping;
- coarser spatial decisions;
- one tiny important region can protect a large tile.

More tiles:

- finer spatial fidelity control;
- higher metadata/transport overhead;
- better fit around multiple small ROIs.

There is no universally correct tile count. Benchmark the downstream task and transport overhead.

---

## 4. Tile relevance aggregation

The planner must reduce many relevance pixels to one logical tile relevance value.

```python
TilePlannerConfig(aggregation="max")
TilePlannerConfig(aggregation="p90")
TilePlannerConfig(aggregation="mean")
```

### `max`

```text
relevance = maximum pixel relevance inside tile
```

Best when missing a small important region is expensive. This is the conservative default.

### `p90`

```text
relevance = 90th percentile inside tile
```

Good compromise when isolated one-pixel peaks should not force full quality.

### `mean`

```text
relevance = average relevance inside tile
```

Most aggressive spatial compression. A tiny but important region can be diluted by a large irrelevant part of the tile.

---

## 5. Built-in degradation curves

The built-in curve maps `distance` to raw quality.

```python
curve="linear"
curve="smoothstep"
curve="gaussian"
curve="exponential"
curve="power"
```

### Linear

Conceptually:

```text
q = 1 - distance
```

Predictable and easy to reason about.

### Smoothstep

Smooth cubic transition with flat derivatives at the ends. Good when abrupt spatial transitions are undesirable.

### Gaussian

Strong fidelity near relevance with progressively stronger falloff.

```python
TilePlannerConfig(curve="gaussian", curve_strength=3.0)
```

### Exponential

Drops rapidly as relevance decreases.

### Power

```text
q = (1 - distance) ** strength
```

Useful when you want a very explicit mathematical shape.

### `curve_strength`

Higher values generally make the non-linear built-ins more selective/aggressive.

```python
TilePlannerConfig(
    target_tiles=100,
    curve="gaussian",
    curve_strength=1.0,  # gentle
)

TilePlannerConfig(
    target_tiles=100,
    curve="gaussian",
    curve_strength=7.0,  # aggressive
)
```

Use `bench/benchmark_gallery.py --matrix full` to generate a strength sweep automatically.

---

# 6. Custom policy level 1: distance-only degradation

The simplest extension point receives only normalized relevance distance:

```python
def degradation(distance: float) -> float:
    ...
```

Input:

```text
0.0 <= distance <= 1.0
```

Return raw quality:

```text
0.0 <= raw quality <= 1.0
```

Example:

```python
from foveastream import TilePlanner, TilePlannerConfig


def my_degradation(distance: float) -> float:
    return max(0.0, 1.0 - distance ** 1.7)

planner = TilePlanner(
    TilePlannerConfig(
        target_tiles=100,
        min_quality=0.03,
        min_resolution_scale=0.10,
    ),
    degradation_fn=my_degradation,
)
```

The returned raw quality is clipped to `[0,1]`, then the configured `min_quality` floor is applied.

You therefore do not need to implement safety clipping inside every policy.

### Sharp protected center

```python
def focus_cliff(distance: float) -> float:
    if distance <= 0.18:
        return 1.0
    return max(0.0, 1.0 - ((distance - 0.18) / 0.82) ** 0.55)
```

This keeps a high-quality plateau around relevant regions before quality starts collapsing.

---

# 7. Custom policy level 2: context-aware quality

For more control use:

```python
quality_fn(context: TilePolicyContext) -> float
```

`TilePolicyContext` exposes:

```python
ctx.index
ctx.row
ctx.column

ctx.x
ctx.y
ctx.w
ctx.h
ctx.center_x
ctx.center_y
ctx.area_fraction

ctx.relevance
ctx.distance
ctx.mean_relevance
ctx.max_relevance
ctx.p90_relevance

ctx.map_width
ctx.map_height
```

This allows policies that depend on geometry, multiple relevance statistics or application priors.

Example:

```python
import math
from foveastream import TilePlanner, TilePlannerConfig, TilePolicyContext


def quality_policy(ctx: TilePolicyContext) -> float:
    center_distance = math.hypot(
        ctx.center_x - 0.5,
        ctx.center_y - 0.5,
    ) / math.sqrt(0.5)

    center_prior = max(0.0, 1.0 - center_distance)

    relevance = max(
        ctx.max_relevance,
        0.80 * ctx.p90_relevance,
        0.55 * ctx.mean_relevance,
    )

    return min(1.0, 0.90 * relevance + 0.10 * center_prior)

planner = TilePlanner(
    TilePlannerConfig(target_tiles=100),
    quality_fn=quality_policy,
)
```

`quality_fn` and `degradation_fn` are mutually exclusive because both decide the raw quality stage.

---

# 8. Custom policy level 3: resolution mapping

You can keep the built-in/custom quality calculation but change how quality becomes tile resolution.

Signature:

```python
resolution_fn(context: TilePolicyContext, quality: float) -> float
```

Example with hardware/transport-friendly discrete levels:

```python
def stepped_resolution(ctx, quality):
    if quality >= 0.82:
        return 1.0
    if quality >= 0.55:
        return 0.5
    if quality >= 0.25:
        return 0.25
    return 0.125

planner = TilePlanner(
    TilePlannerConfig(
        target_tiles=100,
        curve="gaussian",
        min_resolution_scale=0.125,
    ),
    resolution_fn=stepped_resolution,
)
```

The configured `min_resolution_scale` is still enforced after the callback.

That means a broken callback returning `0` cannot silently produce an invalid zero-resolution tile.

---

# 9. Custom policy level 4: QP mapping

Signature:

```python
qp_fn(context: TilePolicyContext, quality: float) -> int
```

Example:

```python
def tiered_qp(ctx, quality):
    if quality >= 0.85:
        return -6
    if quality >= 0.60:
        return 0
    if quality >= 0.30:
        return 8
    return 18

planner = TilePlanner(
    TilePlannerConfig(target_tiles=100),
    qp_fn=tiered_qp,
)
```

The result is clamped to signed `int8` range `[-128,127]`.

Real codecs will usually support a much narrower useful/valid range; the concrete encoder adapter should enforce its own native limits.

---

# 10. Combine all custom hooks

```python
planner = TilePlanner(
    TilePlannerConfig(
        target_tiles=137,
        aggregation="p90",
        min_quality=0.02,
        min_resolution_scale=0.125,
    ),
    quality_fn=quality_policy,
    resolution_fn=stepped_resolution,
    qp_fn=tiered_qp,
)

plan = planner.plan(out.process_result.quality_map)
```

This cleanly separates four decisions:

```text
quality map
   |
   v
aggregate tile relevance
   |
   v
quality_fn / degradation curve
   |
   +--> final tile quality
   |
   +--> resolution_fn -> resolution scale
   |
   +--> qp_fn         -> QP delta
```

The host is free to consume only the fields it supports.

---

# 11. Reference visual realization

FoveaStream provides a CPU/reference function that makes the recommended tile resolution visible while preserving the original frame dimensions:

```python
from foveastream import apply_tile_plan

degraded_rgb = apply_tile_plan(frame_rgb, plan)
```

For every tile it:

1. crops the original tile;
2. downsamples to `resolution_scale`;
3. upsamples back to the original tile rectangle;
4. writes the result into a same-size output frame.

This is useful for:

- documentation;
- visual debugging;
- generic non-tiled pipelines;
- comparing custom degradation functions.

It is **not** the ideal production transport implementation. A real tiled transport should send/store the tile at its reduced native resolution instead of upscaling it before transmission.

---

# 12. Heat/grid visualization

```python
from foveastream import render_tile_plan

heat_rgb = render_tile_plan(frame_rgb, plan)
```

This overlays per-tile quality as a heat field plus tile boundaries.

For <=25 tiles, labels are shown automatically.

The all-modes gallery renders both views side-by-side:

```text
left  = actual reference resolution degradation
right = tile quality heat/grid
```

---

# 13. Effective pixel fraction

```python
plan.effective_pixel_fraction
```

Formula:

```text
sum(tile.area_fraction * tile.resolution_scale^2)
```

Why scale is squared:

```text
0.5 width * 0.5 height = 0.25 pixels
0.25 width * 0.25 height = 0.0625 pixels
```

This metric estimates the raster payload of a real multi-resolution tiled representation.

It does **not** estimate:

- H.264/H.265/AV1 byte size;
- packet headers;
- WebRTC overhead;
- model accuracy;
- decoder complexity.

Those require a concrete downstream adapter/benchmark.

---

# 14. Reference consumer

A custom transport could conceptually do:

```python
for tile in plan.tiles:
    crop = crop_original_frame(tile)
    low_res = resize(crop, tile.resolution_scale)

    transport.send_tile(
        tile_id=tile.index,
        x=tile.x,
        y=tile.y,
        w=tile.w,
        h=tile.h,
        image=low_res,
        qp_delta=tile.qp_delta,
    )
```

The receiver reconstructs or consumes the tile set according to the application's needs.

FoveaStream intentionally does not hardcode one packet protocol into the relevance core.

---

# 15. Benchmark every built-in and custom policy on your own video

```powershell
python bench\benchmark_gallery.py example.mp4
```

The gallery automatically runs:

- 10 / 25 / 100 / 400 tile counts;
- linear / smoothstep / Gaussian / exponential / power curves;
- mean / p90 / max aggregation;
- several custom callback examples;
- balanced / aggressive / extreme overall presets;
- core relevance/QP/cache/atlas visualizations.

For additional parameter sweeps:

```powershell
python bench\benchmark_gallery.py example.mp4 --matrix full
```

Every tile policy gets its own folder containing:

```text
preview.mp4
preview.gif
metrics.json
```

See `docs/BENCHMARK_GALLERY.md`.

---

# 16. Benchmark your own callback module

The gallery can import callbacks directly from a Python file.

Given:

```text
my_policy.py
```

```python
from foveastream import TilePolicyContext


def quality(ctx: TilePolicyContext) -> float:
    return max(ctx.p90_relevance, 0.25 * ctx.max_relevance)


def scale(ctx: TilePolicyContext, quality: float) -> float:
    return 1.0 if quality > 0.7 else 0.25
```

Run:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --custom-quality my_policy.py:quality `
  --custom-resolution my_policy.py:scale
```

The result appears under:

```text
output/gallery/example/09_user_policy/user_policy/
```

Supported plugin switches:

```text
--custom-degradation FILE.py:function
--custom-quality FILE.py:function
--custom-resolution FILE.py:function
--custom-qp FILE.py:function
```

You may combine quality + resolution + QP callbacks. Do not combine `--custom-degradation` and `--custom-quality` because both own the raw-quality stage.

---

# 17. Copyable examples

See:

```text
examples/custom_tile_policy.py
```

It contains:

- `gentle_distance`;
- `focus_cliff`;
- `context_aware_quality`;
- `stepped_resolution`;
- `tiered_qp`.

These are deliberately plain Python functions so applications can copy/adapt them without depending on a policy framework.

---

# 18. Important boundary

A `TilePlan` is a decision/control-plane object.

Today FoveaStream can:

- calculate the exact tile geometry;
- calculate quality;
- calculate recommended resolution;
- calculate QP suggestion;
- visualize actual reference downsampling;
- estimate multi-resolution pixel load;
- benchmark the policy.

A production integration still needs a downstream implementation that actually:

- sends tiles separately;
- stores tiles separately;
- maps logical tiles onto an encoder feature;
- reconstructs the tiled frame;
- or consumes the tiles directly in a model/API.

Do not claim actual network-byte reduction from logical tiles until that adapter exists and is measured.
