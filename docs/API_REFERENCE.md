# FoveaStream Python API reference

This file documents the public Python surface exported by `foveastream` in practical groups.

For architecture, start with `README.md`. For custom logical tile policies, read `docs/TILE_POLICIES.md`. For the all-modes benchmark, read `docs/BENCHMARK_GALLERY.md`.

---

# 1. Spatial primitives

## `Roi`

Normalized rectangle:

```python
from foveastream import Roi

roi = Roi(
    x=0.20,
    y=0.30,
    w=0.15,
    h=0.10,
    confidence=0.95,
)
```

Coordinates are normalized to `[0,1]`.

Useful method:

```python
roi = roi.clipped()
```

---

## `Falloff`

Built-in continuous foveation curve enum:

```python
Falloff.LINEAR
Falloff.SMOOTHSTEP
Falloff.GAUSSIAN
```

This is the continuous full-frame quality-field falloff. It is different from logical tile curves.

---

## `FoveationConfig`

```python
from foveastream import FoveationConfig, Falloff

cfg = FoveationConfig(
    radius_x=0.12,
    radius_y=0.12,
    falloff_x=0.28,
    falloff_y=0.28,
    peripheral_scale=0.25,
    peripheral_desaturate=0.0,
    gamma=1.0,
    falloff=Falloff.SMOOTHSTEP,
)
```

Main fields:

| Field | Meaning |
|---|---|
| `radius_x`, `radius_y` | protected radius around point relevance |
| `falloff_x`, `falloff_y` | spatial width of quality decay |
| `peripheral_scale` | lowest source scale used by same-size foveation |
| `gamma` | continuous quality-curve shaping |
| `falloff` | linear/smoothstep/Gaussian continuous falloff |

---

## `quality_map(...)`

Build a dense `HxW float32` relevance/quality field from points, ROIs and/or a custom map.

```python
from foveastream import quality_map, Roi

q = quality_map(
    frame_rgb.shape,
    points=[(0.5, 0.5)],
    rois=[Roi(0.10, 0.20, 0.20, 0.15)],
    config=cfg,
)
```

A supplied `custom_map` is fused with the generated map using per-pixel maximum.

---

## `foveate(frame_rgb, qmap, config=None)`

Reference same-size spatial foveation.

```python
optimized_rgb = foveate(frame_rgb, q, cfg)
```

The output keeps the input raster width/height.

---

## `context_and_roi_views(...)`

Create one low-resolution whole-scene context image plus high-resolution ROI crops.

```python
views = context_and_roi_views(
    frame_rgb,
    rois,
    context_scale=0.15,
    roi_max_side=512,
)

context = views[0]
roi_images = views[1:]
```

---

## `qp_delta_map(...)`

Convert a dense quality field to a block delta-QP map.

```python
qp = qp_delta_map(
    q,
    block=16,
    fovea_delta=-4,
    periphery_delta=18,
)
```

This is a portable policy map. A concrete encoder adapter must translate it to the encoder's native API.

---

## `depth_focus_map(...)`

Generate a soft relevance field around the depth at a selected image point.

```python
q_depth = depth_focus_map(
    depth_map,
    focus_xy=(0.5, 0.5),
    relative_tolerance=0.08,
    softness=2.0,
)
```

---

## `pixel_budget(views)`

```python
pixels = pixel_budget([context, *roi_images])
```

Returns the sum of image raster pixels, not encoded bytes.

---

# 2. Focus tracking primitives

## `FocusCandidate`

```python
FocusCandidate(x=0.5, y=0.4, confidence=0.9, weight=1.0)
```

## `FocusTrackerConfig`

Controls alpha/beta tracking, prediction latency and camera field of view.

## `FocusTracker`

Useful methods:

```python
FocusTracker.fuse(candidates)
tracker.update(measurement_xy, dt_s)
tracker.predicted()
tracker.compensate_imu(point, gyro_yaw_rad_s, gyro_pitch_rad_s, dt_s)
```

IMU compensation models camera ray motion; IMU is not treated as gaze.

---

# 3. ROI proposals and tracking

## `RoiProposal`

Class-agnostic evidence that a rectangle deserves fidelity.

```python
from foveastream import Roi, RoiProposal

proposal = RoiProposal(
    roi=Roi(0.2, 0.3, 0.15, 0.10),
    confidence=0.95,
    priority=2.0,
    source="task-detector",
    track_hint=None,
)
```

`score = confidence * priority`.

The core does not interpret the semantic class of `source`.

---

## `RoiTrackerConfig`

Main fields:

```text
max_tracks
association_iou
association_center_distance
smoothing
velocity_smoothing
max_stale_s
confidence_decay_per_s
uncertainty_growth_per_s
prediction_horizon_s
min_confidence
proposal_merge_iou
pixel_budget_fraction
```

`pixel_budget_fraction` is a hard total normalized ROI-area budget.

---

## `RoiTrack`

Persistent ROI state containing:

```text
id
roi
velocity
confidence
priority
source
age_s
stale_s
```

---

## `MultiRoiTracker`

```python
tracker = MultiRoiTracker(RoiTrackerConfig(max_tracks=8))
rois = tracker.update(proposals, dt_s=1/30)
tracker.reset()
```

The tracker deduplicates proposals, associates them with persistent tracks, predicts forward and applies the hard ROI-area budget.

---

## `roi_iou(a, b)`

Normalized rectangle intersection-over-union helper.

---

# 4. Automatic class-agnostic motion proposals

## `MotionDetectorConfig`

Main fields:

```text
analysis_width
max_proposals
min_area_ratio
max_area_ratio
padding
mad_multiplier
absolute_threshold
```

---

## `ClassAgnosticMotionRoiDetector`

```python
detector = ClassAgnosticMotionRoiDetector(
    MotionDetectorConfig(analysis_width=256)
)

proposals = detector.propose(frame_rgb)
```

The detector estimates global camera motion, warps the previous frame and finds residual-motion connected components. It does not require person/car/face classes.

Static task-important content should come from another evidence source.

---

# 5. SEND/SKIP scheduler

## `InnovationSignals`

```python
InnovationSignals(
    pose_novelty=0.0,
    roi_prediction_error=0.0,
    residual_motion=0.0,
    scene_change=0.0,
    semantic_uncertainty=0.0,
    uncertainty_radius=0.0,
    hard_trigger=False,
)
```

## `SchedulerConfig`

Controls score threshold, min/max refresh interval, uncertainty hard limit and signal weights.

## `SendDecision`

```text
send: bool
score: float
reason: str
```

## `AdaptiveScheduler`

```python
scheduler = AdaptiveScheduler()
decision = scheduler.step(dt_s, signals)
```

Use scheduler suppression only for event/request consumers that can legally skip updates. Continuous video normally uses every submitted frame.

---

# 6. Core streaming runtime

## `StreamRuntimeConfig`

Important fields:

```text
foveation
tracker
scheduler
context_scale
quality_floor
uncertainty_floor
roi_max_side
qp_block
fovea_qp_delta
periphery_qp_delta
produce_foveated_frame
produce_context_roi_views
produce_qp_map
auto_motion_proposals
```

Named presets:

```python
StreamRuntimeConfig.preset("balanced")
StreamRuntimeConfig.preset("aggressive")
StreamRuntimeConfig.preset("extreme")
```

---

## `FoveaStreamRuntime`

Canonical synchronous frame processor.

```python
runtime = FoveaStreamRuntime(
    StreamRuntimeConfig.preset(
        "aggressive",
        auto_motion_proposals=True,
    )
)

result = runtime.process(
    frame_rgb,
    timestamp_s,
    proposals=external_proposals,
    points=[(0.5, 0.5)],
    signals=InnovationSignals(),
)
```

State must persist across frames. Do not instantiate/reset the runtime per frame.

---

## `ProcessResult`

Main fields:

```python
result.timestamp_s
result.decision
result.rois
result.tracks
result.quality_map
result.foveated_frame
result.views
result.qp_map
```

Convenience properties:

```python
result.context
result.roi_views
```

---

## `CallbackSink`

Adapts a function to the lower-level streaming sink contract.

## `run_stream(...)`

Reference iterable source helper.

---

# 7. Drop-in middleware

This layer is intended for existing camera/source pipelines that want minimal code changes.

## `FramePacket`

```python
packet = FramePacket(
    frame_rgb=frame,
    timestamp_s=timestamp,
    metadata={"camera_id": "left", "frame_id": 123},
    proposals=tuple(proposals),
    points=((0.5, 0.5),),
)
```

`metadata` is opaque and preserved through middleware.

Convenience:

```python
packet = FramePacket.now(frame_rgb, metadata={...})
```

---

## `OptimizedFrame`

```python
optimized.frame_rgb
optimized.timestamp_s
optimized.metadata
optimized.result
optimized.should_send
```

---

## `FoveaStreamTransform`

Simplest source-to-sink integration:

```python
optimizer = FoveaStreamTransform(
    preset="aggressive",
    auto_motion_proposals=True,
    emit_policy="every_frame",
)

optimized = optimizer.transform(frame_rgb, timestamp_s)
```

`emit_policy`:

```text
every_frame  continuous video-safe default
when_send    scheduler may suppress output
```

---

## `CallbackFrameSink`

```python
sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
```

---

## `InlinePipeline`

Synchronous no-hidden-buffer pipeline.

```python
stats = InlinePipeline(transform, sink).run(source)
```

---

## `RealtimeBridge`

Bounded asynchronous bridge for callback-driven capture.

```python
bridge = RealtimeBridge(
    transform,
    sink,
    queue_size=1,
    drop_policy="latest",
)

camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

`drop_policy`:

```text
latest  discard stale queued frame and keep newest pending frame
block   backpressure producer until queue space is available
```

Useful state:

```python
bridge.stats
bridge.last_error
bridge.is_running
bridge.close(drain=True)
```

---

## `PipelineStats`

Counters:

```text
submitted
processed
emitted
scheduler_skipped
dropped_input
errors
max_queue_depth
```

---

## `transform_source(...)`

Pull-style iterable wrapper.

---

# 8. Multi-source relevance fusion

## `EvidenceBus`

Timestamped, TTL-bound relevance fusion.

```python
bus = EvidenceBus(fusion="max")

bus.publish(
    "task",
    timestamp_s,
    ttl_s=0.25,
    weight=2.0,
    proposals=proposals,
    points=[(0.5, 0.5)],
    quality_map=optional_dense_map,
)

snapshot = bus.snapshot(timestamp_s, frame_rgb.shape)
```

Fusion modes:

```text
max
noisy_or
add
```

Useful methods:

```python
bus.clear()
bus.clear("task")
```

---

## `EvidenceRecord`

One timestamped source state.

## `RelevanceSnapshot`

Fused view returned by `EvidenceBus.snapshot(...)`.

## `relevance_map_to_proposals(...)`

Convert a dense relevance field into a small class-agnostic ROI proposal set.

---

# 9. Low-resolution proposal adapter

## `LowResProposalAdapter`

Run custom proposal code on a cheap downscaled frame while keeping the original full-resolution frame for preservation/output.

```python
def my_detector(low_rgb, timestamp_s):
    return proposals

adapter = LowResProposalAdapter(
    my_detector,
    analysis_width=256,
)

proposals = adapter.propose(frame_rgb, timestamp_s)
```

Coordinates should remain normalized.

---

# 10. Latency-aware relevance prediction

## `LatencyBudget`

Fields:

```text
capture_s
analysis_s
encode_s
network_s
decode_s
consumer_s
safety_s
max_horizon_s
```

Example:

```python
latency = LatencyBudget(
    encode_s=0.015,
    network_s=0.040,
    consumer_s=0.020,
    safety_s=0.015,
)

horizon = latency.apply(runtime)
```

The resulting total becomes the predictive ROI horizon.

---

# 11. Temporal ROI cache

## `TemporalRoiCacheConfig`

Important fields:

```text
thumbnail_side
change_threshold
max_refresh_s
match_iou
```

## `TemporalRoiCache`

```python
cache = TemporalRoiCache()
updates = cache.update(
    frame_rgb,
    timestamp_s,
    rois,
    tracks,
)
```

`RoiEnhancement` fields include:

```text
key
roi
changed
change_score
age_s
image
```

When `changed=False`, `image` is `None` and the receiver can reuse the previous emitted high-resolution region.

---

# 12. Background tile cache

## `BackgroundTileCacheConfig`

Fields include:

```text
columns
rows
thumbnail_side
change_threshold
max_refresh_s
```

## `BackgroundTileCache`

```python
cache = BackgroundTileCache()
updates = cache.update(frame_rgb, timestamp_s)
```

Use only for mostly static image-space backgrounds unless the host first compensates camera/world motion.

---

# 13. ROI atlas

## `pack_roi_atlas(...)`

Pack context + only changed ROI enhancements into one RGB image.

```python
atlas = pack_roi_atlas(
    context,
    roi_enhancements,
    max_width=1024,
    max_height=1024,
)
```

`RoiAtlas`:

```python
atlas.image
atlas.placements
```

Each `AtlasPlacement` describes where one source item landed in the atlas.

---

# 14. Portable encoder spatial hints

## `EncoderSpatialHints`

Produced by the adaptive runtime.

```python
hints = out.encoder_hints

hints.block_size
hints.qp_delta_map
hints.rois
hints.fovea_qp_delta
hints.periphery_qp_delta
hints.to_bytes()
```

`to_bytes()` returns row-major signed `int8` block deltas for native adapter transfer.

This remains policy metadata until a real MediaCodec/NVENC/VideoToolbox/VAAPI/etc. adapter consumes it.

---

# 15. Adaptive budget controller

## `AdaptiveBudgetConfig`

Important fields:

```text
target_bitrate_bps
target_bytes_per_frame
target_pixel_fraction
kp
ki
ewma_alpha
min_strength
max_strength
```

## `AdaptiveBudgetState`

```text
strength
ewma_bitrate_bps
integral
last_error
```

## `AdaptiveBudgetController`

```python
controller = AdaptiveBudgetController(
    AdaptiveBudgetConfig(target_bitrate_bps=2_000_000),
    initial_strength=0.55,
)

controller.observe_encoder(encoded_bytes, duration_s)
controller.observe_pixel_fraction(payload_fraction, dt_s)
controller.apply(runtime)
```

The controller continuously interpolates spatial policy between less/more aggressive operating points.

---

# 16. High-level adaptive transport

## `AdaptiveTransportConfig`

Main fields:

```text
preset
auto_motion_proposals
relevance_map_threshold
temporal_cache
background_cache
build_atlas
atlas_max_width
atlas_max_height
latency
budget
```

---

## `AdaptiveTransportRuntime`

```python
optimizer = AdaptiveTransportRuntime(
    AdaptiveTransportConfig(
        preset="aggressive",
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=False,
        build_atlas=True,
        latency=LatencyBudget(...),
        budget=AdaptiveBudgetConfig(...),
    )
)

out = optimizer.process(frame_rgb, timestamp_s)
```

Useful methods:

```python
optimizer.reset()
optimizer.process(...)
optimizer.transform(...)
optimizer.feedback_encoded(encoded_bytes, duration_s)
```

---

## `TransportResult`

```python
out.timestamp_s
out.process_result
out.layered
out.encoder_hints
out.active_evidence_sources
out.controller_state
out.full_frame_pixels

out.frame_rgb
out.should_send
out.payload_pixel_fraction
```

---

## `LayeredPayload`

```python
out.layered.context
out.layered.roi_enhancements
out.layered.changed_rois
out.layered.background_updates
out.layered.atlas
out.layered.pixel_count
```

---

# 17. Logical tile planner

## `TilePlannerConfig`

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

Built-in curves:

```text
linear
smoothstep
gaussian
exponential
power
```

Aggregations:

```text
mean
p90
max
```

---

## `TilePlanner`

```python
planner = TilePlanner(config)
plan = planner.plan(out.process_result.quality_map)
```

Custom hooks:

```python
TilePlanner(
    config,
    degradation_fn=distance_to_quality,
)

TilePlanner(
    config,
    quality_fn=context_to_quality,
    resolution_fn=context_quality_to_scale,
    qp_fn=context_quality_to_delta_qp,
)
```

See `docs/TILE_POLICIES.md` for the exact contracts.

---

## `TilePolicyContext`

Context available to advanced custom policies:

```text
index / row / column
x / y / w / h
center_x / center_y / area_fraction
relevance / distance
mean_relevance / max_relevance / p90_relevance
map_width / map_height
```

---

## `TileDecision`

Per-tile output:

```text
geometry
relevance
distance
quality
resolution_scale
qp_delta
```

---

## `TilePlan`

Useful properties:

```python
plan.tile_count
plan.mean_quality
plan.mean_qp_delta
plan.effective_pixel_fraction
plan.tiles
```

---

## `plan_tiles(...)`

Functional convenience wrapper around `TilePlanner`.

---

## `rasterize_tile_plan(...)`

Convert one `TileDecision` field back to an `HxW` raster.

```python
quality_field = rasterize_tile_plan(plan, width, height, field="quality")
```

---

## `apply_tile_plan(...)`

Reference visual/application realization of per-tile `resolution_scale`.

```python
degraded_rgb = apply_tile_plan(frame_rgb, plan)
```

It downsamples each logical tile and upsamples it back to the original tile rectangle so the quality loss is visible in a normal same-size frame.

A real tiled transport should generally send the low-resolution tile itself rather than upscaling it before transmission.

---

## `render_tile_plan(...)`

Debug heat/grid overlay.

```python
preview_rgb = render_tile_plan(frame_rgb, plan)
```

---

# 18. Common integration recipes

## Ordinary camera -> ordinary encoder

```python
optimizer = FoveaStreamTransform(preset="aggressive")
optimized = optimizer.transform(frame_rgb, timestamp_s)
encoder.send(optimized.frame_rgb)
```

## Camera -> VLM that accepts multiple images

```python
out = adaptive.process(frame_rgb, timestamp_s)
images = [out.layered.context]
images += [r.image for r in out.layered.changed_rois if r.image is not None]
model.send_images(images)
```

## Camera -> API that accepts one image

```python
out = adaptive.process(frame_rgb, timestamp_s)
model.send_image(out.layered.atlas.image)
```

## Camera -> native ROI/QP-aware encoder

```python
out = adaptive.process(frame_rgb, timestamp_s)
encoder.set_qp_map(out.encoder_hints.qp_delta_map)  # adapter pseudocode
encoder.encode(original_frame)
```

## Camera -> custom tiled transport

```python
out = adaptive.process(frame_rgb, timestamp_s)
plan = tile_planner.plan(out.process_result.quality_map)

for tile in plan.tiles:
    send_native_tile(tile)  # host adapter
```

## Callback camera with bounded latency

```python
bridge = RealtimeBridge(
    FoveaStreamTransform(preset="aggressive"),
    CallbackFrameSink(send_frame),
    queue_size=1,
    drop_policy="latest",
)
```

---

# 19. State/lifetime rules

Do:

```text
create runtime once
process monotonically timestamped frames
keep tracker/cache/controller state across frames
use bounded queues for latency-sensitive capture
```

Do not:

```text
recreate runtime every frame
reset temporal cache every frame
assume capture FPS == model FPS
assume QP/tile metadata reduces bytes without an adapter
```

---

# 20. Benchmark/visual tools

```text
bench/real_video_visualization.py    same-encoder codec benchmark
bench/benchmark_transport_stack.py   adaptive transport metrics
bench/benchmark_suite.py             combined single-configuration benchmark
bench/benchmark_gallery.py           all major modes/settings, folders + GIF/MP4/JSON
examples/generate_showcase.py        nine-mode documentation showcase
examples/adaptive_transport.py       live/video interactive reference
```

Recommended one-command exploration:

```powershell
python bench\benchmark_gallery.py example.mp4
```
