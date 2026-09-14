# FoveaStream all-modes benchmark gallery

`bench/benchmark_gallery.py` is the fastest way to understand FoveaStream on **your own video**.

Give it one MP4/video file and it creates a browsable directory containing representative outputs for every major algorithm/actuator plus the main tile settings.

It is intentionally a **curated matrix**, not a Cartesian product of every numeric parameter. A full Cartesian product would create hundreds or thousands of nearly redundant runs.

---

# 1. One command

Windows / PowerShell:

```powershell
python bench\benchmark_gallery.py example.mp4
```

Linux/macOS:

```bash
python bench/benchmark_gallery.py example.mp4
```

Default output:

```text
output/gallery/example/
```

At the end the command prints the exact paths to:

```text
INDEX.md
report.json
```

Open `INDEX.md` first.

---

# 2. Folder layout

A standard run creates approximately:

```text
output/gallery/example/
  INDEX.md
  report.json
  tile_matrix_runtime.json

  01_presets/
    balanced/
      example/
        ... codec + transport benchmark artifacts ...
    aggressive/
      example/
        ...
    extreme/
      example/
        ...

  02_core_actuators/
    01_multi_roi.mp4
    01_multi_roi.gif
    02_tiles_10_linear.mp4
    02_tiles_10_linear.gif
    03_tiles_25_smoothstep.mp4
    03_tiles_25_smoothstep.gif
    04_tiles_100_gaussian.mp4
    04_tiles_100_gaussian.gif
    05_tiles_100_exponential.mp4
    05_tiles_100_exponential.gif
    06_tiles_400_gaussian.mp4
    06_tiles_400_gaussian.gif
    07_qp_map.mp4
    07_qp_map.gif
    08_temporal_reuse.mp4
    08_temporal_reuse.gif
    09_roi_atlas.mp4
    09_roi_atlas.gif
    manifest.json

  03_tile_counts/
    tiles_0010_gaussian/
      preview.mp4
      preview.gif
      metrics.json
    tiles_0025_gaussian/
      ...
    tiles_0100_gaussian/
      ...
    tiles_0400_gaussian/
      ...

  04_tile_curves/
    curve_linear/
    curve_smoothstep/
    curve_gaussian/
    curve_exponential/
    curve_power/

  05_tile_aggregation/
    aggregation_mean/
    aggregation_p90/
    aggregation_max/

  06_custom_policies/
    custom_gentle_distance/
    custom_focus_cliff/
    custom_context_aware_quality/
    custom_stepped_resolution/
    custom_tiered_qp/
```

Every tile-policy folder contains:

```text
preview.mp4
preview.gif
metrics.json
```

---

# 3. What the tile-policy preview shows

Each tile-policy preview is two panels:

```text
+---------------------------+---------------------------+
| actual tile-resolution    | quality heat/grid         |
| degradation               |                           |
|                           |                           |
+---------------------------+---------------------------+
```

Left side:

- each logical tile is downsampled to its recommended `resolution_scale`;
- it is then upscaled back to the source tile rectangle;
- this makes the actual spatial loss visible in a normal video.

Right side:

- tile boundaries;
- normalized quality heat field.

The banner also shows:

```text
tile count
curve
aggregation
effective pixel fraction
mean quality
mean delta-QP
```

---

# 4. Preset benchmark section

By default the gallery runs the existing full benchmark suite for:

```text
balanced
aggressive
extreme
```

Each preset includes:

- same-decoded-frame H.264 baseline vs foveated H.264;
- H.264 bytes saved;
- context+ROI pixel saving;
- processing time;
- adaptive transport metrics;
- temporal ROI reuse;
- logical tile metrics.

This is the expensive part of the gallery because it actually re-encodes video.

Skip it when you only want visual policy exploration:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --skip-preset-benchmarks
```

---

# 5. Core actuator showcase

The `02_core_actuators` folder is produced by the normal showcase generator and demonstrates:

1. multi-ROI relevance;
2. coarse logical tiles;
3. medium logical tiles;
4. fine Gaussian tiles;
5. exponential tiles;
6. very fine tiles;
7. encoder delta-QP map;
8. temporal ROI SEND/REUSE decisions;
9. packed context+changed-ROI atlas.

Skip this section:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --skip-showcase
```

---

# 6. Standard vs full matrix

Default:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --matrix standard
```

`standard` covers each major tile feature:

```text
tile count sweep
all built-in curves
all aggregation modes
custom degradation callback
custom context-aware quality
custom resolution mapping
custom QP mapping
```

Additional sweeps:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --matrix full
```

`full` adds:

```text
07_strength_sweep/
  gaussian_strength_1
  gaussian_strength_2
  gaussian_strength_4.5
  gaussian_strength_7

08_min_scale_sweep/
  min_scale_0.0625
  min_scale_0.125
  min_scale_0.25
  min_scale_0.5
```

---

# 7. Gallery duration and FPS

The expensive preset codec benchmarks use the whole input video unless the lower-level benchmark says otherwise.

The visual policy matrix is sampled because its goal is comparison, not duplicate full-video encoding.

Defaults:

```text
gallery FPS:     8
gallery seconds: 6
```

Change them:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --gallery-fps 12 `
  --gallery-seconds 10
```

Higher values make smoother/longer GIFs but increase runtime and artifact size.

---

# 8. Change visual output size

Tile policy two-panel preview width:

```powershell
--gallery-width 1280
```

GIF width:

```powershell
--gif-width 720
```

Core showcase square size:

```powershell
--showcase-size 720
```

Skip GIF conversion when you only want MP4:

```powershell
--no-gif
```

---

# 9. Custom policy from your own Python file

The gallery can import policy callbacks from an arbitrary `.py` file without making it a package.

Example `my_policy.py`:

```python
from foveastream import TilePolicyContext


def quality(ctx: TilePolicyContext) -> float:
    # Protect a tile if either p90 or max relevance is high.
    return max(ctx.p90_relevance, 0.6 * ctx.max_relevance)


def resolution(ctx: TilePolicyContext, quality: float) -> float:
    if quality >= 0.8:
        return 1.0
    if quality >= 0.4:
        return 0.5
    return 0.125


def qp(ctx: TilePolicyContext, quality: float) -> int:
    return round(18 - 24 * quality)
```

Run:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --custom-quality my_policy.py:quality `
  --custom-resolution my_policy.py:resolution `
  --custom-qp my_policy.py:qp
```

The custom result appears under:

```text
09_user_policy/user_policy/
```

Supported callback flags:

```text
--custom-degradation FILE.py:function
--custom-quality FILE.py:function
--custom-resolution FILE.py:function
--custom-qp FILE.py:function
```

Do not combine custom degradation and custom quality because both own the quality stage.

---

# 10. Configure the user policy baseline

```powershell
--user-tiles 137
--user-fallback-curve gaussian
--user-aggregation p90
--user-min-quality 0.02
--user-min-scale 0.125
```

Example:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --custom-quality my_policy.py:quality `
  --user-tiles 137 `
  --user-aggregation p90 `
  --user-min-quality 0.02
```

---

# 11. Metrics in each `metrics.json`

Example structure:

```json
{
  "category": "04_tile_curves",
  "name": "curve_gaussian",
  "config": {
    "target_tiles": 100,
    "curve": "gaussian",
    "curve_strength": 3.0,
    "aggregation": "max",
    "min_quality": 0.04,
    "min_resolution_scale": 0.125
  },
  "metrics": {
    "planner_ms_mean": 0.0,
    "planner_ms_p95": 0.0,
    "effective_pixel_fraction_mean": 0.0,
    "mean_tile_quality": 0.0,
    "mean_tile_qp_delta": 0.0
  }
}
```

Values above are schematic, not benchmark claims.

---

# 12. Metric definitions

## Planner latency

Only logical tile planning time for that policy, not the complete FoveaStream runtime.

```text
planner_ms_mean
planner_ms_p95
```

The common base runtime latency for the sampled matrix is written to:

```text
tile_matrix_runtime.json
```

## Effective pixel fraction

```text
sum(tile area fraction * resolution_scale^2)
```

This estimates raster cost if a real tiled transport sends each tile at the recommended scale.

It is **not encoded bytes**.

## Mean tile quality

Average normalized tile fidelity after curve/custom policy + `min_quality` floor.

## Mean tile delta-QP

Average recommended tile QP offset.

It is policy metadata until a codec adapter consumes it.

---

# 13. Top-level `report.json`

Contains:

```text
source path/name/SHA256/bytes
environment
selected matrix mode
all preset benchmark records
all logical tile policy records
artifact paths
```

Use it for automated comparison/regression.

---

# 14. Top-level `INDEX.md`

Human-readable summary with:

- folder descriptions;
- source hash;
- one table covering every logical tile policy;
- effective pixel fraction;
- quality;
- QP;
- planner p95.

This is the fastest way to browse a gallery run.

---

# 15. Recommended workflow for a new video

```powershell
git switch main
git pull origin main
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python bench\benchmark_gallery.py example3.mp4
```

Then inspect:

```text
output/gallery/example3/INDEX.md
```

Compare:

1. preset byte savings;
2. ROI behavior;
3. tile count visual fit;
4. curve behavior;
5. aggregation behavior;
6. temporal reuse;
7. atlas/QP representations;
8. custom policy outputs.

Only after that tune smaller numeric parameters.

---

# 16. Why this is not an exhaustive Cartesian benchmark

FoveaStream exposes many continuous parameters:

```text
ROI count
ROI budget
falloff
quality floors
prediction horizon
context scale
tile count
curve
curve strength
aggregation
min resolution
QP deltas
cache thresholds
controller targets
...
```

Trying every combination quickly becomes millions of runs.

The gallery instead covers:

- every major algorithmic branch;
- every built-in logical tile curve;
- every tile aggregation mode;
- representative coarse/fine tile counts;
- representative custom hooks;
- all three main presets.

Use `--matrix full` for an additional numeric sweep, then create a task-specific benchmark around the subset that actually matters.

---

# 17. Important interpretation rule

Different outputs optimize different costs.

Do not compare all metrics as if they were the same:

```text
same-size foveated H.264 -> actual codec bytes
context + ROI            -> image pixel payload
logical tiles            -> estimated multiresolution pixels until adapter exists
temporal reuse           -> high-resolution update frequency
QP map                    -> encoder policy until adapter exists
SEND/SKIP                 -> request/frame emission count
```

The final win condition is downstream task quality per byte/pixel/joule/second, not the prettiest preview.
