# Adaptive relevance transport

This document describes the higher-level optimization stack added on top of the existing provider-agnostic frame middleware.

The goal is not to invent another blur filter. The goal is to derive several transport/encoder actuators from one persistent relevance state.

```text
camera / video / robot / glasses / RTSP / app-owned frames
                         +
      motion / gaze / OCR / task / detector / user / map evidence
                         |
                         v
                   EvidenceBus
                         |
                         v
            predictive multi-ROI runtime
                         |
           +-------------+--------------+
           |             |              |
           v             v              v
       QP hints     layered payload   full frame
           |        context + ROI        |
           |        temporal cache       |
           |        background delta     |
           |        packed atlas         |
           +-------------+--------------+
                         |
                         v
              arbitrary downstream
```

## Canonical API

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
    budget=AdaptiveBudgetConfig(
        target_bitrate_bps=2_000_000,
        target_pixel_fraction=.20,
    ),
))

out = optimizer.process(frame_rgb, timestamp_s)
```

The caller may then choose one or several actuators:

```python
# Drop-in existing frame pipeline
send_frame(out.frame_rgb)

# Multi-image/VLM path
send_images([
    out.layered.context,
    *[x.image for x in out.layered.changed_rois if x.image is not None],
])

# One-image API path
send_image(out.layered.atlas.image)

# Encoder-aware path
encoder.set_delta_qp_map(out.encoder_hints.qp_delta_map)
encoder.encode(original_frame)
```

The library still does not open sockets or depend on a model/provider SDK.

## 1. Encoder spatial hints

`EncoderSpatialHints` exposes the spatial compression policy without requiring the RGB foveation actuator:

- block size;
- signed `int8` delta-QP map;
- active normalized ROIs;
- fovea/periphery reference deltas.

```python
hints = out.encoder_hints
raw = hints.to_bytes()  # row-major signed int8 map
```

This is the portable contract for future MediaCodec, NVENC, VideoToolbox, VAAPI, WebRTC encoder and other adapters.

The current repository does **not** claim that every hardware encoder accepts this map directly. A platform adapter must translate this contract into the native encoder API. Keep that translation outside the relevance core.

## 2. Temporal ROI cache

`TemporalRoiCache` compares the current high-resolution ROI against the last ROI that was actually emitted for the same persistent track.

Default behavior:

```text
first ROI             -> SEND
same ROI unchanged    -> REUSE cached ROI
same ROI unchanged    -> REUSE cached ROI
material change       -> SEND
max-refresh timeout   -> SEND
```

The cache compares a small grayscale thumbnail against the last emitted version. It intentionally compares against the **last sent state**, not only the previous frame, so slow cumulative drift eventually triggers an update.

Output:

```python
for enhancement in out.layered.roi_enhancements:
    enhancement.key
    enhancement.roi
    enhancement.changed
    enhancement.change_score
    enhancement.age_s
    enhancement.image  # None when the cached value can be reused
```

## 3. Background tile cache

`BackgroundTileCache` is an opt-in scene-delta actuator for mostly static cameras.

The frame is divided into a regular grid. Only tiles whose visual signature changed, or whose refresh timeout expired, are emitted.

Do not enable this blindly for a moving camera. A moving camera naturally invalidates most tiles unless the host performs camera-motion/world compensation first.

## 4. Layered payload

`LayeredPayload` contains:

- low-resolution global context;
- only changed high-resolution ROI enhancements;
- optional changed background tiles;
- optional packed atlas.

This supports the transport pattern:

```text
BASE:
small whole scene

ENHANCEMENT:
ROI 17 only when changed
ROI 21 only when changed
ROI 31 only when changed
```

The base preserves scene context even if an enhancement packet is delayed or dropped.

## 5. Packed ROI atlas

`pack_roi_atlas(...)` places the context image plus only changed ROI crops into a single ordinary RGB image.

This is useful when a consumer accepts one image but not a list of images.

The returned `RoiAtlas` contains both the image and placement metadata:

```python
atlas.image
atlas.placements
```

Each placement records:

- `kind` (`context` or `roi`);
- cache/ROI key;
- atlas rectangle;
- original normalized source ROI where applicable.

## 6. Closed-loop adaptive budget controller

Static presets remain useful starting points, but a real stream can vary dramatically from frame to frame.

`AdaptiveBudgetController` accepts feedback from the actual encoder and/or layered payload pixel load.

```python
optimizer.feedback_encoded(
    encoded_bytes=packet_size,
    duration_s=frame_duration,
)
```

When actual bitrate is above target, the controller moves continuously toward a more aggressive policy by adjusting:

- peripheral scale;
- falloff width;
- total ROI budget;
- maximum active ROI count;
- context resolution;
- quality floor;
- uncertainty floor;
- periphery QP delta.

When the stream is below target, the controller can spend more bits again.

This is intentionally a feedback controller, not a promise that a fixed preset always produces a fixed Mbps value.

## 7. Multi-source relevance fusion

`EvidenceBus` keeps timestamped, TTL-bound relevance evidence from independent sources.

Example:

```python
bus.publish(
    "task",
    timestamp_s,
    ttl_s=.5,
    proposals=task_rois,
    weight=2.0,
)

bus.publish(
    "gaze",
    timestamp_s,
    ttl_s=.1,
    points=[gaze_xy],
)

bus.publish(
    "saliency",
    timestamp_s,
    ttl_s=.2,
    quality_map=saliency_map,
)
```

Supported dense fusion modes:

- `max`;
- `noisy_or`;
- `add` with clipping.

Dense fused relevance maps are converted to a small class-agnostic ROI proposal set before entering the normal multi-ROI tracker. No semantic class is hardcoded.

## 8. Low-resolution analysis, full-resolution preservation

`LowResProposalAdapter` runs an arbitrary relevance proposal function on a downscaled analysis frame while preserving the original full-resolution frame for output/encoding.

```python
adapter = LowResProposalAdapter(my_detector, analysis_width=256)
proposals = adapter.propose(frame_rgb, timestamp_s)
```

ROIs use normalized coordinates, so no resolution-specific coordinate rewrite is required.

The built-in residual-motion proposal source already follows the same general principle.

## 9. Latency-aware prediction

`LatencyBudget` converts the expected end-to-end delay into the ROI tracker's prediction horizon.

```python
LatencyBudget(
    encode_s=.015,
    network_s=.040,
    consumer_s=.020,
    safety_s=.015,
)
```

The runtime therefore preserves where the tracked region is expected to matter **after capture/encode/network/consumer delay**, not only where it was when the current frame arrived.

The horizon is clamped by `max_horizon_s` to avoid runaway extrapolation.

## 10. Native Rust control plane

The Rust core exports native equivalents for the pieces that belong in the provider-independent control plane:

- `LatencyBudget`;
- `AdaptiveBudgetConfig` / `AdaptiveBudgetController`;
- `EvidenceBus` / `EvidenceRecord`;
- `RegionSignature` / `signature_rgb8`;
- `TemporalRegionCache`;
- `EncoderSpatialHints`;
- `analysis_dimensions`.

Python contains the reference image atlas/background-tile implementation because it is also the visualization/benchmark layer. Concrete platform-native image/buffer/encoder adapters remain separate work.

## Benchmark

Run:

```bash
python bench/benchmark_transport_stack.py example1.mp4 example2.mp4 \
  --preset aggressive \
  --out output/transport_benchmark.json
```

Optional static-background mode:

```bash
python bench/benchmark_transport_stack.py fixed_camera.mp4 \
  --background-cache
```

The benchmark records:

- mean/p50/p95/p99 preprocessing latency;
- effective processing throughput;
- active ROI count;
- changed ROI count;
- temporal reuse fraction;
- layered payload pixel fraction;
- atlas pixel fraction;
- background changed-tile rate;
- QP-map statistics;
- adaptive controller strength.

Use the existing real-video codec benchmark for actual H.264 byte savings. The transport-stack benchmark measures the additional temporal/layered/control-plane effects.

## Engineering boundary

Implemented now:

- same-size foveated frame;
- QP-map encoder hints;
- low-resolution analysis adapter;
- multi-source relevance fusion;
- predictive latency horizon;
- temporal ROI reuse;
- static background tile reuse;
- context + ROI layered payload;
- one-image packed atlas;
- adaptive target bitrate/pixel controller;
- benchmark runner;
- native control-plane primitives.

Still adapter-specific work:

- MediaCodec/NVENC/VideoToolbox/VAAPI calls that consume the QP map;
- native NV12/YUV zero-copy paths;
- WebRTC/GStreamer/RTP packetization of layered payloads;
- world-locked background cache for freely moving cameras;
- hardware-specific energy/performance tuning.

Those items should consume the contracts above rather than change their semantics.
