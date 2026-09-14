# FoveaStream

**Adaptive relevance-aware middleware for camera, video and machine-vision pipelines.**

FoveaStream sits between an existing frame source and an existing consumer. It maintains spatial/temporal relevance state and can spend less bitrate, fewer image pixels, less resolution, fewer requests, or fewer high-resolution updates on regions that matter less.

It is designed to be **inserted into an existing pipeline**, not to replace the camera, encoder, transport or model.

```text
camera / decoder / RTSP / WebRTC / file / AR glasses / robot
                              |
                              v
                        FoveaStream
                relevance + temporal state
                              |
        +---------------------+----------------------+------------------+
        |                     |                      |                  |
        v                     v                      v                  v
 same-size frame      context + changed ROIs   logical tile plan   encoder QP map
        |                     |                      |                  |
        v                     v                      v                  v
 encoder / stream           VLM/API            tiled transport     smart encoder
```

FoveaStream is **provider agnostic**. Core logic does not depend on Gemini, OpenAI, WebRTC, GStreamer, CameraX, AVFoundation, a specific detector class, a specific camera vendor or a specific model provider.

---

# Quick start: pull latest and test `example3.mp4`

If the repository is already cloned:

```powershell
git switch main
git pull origin main

.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If `.venv` does not exist yet:

```powershell
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Put any video in the repo root:

```text
foveated_streaming_lib/
  example3.mp4
```

## Run the complete benchmark suite

```powershell
python bench\benchmark_suite.py example3.mp4 `
  --preset aggressive `
  --tiles 100 `
  --tile-curve gaussian
```

Output:

```text
output/benchmark_suite/example3/
  example3_benchmark_suite.json
  example3_transport.json

  codec/
    example3_baseline_crf23.mp4
    example3_foveated_crf23.mp4
    example3_visualization.mp4
    example3_benchmark.json
```

The command prints:

```text
H.264 bytes saved
context + ROI pixels saved
codec path processing ms/frame
adaptive transport mean / p50 / p95 / p99
temporal ROI reuse
layered payload pixel fraction
logical tile count + curve
logical-tile effective pixel fraction
```

## Watch the adaptive runtime on your video

```powershell
python examples\adaptive_transport.py `
  --video example3.mp4 `
  --preset aggressive `
  --tiles 100 `
  --tile-curve gaussian
```

Headless:

```powershell
python examples\adaptive_transport.py `
  --video example3.mp4 `
  --preset aggressive `
  --tiles 100 `
  --tile-curve gaussian `
  --no-preview
```

## Generate the full visual showcase from your own video

```powershell
python examples\generate_showcase.py example3.mp4 `
  --outdir output\showcase_example3
```

This generates MP4 + GIF previews for multi-ROI relevance, multiple tile counts/curves, QP maps, temporal ROI reuse and the packed ROI atlas.

---

# What FoveaStream optimizes

FoveaStream does **not** define an ROI as a face, car, person or other hardcoded semantic class.

An ROI means:

> **Spatial support whose loss of detail would disproportionately hurt the current downstream task.**

Relevance may come from any combination of:

- residual motion;
- explicit user tap/rectangle;
- eye gaze or software gaze;
- AR anchor;
- saliency;
- OCR/text region;
- depth/autofocus;
- hand/object interaction;
- detector or segmenter output;
- application/task rules;
- downstream model feedback;
- remote operator input;
- a future learned relevance model.

The built-in fallback is deliberately class agnostic:

```text
frame t-1 + frame t
        |
        v
sparse feature tracking
        |
        v
global camera-motion estimate
        |
        v
warp previous frame
        |
        v
residual motion
        |
        v
connected components
        |
        +--> ROI proposal 1
        +--> ROI proposal 2
        +--> ROI proposal N
```

Those proposals enter a persistent multi-ROI tracker. Tracks are associated across time, predicted into the future, expanded under uncertainty and constrained by a hard total ROI-area budget.

Motion is only a fallback. A static crack, label or component may be task-critical while producing no motion, so applications can inject stronger task evidence at any time.

---

# Current architecture

```text
                              arbitrary evidence
                  motion / gaze / task / OCR / model / depth
                                      |
                                      v
                                EvidenceBus
                           weighted + TTL fusion
                                      |
                                      v
                          predictive multi-ROI state
                                      |
                     latency-aware future prediction
                                      |
                                      v
                          continuous relevance field
                                      |
     +------------------+------------------+------------------+------------------+
     |                  |                  |                  |                  |
     v                  v                  v                  v                  v
same-size RGB     context + ROIs      logical tiles       QP blocks        SEND/SKIP
     |                  |                  |                  |                  |
     |             temporal cache     N exact tiles     codec spatial      event/API
     |                  |                  |              quality hints      request
     |                  v                  v
     |             layered payload   per-tile quality
     |                  |             scale + QP
     |                  v                  |
     |             packed atlas            v
     |                               tiled transport
     +------------------------------ downstream --------------------------------+
```

The key design rule is that **all actuators derive from the same relevance state**. A host application uses whichever representation its downstream stack can actually consume.

---

# Actuator 1: same-size foveated frame

The easiest drop-in mode returns an ordinary frame with the same width and height as the input.

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
)

optimized = optimizer.transform(frame_rgb, timestamp_s)
downstream.send(optimized.frame_rgb)
```

This is useful with an existing ordinary encoder/API that accepts only a normal frame.

It reduces spatial entropy, but the decoded raster is still full-size. Therefore this mode is **not the same thing** as reducing actual model pixels.

---

# Actuator 2: context + high-resolution ROI

A consumer that accepts multiple images can receive:

```text
small whole-scene context
+
N high-resolution relevant regions
```

The global context keeps scene structure while high-resolution pixels are spent only where needed.

```python
out = optimizer.process(frame_rgb, timestamp_s)

images = [out.layered.context]
images += [
    region.image
    for region in out.layered.changed_rois
    if region.image is not None
]

model.send_images(images)
```

---

# Actuator 3: temporal ROI cache

A high-resolution ROI does not have to be resent simply because it is still present.

```text
frame 100: ROI A changed  -> SEND high-res ROI
frame 101: ROI A same     -> REUSE cached ROI
frame 102: ROI A same     -> REUSE cached ROI
frame 103: ROI A same     -> REUSE cached ROI
frame 104: ROI A changed  -> SEND high-res ROI
```

Change is compared against the **last emitted high-resolution state**, not just the previous frame. Slow drift therefore eventually accumulates enough change to trigger refresh.

A maximum refresh timeout prevents indefinite reuse.

---

# Actuator 4: background tile cache

For mostly static cameras, an optional image-space cache can emit only background tiles that changed materially or exceeded their refresh timeout.

```python
AdaptiveTransportConfig(
    background_cache=True,
)
```

Do not blindly enable this for a freely moving camera. Without world/camera compensation, global motion invalidates most image-space tiles.

---

# Actuator 5: layered payload

The adaptive transport can represent:

```text
low-resolution global context
+
changed high-resolution ROI enhancements
+
optional changed background tiles
```

The low-resolution base remains useful even if an enhancement packet is delayed or dropped.

---

# Actuator 6: packed context + ROI atlas

Some APIs accept one image but not a list. FoveaStream can pack global context plus only the currently changed high-resolution ROIs into one image with placement metadata.

```text
+----------------------------------+
| low-res whole-scene context      |
+----------------+-----------------+
| changed ROI 1  | changed ROI 2   |
+----------------+-----------------+
| changed ROI 3                    |
+----------------------------------+
```

```python
if out.layered.atlas is not None:
    model.send_image(out.layered.atlas.image)
```

---

# Actuator 7: logical relevance tiles

This is the explicit tile mode.

It is **separate from codec-native 16x16/32x32 QP blocks**.

You choose the exact number of logical transport tiles:

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

`target_tiles` is exact. Examples:

```python
TilePlannerConfig(target_tiles=10)
TilePlannerConfig(target_tiles=25)
TilePlannerConfig(target_tiles=100)
TilePlannerConfig(target_tiles=400)
```

The planner partitions the whole frame into exactly that many near-rectangular tiles while respecting the frame aspect ratio.

Each tile receives:

```text
normalized rectangle
relevance 0..1
distance-from-relevance 0..1
quality 0..1
recommended resolution scale
recommended delta-QP
```

Example consumer:

```python
for tile in plan.tiles:
    transport.send_tile(
        x=tile.x,
        y=tile.y,
        w=tile.w,
        h=tile.h,
        resolution_scale=tile.resolution_scale,
        qp_delta=tile.qp_delta,
    )
```

`transport.send_tile(...)` is host-adapter pseudocode. FoveaStream produces the plan; the actual WebRTC/custom transport/codec adapter decides how to apply it.

## Tile aggregation

A tile must reduce many relevance pixels to one tile relevance value.

```python
aggregation="max"
aggregation="p90"
aggregation="mean"
```

- `max`: preserve a tile strongly if any part is highly relevant; safest for coarse grids.
- `p90`: robust high-relevance aggregation without one-pixel dominance.
- `mean`: smoothest/most aggressive, but a tiny important region can be diluted inside a large tile.

Default is `max`.

## Built-in degradation curves

The curve maps normalized **distance from relevance** to raw fidelity:

```text
distance = 0.0 -> tile is highly relevant
distance = 1.0 -> tile is maximally irrelevant
```

Built-ins:

```python
curve="linear"
curve="smoothstep"
curve="gaussian"
curve="exponential"
curve="power"
```

Strength:

```python
TilePlannerConfig(
    target_tiles=100,
    curve="gaussian",
    curve_strength=4.5,
)
```

Higher strength generally makes quality fall off more aggressively as relevance decreases.

## Custom degradation function

Python callers can supply their own function directly.

The callback receives `distance` in `[0,1]` and returns raw fidelity in `[0,1]`.

```python
from foveastream import TilePlanner, TilePlannerConfig


def my_degradation(distance: float) -> float:
    # Full fidelity near relevance, then aggressive nonlinear falloff.
    return max(0.0, 1.0 - distance ** 1.7)

planner = TilePlanner(
    TilePlannerConfig(
        target_tiles=100,
        min_quality=0.03,
        min_resolution_scale=0.10,
    ),
    degradation_fn=my_degradation,
)

plan = planner.plan(out.process_result.quality_map)
```

`min_quality` is applied after the curve, so the custom function cannot accidentally drive a tile below the configured quality floor.

## Resolution scaling

The planner maps tile quality to a suggested spatial scale:

```text
quality 1.0 -> resolution_scale 1.0
quality 0.0 -> min_resolution_scale
```

If:

```python
min_resolution_scale=0.125
```

then a lowest-quality tile may be represented at roughly `1/8` width and `1/8` height of its native tile resolution.

The exact transport/encoder decides whether it can actually consume that scale.

## Tile QP suggestion

The same tile quality also maps to a suggested delta-QP:

```python
fovea_qp_delta=-4
periphery_qp_delta=18
```

Highly relevant tiles receive a lower/better QP; irrelevant tiles receive a higher/worse QP.

## Effective tile pixel fraction

The planner exposes:

```python
plan.effective_pixel_fraction
```

It estimates the raster cost of a multi-resolution tiled representation:

```text
sum(tile_area_fraction * tile_resolution_scale^2)
```

Example:

```python
print(plan.tile_count)
print(plan.effective_pixel_fraction)
```

This is an **estimated pixel load**, not encoded-byte savings. Actual byte savings depend on the transport/codec implementation.

## Rasterize a tile plan

For preview or for an application that wants a discrete tile quality field:

```python
from foveastream import rasterize_tile_plan

q = rasterize_tile_plan(
    plan,
    frame_width,
    frame_height,
    field="quality",
)
```

## Visualize a tile plan

```python
from foveastream import render_tile_plan

preview_rgb = render_tile_plan(frame_rgb, plan)
```

For <=25 tiles, quality labels are drawn automatically. Large grids show boundaries + heat field without unreadable labels.

---

# Logical tiles vs encoder QP blocks

These solve related but different integration problems.

```text
continuous relevance field
        |
        +--> logical TilePlan
        |      exact N tiles: 10 / 25 / 100 / 400 / ...
        |      per-tile scale + quality + QP suggestion
        |      useful for custom transport / tiled VLM / layered streaming
        |
        +--> codec QP map
               fixed codec block size, e.g. 16x16
               useful for NVENC / MediaCodec / VideoToolbox / VAAPI adapters
```

You may use both simultaneously.

A 100-tile transport plan does **not** mean the encoder itself has only 100 coding blocks.

---

# Actuator 8: encoder spatial hints / delta-QP map

A capable encoder can keep the original source frame and consume spatial quality hints instead of receiving an already degraded RGB image.

```text
original frame ----------------------------+
                                          |
FoveaStream relevance -> block delta-QP --+--> encoder
```

Portable output:

```python
out.encoder_hints.qp_delta_map
out.encoder_hints.block_size
out.encoder_hints.rois
```

The QP map is signed `int8`, row-major, and independent of one vendor API.

Direct MediaCodec, NVENC, VideoToolbox and VAAPI adapters still need to translate that portable contract into each encoder's native API.

Do not claim hardware-encoder savings until a concrete adapter consumes the map and is benchmarked.

---

# Actuator 9: SEND / SKIP

Continuous video defaults to:

```python
emit_policy="every_frame"
```

Request/event-driven pipelines may opt into:

```python
emit_policy="when_send"
```

The scheduler can suppress a whole redundant request/frame for consumers that do not require continuous cadence.

Do **not** silently apply SEND/SKIP to a continuous video track.

---

# Multi-source relevance fusion

`EvidenceBus` accepts independent timestamped evidence sources with their own TTL and weight.

A source may publish:

- ROI proposals;
- points;
- dense relevance maps.

Fusion modes include:

```text
max
noisy_or
add
```

The bus understands relevance, not semantic classes.

---

# Low-resolution analysis, full-resolution preservation

Relevance analysis does not need to run at full camera resolution.

```text
1080p / 4K source
      |
      +----> 256 px analysis path ---> relevance
      |
      +----> original frame ----------> output/encoder
```

Normalized ROI coordinates let cheap analysis drive full-resolution fidelity decisions.

---

# Predictive multi-ROI state

The runtime supports zero, one or many simultaneous relevant regions.

Track state persists across frames. Prediction can include:

```text
region velocity
region size change
staleness
uncertainty
encode latency
network latency
consumer latency
safety margin
```

The protected area therefore represents where relevance is expected to matter downstream, not only where it was at capture time.

---

# Hard ROI-area budget

Presets enforce a hard total ROI-area budget.

If the next predicted ROI would exceed the remaining budget, it is center-preserving/aspect-preserving shrunk to fit.

This prevents a multi-ROI scene from silently turning into an almost-full-resolution frame.

---

# Adaptive bitrate / pixel-budget controller

A fixed preset can be only the initial condition.

```python
from foveastream import AdaptiveBudgetConfig

budget = AdaptiveBudgetConfig(
    target_bitrate_bps=2_000_000,
    target_pixel_fraction=0.20,
)
```

The closed-loop controller can adjust:

- peripheral scale;
- falloff width;
- total ROI budget;
- maximum active ROI count;
- context scale;
- quality floor;
- uncertainty floor;
- peripheral delta-QP.

Feed real encoded output back when a real encoder is attached:

```python
optimizer.feedback_encoded(
    encoded_bytes=encoded_bytes,
    duration_s=frame_duration,
)
```

Conceptually:

```text
actual bitrate > target
        |
        v
increase optimization strength
        |
        v
smaller spatial payload / larger peripheral QP
```

---

# Full adaptive API

```python
from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    LatencyBudget,
)

optimizer = AdaptiveTransportRuntime(
    AdaptiveTransportConfig(
        preset="aggressive",
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=False,
        build_atlas=True,
        latency=LatencyBudget(
            encode_s=0.015,
            network_s=0.040,
            consumer_s=0.020,
            safety_s=0.015,
        ),
        budget=AdaptiveBudgetConfig(
            target_pixel_fraction=0.20,
        ),
    )
)

out = optimizer.process(frame_rgb, timestamp_s)
```

Important outputs:

```python
out.frame_rgb

out.process_result.rois
out.process_result.tracks
out.process_result.quality_map
out.process_result.qp_map
out.process_result.decision

out.layered.context
out.layered.changed_rois
out.layered.background_updates
out.layered.atlas

out.encoder_hints.qp_delta_map
out.payload_pixel_fraction
out.controller_state
```

Then optionally build logical tiles from the same relevance field:

```python
plan = planner.plan(out.process_result.quality_map)
```

---

# Callback-driven realtime integration

```python
from foveastream import CallbackFrameSink, FoveaStreamTransform, RealtimeBridge

optimizer = FoveaStreamTransform(
    preset="aggressive",
    emit_policy="every_frame",
)

sink = CallbackFrameSink(
    lambda packet: downstream.send(packet.frame_rgb)
)

bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)

camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

If capture temporarily outruns processing, `latest` replaces stale pending frames with the newest state instead of building an unbounded latency queue.

```text
60 FPS source
     |
     v
bounded latest-frame queue
     |
     v
optimizer
```

Use `drop_policy="block"` only when every input frame must be processed and backpressure is acceptable.

---

# Presets

| Preset | Peripheral source | Context scale | Max ROIs | Hard ROI-area budget | Peripheral quality floor |
|---|---:|---:|---:|---:|---:|
| `balanced` | 1/8 per axis | 0.25 | 8 | 30% | `0.04 + uncertainty` |
| `aggressive` | **1/16 per axis** | **0.15** | **6** | **22%** | `0.01 + uncertainty` |
| `extreme` | 1/24 per axis | 0.10 | 4 | 15% | `0.00 + uncertainty` |

For general experiments, `aggressive` is the recommended starting point.

`context_scale=0.15` means 15% width × 15% height, so the global context alone contains only **2.25% of full-frame pixels** before ROI enhancements are added.

---

# Visual functionality showcase

The documentation showcase is generated by the repository itself:

```powershell
python examples\generate_showcase.py --synthetic
```

or from your own video:

```powershell
python examples\generate_showcase.py example3.mp4 `
  --outdir output\showcase_example3
```

The `showcase` GitHub workflow regenerates the documentation GIFs from a deterministic synthetic multi-motion scene after relevant changes land on `main`.

## Multi-ROI relevance

<p align="center">
  <img src="docs/assets/showcase/01_multi_roi.gif" width="55%" alt="FoveaStream multi-ROI relevance">
</p>

## 10 tiles — linear degradation

<p align="center">
  <img src="docs/assets/showcase/02_tiles_10_linear.gif" width="55%" alt="FoveaStream 10 tile linear degradation">
</p>

## 25 tiles — smoothstep degradation

<p align="center">
  <img src="docs/assets/showcase/03_tiles_25_smoothstep.gif" width="55%" alt="FoveaStream 25 tile smoothstep degradation">
</p>

## 100 tiles — Gaussian degradation

<p align="center">
  <img src="docs/assets/showcase/04_tiles_100_gaussian.gif" width="55%" alt="FoveaStream 100 tile Gaussian degradation">
</p>

## 100 tiles — exponential degradation

<p align="center">
  <img src="docs/assets/showcase/05_tiles_100_exponential.gif" width="55%" alt="FoveaStream 100 tile exponential degradation">
</p>

## 400 tiles — Gaussian degradation

<p align="center">
  <img src="docs/assets/showcase/06_tiles_400_gaussian.gif" width="55%" alt="FoveaStream 400 tile Gaussian degradation">
</p>

## Encoder delta-QP field

<p align="center">
  <img src="docs/assets/showcase/07_qp_map.gif" width="55%" alt="FoveaStream encoder QP map">
</p>

## Temporal ROI cache — SEND vs REUSE

<p align="center">
  <img src="docs/assets/showcase/08_temporal_reuse.gif" width="55%" alt="FoveaStream temporal ROI reuse">
</p>

## Packed context + changed ROI atlas

<p align="center">
  <img src="docs/assets/showcase/09_roi_atlas.gif" width="55%" alt="FoveaStream packed ROI atlas">
</p>

---

# Existing real-video GIFs

The original real-video GIFs are kept unchanged.

<p align="center">
  <img src="docs/assets/real_demo/example1.gif" width="100%" alt="Foveated streaming demo 1">
</p>

<p align="center">
  <img src="docs/assets/real_demo/example2.gif" width="100%" alt="Foveated streaming demo 2">
</p>

---

# Real-video codec benchmark

The checked-in benchmark uses two real approximately 60 FPS H.264 phone videos. FoveaStream processes approximately 30 FPS and compares the optimized output against a **baseline re-encode of exactly the same decoded frames**.

Both arms use:

```text
encoder:       libx264
preset:        veryfast
CRF:           23
pixel format:  yuv420p
```

This avoids claiming savings merely because the original phone file used unrelated encoder settings.

Reference environment:

```text
CPU:      AMD EPYC 9V74 80-Core Processor
Python:   3.13.5
OpenCV:   4.13.0
NumPy:    2.3.5
FFmpeg:   7.1.5
```

## Aggressive preset

| Video | Same-encoder baseline | FoveaStream H.264 | H.264 bytes saved | Context + ROI pixels saved | Processing | Active ROIs |
|---|---:|---:|---:|---:|---:|---:|
| `example1.mp4` | 824.3 kB | **236.6 kB** | **71.30%** | **96.26%** | **20.16 ms/frame (~49.6 FPS)** | 1.13 mean / 3 max |
| `example2.mp4` | 3.720 MB | **2.554 MB** | **31.34%** | **84.08%** | **29.28 ms/frame (~34.2 FPS)** | 3.30 mean / 6 max |

Across both clips together:

```text
same-encoder baseline: 4.544 MB
aggressive output:     2.790 MB
byte reduction:        38.59%
```

These timings are reference-host measurements, not universal device latency claims.

Machine-readable data:

[`docs/assets/real_demo/benchmark_presets_2026-09-14.json`](docs/assets/real_demo/benchmark_presets_2026-09-14.json)

## Full preset results — `example1.mp4`

| Preset | Output | H.264 saving | Context + ROI pixel saving | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 293.1 kB | **64.45%** | **92.26%** | 1.49% | 6.25% |
| aggressive | **236.6 kB** | **71.30%** | **96.26%** | 1.49% | 6.25% |
| extreme | 218.6 kB | **73.48%** | **97.50%** | 1.49% | 6.25% |

## Full preset results — `example2.mp4`

| Preset | Output | H.264 saving | Context + ROI pixel saving | Mean ROI area | Max ROI area |
|---|---:|---:|---:|---:|---:|
| balanced | 2.847 MB | **23.46%** | **75.54%** | 18.19% | **30.00% cap** |
| aggressive | **2.554 MB** | **31.34%** | **84.08%** | 13.66% | **22.00% cap** |
| extreme | 2.138 MB | **42.53%** | **89.61%** | 9.38% | **15.00% cap** |

The second clip has materially more simultaneous relevant regions, which is why the hard multi-ROI budget matters.

---

# Benchmark metric definitions

## H.264 byte saving

```text
same decoded frames
      |
      +--> ordinary libx264 CRF23 ---------- baseline bytes
      |
      +--> FoveaStream RGB -> libx264 CRF23 - optimized bytes
```

This measures whether reduced spatial entropy gives an ordinary codec fewer bytes to encode.

## Context + ROI pixel saving

This is a different actuator:

```text
full frame pixels
vs.
low-res context pixels + high-res ROI pixels
```

It estimates actual image pixels presented to a compatible consumer.

## Temporal ROI reuse

Fraction of persistent high-resolution ROI instances that reuse the last emitted state instead of transmitting a new crop.

## Layered pixel fraction

Pixel count of context + changed high-resolution enhancements divided by the full frame pixel count.

## Logical-tile effective pixel fraction

Estimated multi-resolution raster load:

```text
sum(tile_area_fraction * resolution_scale^2)
```

This is **not encoded bytes**.

## Atlas pixel fraction

Equivalent size of the packed one-image context+ROI representation.

## Delta-QP statistics

Statistics over the portable encoder block map. These are not hardware-encoder byte-savings claims until a concrete encoder adapter uses them.

---

# Benchmark your own video

Recommended full suite:

```powershell
python bench\benchmark_suite.py example3.mp4 `
  --preset aggressive `
  --tiles 100 `
  --tile-curve gaussian
```

All presets:

```powershell
'balanced','aggressive','extreme' | ForEach-Object {
  python bench\benchmark_suite.py example3.mp4 `
    --preset $_ `
    --tiles 100 `
    --tile-curve gaussian `
    --outdir "output\benchmark_$_"
}
```

Compare tile counts:

```powershell
10,25,100,400 | ForEach-Object {
  python bench\benchmark_suite.py example3.mp4 `
    --preset aggressive `
    --tiles $_ `
    --tile-curve gaussian `
    --outdir "output\tiles_$_"
}
```

Compare curves:

```powershell
'linear','smoothstep','gaussian','exponential','power' | ForEach-Object {
  python bench\benchmark_suite.py example3.mp4 `
    --preset aggressive `
    --tiles 100 `
    --tile-curve $_ `
    --outdir "output\curve_$_"
}
```

Mostly static camera + background cache:

```powershell
python bench\benchmark_suite.py example3.mp4 `
  --preset aggressive `
  --background-cache
```

Adaptive layered pixel target:

```powershell
python bench\benchmark_suite.py example3.mp4 `
  --preset aggressive `
  --target-pixel-fraction 0.20
```

Multiple videos:

```powershell
python bench\benchmark_suite.py example3.mp4 example4.mp4 example5.mp4 `
  --preset aggressive
```

---

# Realtime behavior

The processing path is causal: frame `N` does not require future frame `N+1`.

Capture FPS, analysis FPS, optimizer FPS and downstream/model FPS may differ.

```text
camera:                60 FPS
local evidence:        30-200 Hz depending on source
optimizer:             device dependent
remote model:           1-10 requests/s or event-driven
```

The bounded latest-frame bridge is designed for latency-sensitive capture when the source can temporarily outrun processing.

The Python/OpenCV reference benchmark already demonstrates approximately 30 FPS processing on both checked-in clips on the benchmark host. Production target-device performance must be measured on the actual target hardware.

---

# Native Rust

The repository contains the native Rust runtime/control plane as well as a native logical tile planner.

```rust
use foveastream::{
    EmitPolicy,
    FrameInput,
    InnovationSignals,
    StreamMiddleware,
    TilePlannerConfig,
    plan_tiles,
};

let mut optimizer = StreamMiddleware::aggressive();
optimizer.set_emit_policy(EmitPolicy::EveryFrame);

let output = optimizer.process_rgb8(
    FrameInput {
        rgb8: frame,
        width,
        height,
        timestamp_s,
    },
    &proposals,
    &points,
    InnovationSignals::default(),
)?;

if let Some(result) = output {
    let tiles = plan_tiles(
        &result.quality_map,
        width,
        height,
        &TilePlannerConfig::default(),
    );

    downstream.send(&result.foveated_rgb8)?;
}
```

Native transport primitives also include latency budgets, adaptive control, evidence records, region signatures, temporal region caching and portable encoder spatial hints.

The Python/OpenCV layer remains the reference integration, visualization and benchmark environment.

---

# Installation

## Windows

```powershell
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

Manual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Development dependencies:

```powershell
python -m pip install -r requirements-dev.txt
```

## Linux / macOS

```bash
git clone https://github.com/SirPaul-code/foveated_streaming_lib.git
cd foveated_streaming_lib
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
```

FFmpeg with `libx264` is required for MP4 codec benchmarks/showcase generation. Core middleware does not require FFmpeg.

---

# Validation

Python:

```powershell
pytest -q
```

Rust:

```powershell
cargo test --all-targets
cargo build --release
```

CLI smoke checks:

```powershell
python examples\adaptive_transport.py --help
python examples\generate_showcase.py --help
python bench\benchmark_suite.py --help
```

CI covers Python plus Rust test/release builds on Ubuntu, Windows and macOS.

---

# Current implementation status

## Implemented

- causal frame-by-frame runtime;
- provider-independent source/transform/sink boundary;
- same-size drop-in optimized frame;
- bounded latest-frame realtime bridge;
- class-agnostic residual-motion proposals;
- arbitrary external ROI evidence;
- multi-source relevance fusion with TTL;
- persistent predictive multi-ROI tracking;
- hard total ROI-area budget;
- latency-aware future relevance prediction;
- low-resolution analysis/full-resolution preservation pattern;
- context + multiple high-resolution ROI views;
- temporal high-resolution ROI reuse;
- optional static-background tile cache;
- layered context + changed-ROI payload;
- packed one-image context+ROI atlas;
- **exact-count logical tile planner**;
- built-in linear/smoothstep/Gaussian/exponential/power tile degradation;
- custom Python degradation callback;
- per-tile quality/resolution-scale/QP recommendations;
- logical-tile effective-pixel estimate;
- portable signed delta-QP encoder block map;
- adaptive bitrate/pixel-budget controller;
- SEND/SKIP recommendation;
- Python reference/integration API;
- Rust native control-plane + logical tile planner;
- reproducible codec benchmark;
- reproducible adaptive transport benchmark;
- one-command combined benchmark suite;
- reproducible visual showcase generator.

## Still platform/adapter work

- direct NV12/YUV hot path throughout the full runtime;
- zero-copy AHardwareBuffer / CVPixelBuffer / DMA-BUF integration;
- CameraX/Camera2 adapter;
- AVFoundation adapter;
- OpenXR/Meta camera adapter;
- direct MediaCodec spatial-QP/ROI adapter;
- direct NVENC spatial-QP/ROI adapter;
- direct VideoToolbox/VAAPI adapter;
- concrete WebRTC/RTP/GStreamer layered/tiled packetization;
- world-locked background cache for freely moving cameras;
- stateful C ABI for the full high-level adaptive runtime;
- target-device p50/p95/energy matrix;
- broad downstream task-quality validation.

A logical tile plan is policy metadata until the downstream adapter actually sends/encodes those tiles at their requested scale/quality.

A portable QP map is policy metadata until a real encoder adapter consumes it.

---

# Repository layout

```text
src/
  middleware.rs       native drop-in middleware
  streaming.rs        native runtime / ProcessResult
  transport.rs        adaptive transport control-plane primitives
  tiles.rs            native logical tile planner
  roi.rs              multi-ROI tracker + hard spatial budget
  predictive.rs       prediction / uncertainty primitives
  scheduler.rs        SEND/SKIP controller

python/foveastream/
  middleware.py       FramePacket / transform / RealtimeBridge
  streaming.py        reference runtime / motion proposals / ROI tracking
  optimization.py     adaptive transport stack
  tiles.py            logical tile planner + custom degradation + visualization

examples/
  live_webcam.py
  custom_sink_adapter.py
  adaptive_transport.py
  generate_showcase.py

bench/
  real_video_visualization.py
  benchmark_transport_stack.py
  benchmark_suite.py

docs/
  ADAPTIVE_TRANSPORT.md
  FRAME_MIDDLEWARE.md
  AGENT_INTEGRATION.md
  REALTIME_STREAMING_STATUS.md
  PREDICTIVE_ATTENTION_ARCHITECTURE.md
  STATUS.md

AGENTS.md              durable coding-agent integration contract
```

---

# For coding agents

Read in this order:

1. [`AGENTS.md`](AGENTS.md)
2. [`README.md`](README.md)
3. [`docs/ADAPTIVE_TRANSPORT.md`](docs/ADAPTIVE_TRANSPORT.md)
4. [`docs/FRAME_MIDDLEWARE.md`](docs/FRAME_MIDDLEWARE.md)
5. [`docs/STATUS.md`](docs/STATUS.md)

Repository documentation is the durable handoff. Do not depend on chat history.

---

# License / commercial use

The repository is available for evaluation and permitted non-commercial use under the included [`LICENSE`](LICENSE).

For **commercial use, OEM integration, redistribution or commercial licensing**, contact:

**p.duplinsky@gmail.com**

The current runtime does not require a licensing server, telemetry or a paywall.
