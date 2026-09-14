from pathlib import Path

p = Path('README.md')
s = p.read_text(encoding='utf-8')

old = '# Visual showcase\n'
new = '''# Real `example1` actuator showcase

The GIFs in this section are from the **real `example1` cat-video benchmark gallery**. They replace the old synthetic documentation scene. README copies are lightweight animated derivatives of the exact benchmark outputs so GitHub does not load tens of megabytes per page view.

'''
if old in s:
    s = s.replace(old, new, 1)

marker = '# Existing real-video examples\n'
section = r'''# Real `example1` all-modes benchmark gallery

The gallery below was generated from the same real `example1` cat clip with:

```powershell
python bench\benchmark_gallery.py example1.mp4 --clean
```

It is meant to show the supported FoveaStream policy families before integration. A full local run creates MP4/GIF/JSON per configuration; the README uses compact animated derivatives of those exact benchmark outputs.

## What the gallery covers

| Family | Supported choices | What changes |
|---|---|---|
| Core actuators | multi-ROI, logical tiles, encoder QP field, temporal reuse, context+ROI atlas | output representation / actuator |
| Tile count | exact 10 / 25 / 100 / 400 / arbitrary N | spatial policy granularity |
| Degradation curve | linear / smoothstep / gaussian / exponential / power | how quickly fidelity falls away from relevant regions |
| Aggregation | mean / p90 / max | how pixel relevance inside each logical tile becomes one tile relevance value |
| Custom policy | distance, context-aware quality, stepped resolution, tiered QP | user-defined policy stages |

### Tile count / spatial granularity

`target_tiles` is an **exact whole-frame logical tile count**. Fewer tiles are coarser; more tiles give more precise spatial control.

<table>
<tr>
<td align="center"><b>10 tiles</b><br><img src="docs/assets/benchmark_gallery/example1/tile_counts/10_gaussian.gif" width="180"></td>
<td align="center"><b>25 tiles</b><br><img src="docs/assets/benchmark_gallery/example1/tile_counts/25_gaussian.gif" width="180"></td>
<td align="center"><b>100 tiles</b><br><img src="docs/assets/benchmark_gallery/example1/tile_counts/100_gaussian.gif" width="180"></td>
<td align="center"><b>400 tiles</b><br><img src="docs/assets/benchmark_gallery/example1/tile_counts/400_gaussian.gif" width="180"></td>
</tr>
</table>

CLI:

```powershell
python examples\adaptive_transport.py --video example.mp4 --tiles 100 --tile-curve gaussian
```

Python:

```python
planner = TilePlanner(TilePlannerConfig(target_tiles=100))
```

The logical tile grid is **not** the codec block grid. A 100-tile plan can still be translated to a much finer 16x16/32x32 encoder QP map.

### Built-in degradation curves

All built-in curves map normalized relevance-distance (`0 = most relevant`, `1 = least relevant`) to tile quality.

<table>
<tr>
<td align="center"><b>linear</b><br><img src="docs/assets/benchmark_gallery/example1/curves/linear.gif" width="180"></td>
<td align="center"><b>smoothstep</b><br><img src="docs/assets/benchmark_gallery/example1/curves/smoothstep.gif" width="180"></td>
<td align="center"><b>gaussian</b><br><img src="docs/assets/benchmark_gallery/example1/curves/gaussian.gif" width="180"></td>
</tr>
<tr>
<td align="center"><b>exponential</b><br><img src="docs/assets/benchmark_gallery/example1/curves/exponential.gif" width="180"></td>
<td align="center"><b>power</b><br><img src="docs/assets/benchmark_gallery/example1/curves/power.gif" width="180"></td>
<td></td>
</tr>
</table>

- `linear` — constant-rate falloff.
- `smoothstep` — eased transition with softer endpoints.
- `gaussian` — smooth high-quality focus with progressively weaker periphery.
- `exponential` — faster, more aggressive quality decay.
- `power` — tunable nonlinear shape.

```powershell
python bench\benchmark_suite.py example.mp4 `
  --tiles 100 `
  --tile-curve gaussian `
  --tile-strength 3.0
```

```python
TilePlannerConfig(
    target_tiles=100,
    curve='gaussian',
    curve_strength=3.0,
    min_quality=0.04,
    min_resolution_scale=0.125,
)
```

### Tile relevance aggregation

Aggregation controls how an HxW relevance field inside a logical tile is reduced to one relevance value.

<table>
<tr>
<td align="center"><b>mean</b><br><img src="docs/assets/benchmark_gallery/example1/aggregation/mean.gif" width="180"></td>
<td align="center"><b>p90</b><br><img src="docs/assets/benchmark_gallery/example1/aggregation/p90.gif" width="180"></td>
<td align="center"><b>max</b><br><img src="docs/assets/benchmark_gallery/example1/aggregation/max.gif" width="180"></td>
</tr>
</table>

- `mean` — aggressive; a tiny important region can be diluted by the rest of a coarse tile.
- `p90` — robust high-percentile policy.
- `max` — safest for tiny critical support because one strong local relevance value can protect the tile.

```python
TilePlannerConfig(target_tiles=100, aggregation='p90')
```

### Custom tile policies

FoveaStream does not lock users to built-in curves. The planner exposes independent hooks for quality, spatial resolution and encoder-QP recommendation.

<table>
<tr>
<td align="center"><b>gentle distance</b><br><img src="docs/assets/benchmark_gallery/example1/custom/gentle_distance.gif" width="180"></td>
<td align="center"><b>focus cliff</b><br><img src="docs/assets/benchmark_gallery/example1/custom/focus_cliff.gif" width="180"></td>
<td align="center"><b>context-aware quality</b><br><img src="docs/assets/benchmark_gallery/example1/custom/context_aware_quality.gif" width="180"></td>
</tr>
<tr>
<td align="center"><b>stepped resolution</b><br><img src="docs/assets/benchmark_gallery/example1/custom/stepped_resolution.gif" width="180"></td>
<td align="center"><b>tiered QP</b><br><img src="docs/assets/benchmark_gallery/example1/custom/tiered_qp.gif" width="180"></td>
<td></td>
</tr>
</table>

Simple custom distance degradation:

```python
def my_degradation(distance: float) -> float:
    return max(0.0, 1.0 - distance ** 1.7)

planner = TilePlanner(
    TilePlannerConfig(target_tiles=100),
    degradation_fn=my_degradation,
)
```

Full context-aware policy:

```python
from foveastream import TilePlanner, TilePlannerConfig, TilePolicyContext


def quality(ctx: TilePolicyContext) -> float:
    return max(ctx.max_relevance, 0.8 * ctx.p90_relevance)


def resolution(ctx: TilePolicyContext, quality: float) -> float:
    if quality >= 0.82:
        return 1.0
    if quality >= 0.55:
        return 0.5
    if quality >= 0.25:
        return 0.25
    return 0.125


def qp(ctx: TilePolicyContext, quality: float) -> int:
    if quality >= 0.85:
        return -6
    if quality >= 0.60:
        return 0
    if quality >= 0.30:
        return 8
    return 18

planner = TilePlanner(
    TilePlannerConfig(
        target_tiles=100,
        aggregation='p90',
        min_quality=0.02,
        min_resolution_scale=0.125,
    ),
    quality_fn=quality,
    resolution_fn=resolution,
    qp_fn=qp,
)
```

The callbacks can be benchmarked without modifying the library:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --custom-quality my_policy.py:quality `
  --custom-resolution my_policy.py:resolution `
  --custom-qp my_policy.py:qp `
  --clean
```

Copyable callback implementations are in [`examples/custom_tile_policy.py`](examples/custom_tile_policy.py). The complete callback contract is in [`docs/TILE_POLICIES.md`](docs/TILE_POLICIES.md).

## Gallery output layout

```text
output/gallery/example/
  INDEX.md
  report.json
  tile_policies.csv
  tile_matrix_runtime.json

  01_presets/          balanced / aggressive / extreme
  02_core_actuators/   multi-ROI / tiles / QP / temporal reuse / atlas
  03_tile_counts/      10 / 25 / 100 / 400
  04_tile_curves/      linear / smoothstep / gaussian / exponential / power
  05_tile_aggregation/ mean / p90 / max
  06_custom_policies/  five reference custom policies
```

`--matrix full` additionally adds strength and minimum-resolution sweeps:

```powershell
python bench\benchmark_gallery.py example.mp4 --matrix full --clean
```

Machine-readable README-gallery policy results are checked in at [`docs/assets/benchmark_gallery/example1/tile_policies.csv`](docs/assets/benchmark_gallery/example1/tile_policies.csv).

### How to interpret the numbers

Different actuators measure different costs:

```text
same-size foveated H.264 -> actual encoded bytes
context + ROI            -> actual image raster pixels
temporal reuse           -> high-resolution refresh frequency
logical tiles            -> estimated multiresolution raster pixels until a real tile adapter consumes them
QP map                    -> spatial encoder policy until a real hardware/software encoder consumes it
SEND/SKIP                 -> emitted request/frame count
```

Do not report `TilePlan.effective_pixel_fraction` as encoded-byte savings. The checked-in H.264 results lower in this README are actual same-encoder byte measurements.

---

'''

if 'Real `example1` all-modes benchmark gallery' not in s:
    if marker not in s:
        raise SystemExit(f'README marker not found: {marker!r}')
    s = s.replace(marker, section + marker, 1)

p.write_text(s, encoding='utf-8')
