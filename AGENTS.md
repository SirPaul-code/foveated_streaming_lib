# AGENTS.md — FoveaStream integration contract

This is the durable handoff for coding agents. Do not depend on chat history.

## Product boundary

FoveaStream is a provider-agnostic adaptive relevance transport/middleware layer for machine vision.

```text
existing source
      |
      v
timestamped frame + optional relevance evidence
      |
      v
FoveaStream
persistent predictive multi-ROI relevance state
      |
      +--> same-size optimized frame
      +--> context + changed high-res ROI layers
      +--> packed one-image atlas
      +--> exact-count logical tile plan
      +--> codec-block delta-QP hints
      +--> optional SEND/SKIP
      |
      v
existing encoder / model / recorder / callback / custom transport
```

The core must NOT depend on Gemini, OpenAI, WebRTC, GStreamer, CameraX, AVFoundation, Meta APIs, one detector class, one camera vendor, or one vision model.

## Read first

1. `README.md` — canonical user/integration overview and benchmark commands.
2. `docs/API_REFERENCE.md` — public Python surface.
3. `docs/TILE_POLICIES.md` — tile planner and custom policy callback contracts.
4. `docs/BENCHMARK_GALLERY.md` — all-modes arbitrary-video benchmark.
5. `docs/ADAPTIVE_TRANSPORT.md`
6. `docs/FRAME_MIDDLEWARE.md`
7. `docs/AGENT_INTEGRATION.md`
8. `docs/STATUS.md` — exact current checkpoint and limitations.
9. `docs/REALTIME_STREAMING_STATUS.md`
10. `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`

## Install / validate

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
cargo test --all-targets
cargo build --release
```

## Simplest drop-in frame path

```python
from foveastream import FoveaStreamTransform

optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="every_frame",
)

optimized = optimizer.transform(frame_rgb, timestamp_s)
downstream.send(optimized.frame_rgb)
```

`optimized.frame_rgb` preserves input width/height. Full runtime state is attached as `optimized.result`.

## Full adaptive transport path

```python
from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    LatencyBudget,
)

optimizer = AdaptiveTransportRuntime(AdaptiveTransportConfig(
    preset="aggressive",
    auto_motion_proposals=True,
    temporal_cache=True,
    background_cache=False,
    build_atlas=True,
    latency=LatencyBudget(
        encode_s=.015,
        network_s=.040,
        consumer_s=.020,
        safety_s=.015,
    ),
    budget=AdaptiveBudgetConfig(target_pixel_fraction=.20),
))

out = optimizer.process(frame_rgb, timestamp_s)
```

Available actuators:

```python
out.frame_rgb
out.layered.context
out.layered.changed_rois
out.layered.background_updates
out.layered.atlas
out.encoder_hints.qp_delta_map
out.process_result.quality_map
out.process_result.decision
```

Do not force every downstream consumer through the same actuator.

## Logical tile planner

Logical tiles are a first-class application/transport actuator. They are NOT the same thing as codec-native 16x16/32x32 QP blocks.

```python
from foveastream import TilePlanner, TilePlannerConfig

planner = TilePlanner(TilePlannerConfig(
    target_tiles=100,
    curve="gaussian",
    curve_strength=3.0,
    aggregation="max",
    min_quality=.04,
    min_resolution_scale=.125,
    fovea_qp_delta=-4,
    periphery_qp_delta=18,
))

plan = planner.plan(out.process_result.quality_map)
```

`target_tiles` is exact. Counts such as 10, 25, 100, 137 and 400 are valid.

Each `TileDecision` contains normalized geometry plus:

- `relevance`;
- `distance` from relevance;
- `quality`;
- `resolution_scale`;
- `qp_delta`.

Built-in curves:

```text
linear
smoothstep
gaussian
exponential
power
```

Aggregation modes:

```text
max   — safest for coarse tiles; preserve if any support is highly relevant
p90   — robust high-relevance aggregation
mean  — smooth/aggressive, but can dilute a small important region
```

### Custom tile-policy invariant

There are four deliberate customization stages. Preserve these contracts instead of adding one-off special cases in the planner.

#### Simple distance-only degradation

```python
def degradation(distance: float) -> float:
    return max(0.0, 1.0 - distance ** 1.7)

planner = TilePlanner(
    TilePlannerConfig(target_tiles=100),
    degradation_fn=degradation,
)
```

`distance` is `[0,1]`, where 0 is highly relevant. This owns the raw-quality stage.

#### Context-aware quality

```python
def quality(ctx: TilePolicyContext) -> float:
    return max(ctx.max_relevance, .8 * ctx.p90_relevance)

planner = TilePlanner(config, quality_fn=quality)
```

`TilePolicyContext` exposes tile geometry, center/area and mean/max/p90 relevance. `quality_fn` and `degradation_fn` are mutually exclusive because both own raw quality.

#### Custom resolution mapping

```python
def resolution(ctx, quality):
    return 1.0 if quality > .8 else .25

planner = TilePlanner(config, resolution_fn=resolution)
```

Configured `min_resolution_scale` remains a hard floor after the callback.

#### Custom QP mapping

```python
def qp(ctx, quality):
    return -6 if quality > .85 else 18

planner = TilePlanner(config, qp_fn=qp)
```

Custom QP is clamped to signed int8 at the generic API boundary. Native encoder adapters must apply their own real codec limits.

Callbacks may be combined as:

```python
TilePlanner(
    config,
    quality_fn=quality,
    resolution_fn=resolution,
    qp_fn=qp,
)
```

See `docs/TILE_POLICIES.md` and `examples/custom_tile_policy.py` before modifying this API.

### Reference tile realization

```python
from foveastream import apply_tile_plan
preview = apply_tile_plan(frame_rgb, plan)
```

`apply_tile_plan` downscales each tile according to `resolution_scale` and upscales it back into the same-size frame. It exists for visual verification/reference pipelines.

It is **not** a production tiled transport and not a zero-copy implementation. A real tiled transport should send the lower-resolution tile itself.

### Effective tile cost

`plan.effective_pixel_fraction` estimates:

```text
sum(tile_area_fraction * resolution_scale^2)
```

It is NOT encoded-byte savings.

### Tile planner vs encoder QP map

```text
continuous relevance field
     |
     +--> TilePlan
     |      exact N logical/application tiles
     |      scale + quality + QP suggestion
     |
     +--> EncoderSpatialHints
            codec block grid
            signed delta-QP values
```

Both can coexist. Never claim that a 100-tile logical plan means the codec has 100 coding blocks.

A TilePlan is policy metadata until a downstream adapter actually transmits/rasterizes/encodes tiles at the requested scale/quality.

## Encoder-hint invariant

`EncoderSpatialHints` is a portable contract, not proof that MediaCodec/NVENC/VideoToolbox/VAAPI is wired.

```text
EncoderSpatialHints
    -> platform encoder adapter
    -> native ROI/QP API
    -> actual encoded frame
```

Benchmark actual output bytes/quality before hardware-encoder claims.

## Temporal-cache invariant

`TemporalRoiCache` compares current ROI content against the **last emitted high-resolution state**, not only the previous frame.

```text
first observation     -> SEND
unchanged ROI         -> REUSE
unchanged ROI         -> REUSE
material change       -> SEND
max refresh timeout   -> SEND
```

Do not reset runtime/cache per frame.

## Background-cache invariant

`BackgroundTileCache` is opt-in for mostly static cameras/backgrounds. Do not enable blindly on a freely moving camera. A future moving-camera implementation should be world-locked using pose/homography/depth.

## ROI / relevance invariant

An ROI is NOT a semantic object class. It is spatial support whose loss of detail would disproportionately harm the downstream task.

Evidence may come from:

- residual motion;
- user selection;
- gaze/head direction;
- saliency;
- detector/segmenter output;
- OCR/text;
- depth/autofocus;
- AR anchors;
- hand/object interaction;
- task/planner state;
- downstream model feedback;
- remote operator/controller;
- future learned relevance models.

`EvidenceBus` fuses timestamped TTL-bound evidence. Dense-map fusion supports `max`, `noisy_or`, and clipped additive fusion.

Do not replace this abstraction with hardcoded face/person/car logic in core.

## Low-resolution analysis rule

Generic proposal generation should run on a cheap downscaled image where possible while preserving the original full-resolution source for output/encoding. `LowResProposalAdapter` formalizes this and keeps coordinates normalized.

## Latency-aware prediction

`LatencyBudget` may include capture, analysis, encode, network, decode, consumer/model and safety delay. The prediction horizon should preserve where relevance is expected to matter downstream, not only at capture time.

## Closed-loop budget rule

Presets are initial conditions, not guaranteed bitrate modes.

`AdaptiveBudgetController` may consume real encoded-byte and/or layered-pixel feedback to adjust:

- peripheral scale;
- falloff;
- ROI area budget/count;
- context scale;
- quality/uncertainty floors;
- peripheral delta-QP.

If a real encoder is integrated, call `feedback_encoded(...)` with actual encoded output size.

## Realtime callback path

```python
from foveastream import CallbackFrameSink, RealtimeBridge

sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
bridge = RealtimeBridge(optimizer, sink, queue_size=1, drop_policy="latest")
camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

Latest-frame queueing is deliberate. Latency-sensitive perception must not accumulate an unbounded stale-frame backlog.

Use `drop_policy="block"` only when every frame is mandatory and source backpressure is acceptable.

## Emit-policy invariant

`every_frame` is the safe default for continuous video/encoder paths.

`when_send` is opt-in for request/event pipelines. Do not silently apply SEND/SKIP to a transport requiring continuous cadence.

## Metadata invariant

`FramePacket.metadata` is opaque and must be preserved. Do not teach core about provider-specific RTP/camera/tracing metadata.

## Native Rust

Native exports include:

- `StreamMiddleware`;
- `LatencyBudget`;
- `AdaptiveBudgetController`;
- `EvidenceBus`;
- `RegionSignature` / `TemporalRegionCache`;
- `EncoderSpatialHints`;
- `TilePlannerConfig` / `TilePlan` / `plan_tiles`.

Python currently exposes richer arbitrary callback tile policies. Rust provides built-in deterministic tile curves; keep cross-language semantics aligned where they overlap.

The native core is synchronous. Platform capture threads, bounded queues, zero-copy buffers, encoder threads and sockets belong to host/platform adapters.

## Benchmark arbitrary videos

### One configuration

```bash
python bench/benchmark_suite.py example.mp4 --preset aggressive --tiles 100 --tile-curve gaussian
```

Runs same-encoder H.264 + adaptive transport for one selected configuration.

### All major modes/settings

```bash
python bench/benchmark_gallery.py example.mp4
```

This is the preferred exploratory/regression command. It creates:

```text
INDEX.md
report.json
tile_policies.csv
01_presets/
02_core_actuators/
03_tile_counts/
04_tile_curves/
05_tile_aggregation/
06_custom_policies/
```

`--matrix full` also adds strength and minimum-resolution sweeps.

Every tile policy gets `preview.mp4`, `preview.gif`, `metrics.json`.

A user policy can be loaded without editing FoveaStream:

```bash
python bench/benchmark_gallery.py example.mp4 \
  --custom-quality my_policy.py:quality \
  --custom-resolution my_policy.py:resolution \
  --custom-qp my_policy.py:qp
```

Do not confuse:

- actual H.264 byte measurements;
- actual context+ROI raster pixels;
- estimated logical-tile effective pixels;
- QP control metadata.

Read `docs/BENCHMARK_GALLERY.md` before changing benchmark semantics.

## Visual showcase

Generate all visual modes from a real video:

```bash
python examples/generate_showcase.py example.mp4 --outdir output/showcase_example
```

Reproducible docs showcase:

```bash
python examples/generate_showcase.py --synthetic
```

Showcase modes:

1. multi-ROI relevance;
2. 10 tiles / linear;
3. 25 tiles / smoothstep;
4. 100 tiles / Gaussian;
5. 100 tiles / exponential;
6. 400 tiles / Gaussian;
7. encoder delta-QP map;
8. temporal ROI SEND vs REUSE;
9. packed context+ROI atlas.

The `showcase` GitHub workflow regenerates `docs/assets/showcase/*.gif` from a deterministic scene after relevant main-branch changes.

## Multi-rate rule

Never assume capture FPS == optimizer FPS == output/model FPS.

```text
camera:      60 FPS
optimizer:   device dependent
IMU:        200 Hz
model:        1-10 requests/s or event-driven
```

Keep latency-sensitive queues bounded and preserve source timestamps.

## Performance rule

Python/OpenCV is reference/integration/benchmark code. Production hot paths should progressively use:

- Rust/native core;
- NV12/YUV instead of RGB round trips;
- zero/low-copy platform buffers;
- real hardware encoder ROI/QP controls;
- concrete tiled transport adapters where useful.

Do not claim zero-copy/hardware encoder/tiled packetization until a real adapter exists and is measured.

## Demo / regression

```bash
python examples/live_webcam.py --preset aggressive
python examples/adaptive_transport.py --video example.mp4 --preset aggressive --tiles 100
python examples/generate_showcase.py --synthetic
python bench/benchmark_suite.py example.mp4 --preset aggressive --tiles 100
python bench/benchmark_gallery.py example.mp4 --skip-preset-benchmarks --skip-showcase --no-gif
pytest -q
cargo test --all-targets
cargo build --release
```

## Engineering invariants

- Source and sink stay replaceable.
- Preserve source timestamps and opaque metadata.
- Preserve temporal state across frames.
- Zero/one/many ROIs are all valid.
- ROI area budget is a hard cap.
- Uncertainty spends bandwidth instead of silently losing targets.
- Temporal reuse has max-refresh protection.
- Static background cache is opt-in.
- Logical tiles and codec QP blocks remain separate abstractions.
- Tile custom policies stay pure/provider-independent callbacks.
- `apply_tile_plan` remains a reference visual path, not a fake production transport.
- `EveryFrame` is safe default for continuous video.
- `WhenSend` is explicit/opt-in.
- Realtime queues are bounded.
- Provider auth/network/model SDKs stay outside core.
- Concrete encoder/transport integrations stay outside relevance policy.
- Benchmark downstream task quality, not only appearance/bytes.

## Before handing work to another agent

Update `docs/STATUS.md` with exact branch/commit, implemented behavior, tests/CI, limitations and precise next steps. Never mark an adapter/platform as complete until implemented and measured.
