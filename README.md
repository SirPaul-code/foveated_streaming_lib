# FoveaStream

**Adaptive relevance-aware middleware for camera, video, VLM and machine-vision pipelines.**

FoveaStream sits between a frame source and a consumer and decides **where image quality is worth spending**. The same persistent relevance state can drive a normal same-size frame, logical tiles, encoder delta-QP hints, context + ROI layers, temporal reuse, packed atlases, or SEND/SKIP decisions.

```text
camera / decoder / RTSP / WebRTC / file / AR glasses / robot
                              |
                              v
                        FoveaStream
                 relevance + temporal state
                              |
       +-----------+----------+----------+----------+
       |           |          |          |          |
       v           v          v          v          v
   RGB frame    tiles      QP map    ROI layers   SEND/SKIP
       |           |          |          |          |
       +-----------+----------+----------+----------+
                              |
                              v
                    existing downstream
```

FoveaStream is provider-agnostic. ROI does **not** mean a hardcoded face/person/car class. An ROI is spatial support whose loss of detail would disproportionately hurt the current downstream task.

---

## Real-video result

These are real-video benchmark outputs, not the synthetic documentation scene.

<p align="center">
  <img src="docs/assets/real_demo/example1.gif" width="100%" alt="FoveaStream real-video benchmark example 1">
</p>

<p align="center">
  <img src="docs/assets/real_demo/example2.gif" width="100%" alt="FoveaStream real-video benchmark example 2">
</p>

The synthetic `VALVE / circle / polygon` showcase is intentionally **not used in this README**. For documentation screenshots/GIFs, prefer outputs generated from a real input video with `benchmark_gallery.py`.

---

# Quick start

```powershell
git switch main
git pull origin main

.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If this is a fresh checkout:

```powershell
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Put your own video in the repository root, for example:

```text
foveated_streaming_lib/
  example.mp4
```

## Run every major mode on your own video

```powershell
python bench\benchmark_gallery.py example.mp4 --clean
```

Output:

```text
output/gallery/example/
  INDEX.md
  report.json
  tile_policies.csv
  tile_matrix_runtime.json

  01_presets/
  02_core_actuators/
  03_tile_counts/
  04_tile_curves/
  05_tile_aggregation/
  06_custom_policies/
```

Every visual tile-policy variant gets its own folder with:

```text
preview.mp4
preview.gif
metrics.json
```

Open `output/gallery/example/INDEX.md` after the run. It is the human-readable index for the experiment.

For the larger numeric sweep:

```powershell
python bench\benchmark_gallery.py example.mp4 --matrix full --clean
```

---

# What FoveaStream can output

| Mode | Output | Best fit |
|---|---|---|
| Same-size foveated RGB | normal frame, same W×H | drop-in existing encoder/API |
| Logical tiles | exact N tiles with quality/resolution/QP policy | tiled transport, custom renderer |
| Encoder delta-QP | block map + original frame | H.264/H.265/AV1 hardware encoder adapter |
| Context + ROI | low-res scene + high-res relevant regions | VLM / machine vision |
| Temporal ROI reuse | only changed high-res ROI refreshes | video/VLM with persistent content |
| Background cache | changed background tiles | stable/static cameras |
| Packed atlas | context + changed ROIs packed into one image | APIs accepting one image |
| SEND/SKIP | scheduler decision | request/event pipelines |

All of these derive from the **same relevance state**. They are actuators, not separate ROI systems.

---

# Logical tiles

Logical tiles are application/transport tiles. They are separate from encoder macroblocks/QP blocks.

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

plan = planner.plan(quality_map)
```

`target_tiles` is exact. Examples:

```python
TilePlannerConfig(target_tiles=10)
TilePlannerConfig(target_tiles=25)
TilePlannerConfig(target_tiles=100)
TilePlannerConfig(target_tiles=137)
TilePlannerConfig(target_tiles=400)
```

Each tile contains normalized geometry plus policy output:

```text
index / row / column
x / y / w / h
relevance
distance
quality
resolution_scale
qp_delta
```

The core does not force a transport. A custom transport can consume the plan however it wants:

```python
for tile in plan.tiles:
    transport.send_tile(
        tile_id=tile.index,
        rect=(tile.x, tile.y, tile.w, tile.h),
        resolution_scale=tile.resolution_scale,
        qp_delta=tile.qp_delta,
    )
```

`send_tile()` above is adapter pseudocode.

---

# Supported tile degradation styles

Built-in distance-to-quality curves:

```text
linear
smoothstep
gaussian
exponential
power
```

Use them directly:

```python
TilePlannerConfig(target_tiles=100, curve="linear")
TilePlannerConfig(target_tiles=100, curve="smoothstep")
TilePlannerConfig(target_tiles=100, curve="gaussian", curve_strength=3.0)
TilePlannerConfig(target_tiles=100, curve="exponential", curve_strength=3.0)
TilePlannerConfig(target_tiles=100, curve="power", curve_strength=3.0)
```

Conceptually:

```text
ROI / high relevance
████████████████
██████████████▓▓
████████▓▓▓▓▒▒▒▒
▓▓▓▓▒▒▒▒░░░░░░░░
▒▒░░░░░░░░░░░░░░
far from relevance
```

More tiles give finer spatial control. Fewer tiles reduce control/metadata complexity.

```text
10 tiles   -> coarse policy
25 tiles   -> coarse/medium
100 tiles  -> useful general-purpose grid
400 tiles  -> fine spatial allocation
```

This is independent from encoder QP-block resolution.

---

# Tile relevance aggregation

When a logical tile covers many relevance-map samples, choose how they are combined:

```python
aggregation="max"
aggregation="p90"
aggregation="mean"
```

- `max` protects small important regions most aggressively.
- `p90` is a robust high-relevance policy.
- `mean` is more aggressive and can dilute a small important region inside a large tile.

---

# Write your own tile degradation function

You do **not** need to modify FoveaStream.

## 1. Custom distance → quality

```python
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

`distance` is normalized: `0` is closest/highest relevance and `1` is farthest/lowest relevance.

## 2. Context-aware quality policy

For full control, use `TilePolicyContext`:

```python
from foveastream import TilePolicyContext


def quality_policy(ctx: TilePolicyContext) -> float:
    return max(
        ctx.max_relevance,
        0.8 * ctx.p90_relevance,
        0.55 * ctx.mean_relevance,
    )

planner = TilePlanner(
    TilePlannerConfig(target_tiles=100),
    quality_fn=quality_policy,
)
```

The context exposes tile geometry and statistics including:

```text
index / row / column
x / y / w / h
center_x / center_y / area_fraction
relevance / distance
mean_relevance / max_relevance / p90_relevance
map_width / map_height
```

## 3. Custom resolution mapping

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
    TilePlannerConfig(target_tiles=100),
    resolution_fn=stepped_resolution,
)
```

Useful for hardware/transports that support discrete resolution levels.

## 4. Custom QP mapping

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

## Combine custom policies

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
```

Full extension-point documentation: [`docs/TILE_POLICIES.md`](docs/TILE_POLICIES.md).

---

# Benchmark all tile styles on one video

This is the easiest way to understand the API visually:

```powershell
python bench\benchmark_gallery.py example.mp4 --clean
```

The standard matrix generates examples for:

### Core actuators

```text
multi-ROI relevance
10 tiles / linear
25 tiles / smoothstep
100 tiles / gaussian
100 tiles / exponential
400 tiles / gaussian
encoder delta-QP map
temporal ROI SEND vs REUSE
packed ROI atlas
```

### Tile counts

```text
10 / 25 / 100 / 400
```

### Curves

```text
linear / smoothstep / gaussian / exponential / power
```

### Aggregation

```text
mean / p90 / max
```

### Built-in custom-policy examples

```text
gentle distance
focus cliff
context-aware quality
stepped resolution
tiered QP
```

The generated GIFs are deliberately tied to **your supplied video**, so documentation/experiments can show the real content rather than a canned synthetic scene.

---

# Benchmark your own policy

Create `my_policy.py`:

```python
from foveastream import TilePolicyContext


def quality(ctx: TilePolicyContext) -> float:
    return max(ctx.p90_relevance, 0.6 * ctx.max_relevance)


def resolution(ctx: TilePolicyContext, quality: float) -> float:
    return 1.0 if quality > 0.7 else 0.25


def qp(ctx: TilePolicyContext, quality: float) -> int:
    return round(18 - 24 * quality)
```

Then:

```powershell
python bench\benchmark_gallery.py example.mp4 `
  --custom-quality my_policy.py:quality `
  --custom-resolution my_policy.py:resolution `
  --custom-qp my_policy.py:qp `
  --clean
```

Your result appears under `09_user_policy/` with preview and metrics.

---

# Same-size drop-in middleware

For a normal camera → encoder/API pipeline:

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
)

optimized = optimizer.transform(frame_rgb, timestamp_s)
downstream.send(optimized.frame_rgb)
```

Default continuous-video behavior is `emit_policy="every_frame"`.

For a request/event pipeline you can explicitly use `emit_policy="when_send"`.

---

# Realtime bridge

If the camera can outrun processing, use the latest-frame bridge instead of building latency with an unbounded FIFO:

```python
from foveastream import FoveaStreamTransform, CallbackFrameSink, RealtimeBridge

optimizer = FoveaStreamTransform(preset="aggressive", auto_motion_proposals=True)
sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))

bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)

def on_camera_frame(frame, timestamp):
    bridge.submit(frame, timestamp)
```

This is intended for realtime vision: if processing falls behind, stale pending frames are replaced by the newest frame instead of accumulating latency.

---

# Context + ROI / temporal reuse

```python
out = adaptive.process(frame_rgb, timestamp_s)

images = [out.layered.context]
images += [
    roi.image
    for roi in out.layered.changed_rois
    if roi.image is not None
]

model.send_images(images)
```

Temporal cache compares against the **last emitted high-resolution ROI**, not merely frame `N-1`:

```text
frame 100  ROI A changed -> SEND
frame 101  ROI A same    -> REUSE
frame 102  ROI A same    -> REUSE
frame 103  ROI A changed -> SEND
```

A forced refresh timeout prevents indefinite stale reuse.

---

# Packed atlas

If a model/API accepts one image but not multiple images:

```python
if out.layered.atlas is not None:
    model.send_image(out.layered.atlas.image)
```

The atlas packs low-resolution scene context and changed high-resolution ROI crops into one image.

---

# Encoder delta-QP

For a smart encoder path, preserve the original source frame and consume:

```python
out.encoder_hints.block_size
out.encoder_hints.qp_delta_map
out.encoder_hints.rois
```

Conceptually:

```text
original NV12/RGB -------------------------+
                                          |
FoveaStream relevance -> delta-QP map -----+--> H.264/H.265/AV1 encoder
```

The core provides portable spatial hints. Direct NVENC, MediaCodec, VideoToolbox and VAAPI translation remains adapter/platform work; do not claim those adapters are already implemented.

---

# Adaptive bitrate / pixel controller

```python
AdaptiveBudgetConfig(
    target_bitrate_bps=2_000_000,
    target_pixel_fraction=0.20,
)
```

Feed actual encoder output back:

```python
optimizer.feedback_encoded(
    encoded_bytes=actual_size,
    duration_s=frame_duration,
)
```

The controller can adapt ROI budget, context resolution, peripheral scale, quality floor, falloff and QP strength instead of relying forever on a fixed preset.

---

# Presets

| Preset | Periphery | Falloff | Quality floor | Max ROI | ROI budget |
|---|---:|---:|---:|---:|---:|
| balanced | /8 | 2.8 | 0.04 | 8 | 30% |
| aggressive | /16 | 4.5 | 0.01 | 6 | 22% |
| extreme | /24 | 6.5 | 0.00 | 4 | 15% |

These are starting policies, not universal optimal settings.

---

# Real-video codec benchmark

Primary comparison uses the **same decoded frames and same libx264 settings** for baseline and FoveaStream output. That isolates the spatial transform better than comparing against the original source file, whose encoder history may be different.

| Video | Preset | Baseline H.264 | FoveaStream H.264 | H.264 saving | Context + ROI pixel saving |
|---|---|---:|---:|---:|---:|
| example1 | balanced | 824,328 B | 293,050 B | 64.45% | 92.26% |
| example1 | aggressive | 824,328 B | 236,596 B | **71.30%** | **96.26%** |
| example1 | extreme | 824,328 B | 218,618 B | 73.48% | 97.50% |
| example2 | balanced | 3,719,593 B | 2,846,879 B | 23.46% | 75.54% |
| example2 | aggressive | 3,719,593 B | 2,553,743 B | **31.34%** | **84.08%** |
| example2 | extreme | 3,719,593 B | 2,137,705 B | 42.53% | 89.61% |

Aggressive combined byte saving across both benchmark clips: **38.59%**.

These are benchmark-clip results, not universal compression guarantees. The next meaningful comparison for a downstream vision system is equal task quality/accuracy versus ordinary CRF reduction, global downscale and direct encoder-QP actuation.

Machine-readable benchmark data: [`docs/assets/real_demo/benchmark_presets_2026-09-14.json`](docs/assets/real_demo/benchmark_presets_2026-09-14.json).

---

# Documentation

- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) — public Python API.
- [`docs/TILE_POLICIES.md`](docs/TILE_POLICIES.md) — custom tile policies and callbacks.
- [`docs/BENCHMARK_GALLERY.md`](docs/BENCHMARK_GALLERY.md) — all-modes benchmark gallery.
- [`docs/ADAPTIVE_TRANSPORT.md`](docs/ADAPTIVE_TRANSPORT.md) — adaptive transport internals.
- [`docs/FRAME_MIDDLEWARE.md`](docs/FRAME_MIDDLEWARE.md) — source → transform → sink contract.
- [`docs/AGENT_INTEGRATION.md`](docs/AGENT_INTEGRATION.md) — integration contract for coding agents.
- [`docs/STATUS.md`](docs/STATUS.md) — exact implementation boundary/current handoff.
- [`AGENTS.md`](AGENTS.md) — invariants another coding agent must preserve.

---

# Implementation boundary

Implemented today:

```text
persistent multi-ROI tracking
class-agnostic motion proposals
external relevance evidence
continuous relevance/quality maps
same-size RGB actuator
logical tile planner
custom degradation/quality/resolution/QP callbacks
encoder delta-QP hint map
temporal ROI cache
background tile cache
context + ROI layered payload
packed atlas
adaptive bitrate/pixel controller
latest-frame realtime bridge
Python reference runtime
Rust core/control-plane primitives
benchmark + gallery tooling
```

Still platform integration work:

```text
direct CameraX / AVFoundation / OpenXR capture adapters
native NV12/YUV zero-copy hot path
direct NVENC / MediaCodec / VideoToolbox / VAAPI QP wiring
production WebRTC / RTP / GStreamer adapters
world-locked background cache for freely moving cameras
```

---

# License

The repository is available for evaluation and non-commercial research/development under [`LICENSE`](LICENSE).

Commercial production, OEM, SaaS, resale, paid SDK integration or other commercial use requires a separate written commercial license.

**Commercial licensing:** `p.duplinsky@gmail.com`
