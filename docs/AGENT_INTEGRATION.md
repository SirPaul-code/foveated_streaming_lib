# Integrating FoveaStream into an existing workflow

This document is written for a coding agent that has never seen the project before and must integrate it into an arbitrary camera, video, streaming, or vision-model workflow.

## 1. Do not start with a provider

FoveaStream is not a Gemini/OpenAI/WebRTC client. Treat it as a stateful transform/control plane between capture and the consumer.

```text
existing source
(camera / RTSP / file / smart glasses / robot / AR stack)
        |
        v
convert/adapt to timestamped frame + optional evidence
        |
        v
FoveaStreamRuntime
        |
        +--> SEND/SKIP decision
        +--> tracked ROI set
        +--> quality map
        +--> same-size foveated frame
        +--> low-res context + N hi-res ROI images
        +--> encoder delta-QP map
        |
        v
existing consumer
(model API / WebRTC / local model / recorder / message bus / custom transport)
```

Only the two edge adapters are application-specific.

## 2. Pick the output actuator that matches the existing consumer

### Existing video/image stream expects one ordinary frame

Use `result.foveated_frame`. Encode it exactly as the old frame was encoded. This is the lowest-friction integration.

### Existing vision API can accept multiple images

Use `result.context` plus `result.roi_views`. This reduces actual decoded/model pixels instead of merely making a full-size frame easier to compress.

### Existing encoder accepts ROI / block QP metadata

Use `result.qp_map` or the Rust `qp_delta_map`. Avoid reconstructing a foveated RGB frame if the encoder can spatially allocate bits directly.

### Existing system can skip frames/requests

Respect `result.decision.send`. Continue local tracking on every analysis frame even when the transport sends nothing.

## 3. Decide where ROI proposals come from

The runtime does not define "interesting" as a semantic class. ROI proposals are relevance evidence.

Start with any signals already available in the host application. Examples:

```text
AR app                -> user tap + AR ray + tracked anchor
smart glasses         -> gaze / head direction / hand interaction
inspection app        -> current checklist item + OCR + motion
robotics              -> planner target + detector + depth discontinuity
screen/video agent    -> cursor/task state + motion + text regions
security              -> existing detector outputs
unknown generic video -> residual-motion proposal source as a fallback
```

If the host already knows what matters, pass that information in. Do not run a redundant generic detector just because one exists.

Python proposal format:

```python
RoiProposal(
    roi=Roi(x, y, w, h),       # normalized 0..1 coordinates
    confidence=0.0_to_1.0,
    priority=relative_weight,
    source="adapter-name",
    track_hint=stable_id_or_None,
)
```

`track_hint` is optional. Supply it when the host already has stable object/anchor IDs.

## 4. Multiple ROIs

Multiple ROIs are first-class. `MultiRoiTracker`:

1. deduplicates overlapping proposals from different sources;
2. associates proposals with persistent tracks;
3. estimates rectangle velocity;
4. predicts support forward by the configured latency horizon;
5. expands support when stale/uncertain;
6. ranks tracks by confidence × priority;
7. selects multiple ROIs under `pixel_budget_fraction` and `max_tracks`.

The output quality map is the union/max of all active spatial supports. One frame may therefore contain zero, one, or many foveae.

A pixel budget exists because blindly preserving every candidate at full resolution eventually recreates the original full-resolution frame.

## 5. Minimal Python adapter

```python
from foveastream import (
    FoveaStreamRuntime,
    StreamRuntimeConfig,
    Roi,
    RoiProposal,
)

runtime = FoveaStreamRuntime(StreamRuntimeConfig())

while source.running:
    frame_rgb, timestamp_s = source.next_frame()

    proposals = []
    for region in my_existing_relevance_system(frame_rgb):
        proposals.append(RoiProposal(
            Roi(region.x, region.y, region.w, region.h),
            confidence=region.confidence,
            priority=region.priority,
            source="my-workflow",
            track_hint=getattr(region, "id", None),
        ))

    result = runtime.process(frame_rgb, timestamp_s, proposals=proposals)

    if not result.decision.send:
        continue

    # Choose ONE path based on what the downstream consumer supports.
    transport.send(result.foveated_frame)
    # or: transport.send_images([result.context, *result.roi_views])
    # or: encoder.encode(frame_rgb, qp_map=result.qp_map)
```

## 6. Generic sink adapter

```python
class MySink:
    def consume(self, result):
        if not result.decision.send:
            return
        payload = encode_for_my_existing_protocol(result)
        my_existing_connection.send(payload)

runtime.push(frame_rgb, timestamp_s, MySink(), proposals=proposals)
```

Do not put API keys or provider SDK imports into `foveastream.streaming`.

## 7. Live-source adapter

A source only needs to produce monotonic timestamps and RGB frames for the Python reference runtime:

```python
def source():
    while True:
        yield monotonic_time_seconds(), next_rgb_frame()

run_stream(source(), runtime, sink)
```

For native production code, do not force a copy to RGB if the platform has YUV/NV12 and a suitable native actuator. The current Rust streaming runtime is RGB8 reference/native functionality; platform-native YUV/zero-copy adapters remain separate engineering work.

## 8. Threading and backpressure

Recommended host architecture:

```text
capture thread
  -> bounded latest-frame queue
analysis/runtime thread
  -> bounded selected-output queue
transport/encode thread
```

When overloaded, prefer dropping stale frames and processing the newest state. Do not build an unbounded queue that converts real-time latency into seconds of backlog.

Keep timestamps from capture time, not processing time.

## 9. Output-rate decoupling

FoveaStream is explicitly multi-rate. Example:

```text
capture:       60 FPS
motion/ROI:    30-60 FPS
IMU:           100-500 Hz (native future adapter)
transport:     1-30 FPS depending on consumer
```

The consumer's rate limit must not determine the local tracking rate.

## 10. Validation after integration

Measure at minimum:

- input frames/s;
- local processing ms/frame p50/p95;
- selected output frames/s;
- bytes/s;
- decoded/model pixels/s;
- active ROI count and total ROI area;
- target/important-region miss rate;
- downstream task quality against a full-frame baseline;
- end-to-end latency p50/p95.

Do not optimize only for encoded file size. A 90% byte reduction is useless if the important task support is missed.

## 11. Current hard boundary

Implemented now:

- causal push-frame runtime;
- provider-agnostic sink contract;
- multiple persistent ROI tracks;
- class-agnostic residual-motion proposal source in Python;
- external ROI injection;
- same-size foveated frame;
- context + multiple ROI views;
- QP map;
- send/skip decision;
- Rust native equivalent for runtime/tracker/sink abstractions.

Still platform/application adapters, not core functionality:

- CameraX/Camera2, AVFoundation, OpenXR camera capture;
- NV12/YUV zero-copy;
- MediaCodec/NVENC/VideoToolbox/VAAPI ROI wiring;
- concrete WebRTC/RTP/GStreamer/provider SDK clients;
- production queue/backpressure implementation in each host;
- provider-specific billing/token estimation.

## 12. Where to extend

Add a new relevance source by producing `RoiProposal`; do not modify the tracker.

Add a new destination by implementing a sink/adapter; do not modify the runtime.

Add a new payload actuator by consuming `quality_map` / `rois`; avoid provider logic in the actuator.

If a change violates these boundaries, document why in `docs/STATUS.md` before merging it.
