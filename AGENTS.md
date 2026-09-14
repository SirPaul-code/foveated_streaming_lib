# AGENTS.md — FoveaStream integration contract

This is the durable handoff for coding agents. Do not depend on chat history.

## Product boundary

FoveaStream is a provider-agnostic adaptive relevance transport/middleware layer for machine vision.

Preferred shape:

```text
existing source
      |
      v
timestamped frame + optional relevance evidence
      |
      v
FoveaStream
persistent predictive multi-ROI state
      |
      +--> same-size optimized frame
      +--> context + changed high-res ROI layers
      +--> packed one-image atlas
      +--> block delta-QP encoder hints
      +--> optional SEND/SKIP
      |
      v
existing encoder / WebRTC / model / recorder / callback / custom transport
```

The core must NOT depend on Gemini, OpenAI, WebRTC, GStreamer, CameraX, AVFoundation, Meta APIs, one detector class, one camera vendor, or one vision model.

## Read first

1. `README.md`
2. `docs/ADAPTIVE_TRANSPORT.md`
3. `docs/FRAME_MIDDLEWARE.md`
4. `docs/AGENT_INTEGRATION.md`
5. `docs/STATUS.md`
6. `docs/REALTIME_STREAMING_STATUS.md`
7. `docs/PREDICTIVE_ATTENTION_ARCHITECTURE.md`

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

`optimized.frame_rgb` preserves the submitted width/height. Full runtime state is available as `optimized.result`.

## Adaptive transport path

Use this when the host can exploit more than a same-size RGB frame:

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

Available actuators:

```python
out.frame_rgb                         # same-size drop-in output
out.layered.context                   # low-res whole-scene base
out.layered.changed_rois              # only changed high-res ROI enhancements
out.layered.background_updates        # opt-in static-background tile deltas
out.layered.atlas                     # context + changed ROIs in one RGB image
out.encoder_hints.qp_delta_map        # portable signed delta-QP block map
out.process_result.decision            # optional SEND/SKIP decision
```

Do not force all consumers through one actuator.

## Encoder-hint invariant

`EncoderSpatialHints` is a portable control-plane contract. It is **not** proof that MediaCodec, NVENC, VideoToolbox, VAAPI, WebRTC, or another concrete encoder is already wired to the map.

A platform adapter may translate:

```text
EncoderSpatialHints
    -> native encoder ROI/QP API
    -> actual encoded frame
```

Keep that adapter outside the relevance core and benchmark its real byte/quality effect before making hardware-encoder claims.

## Temporal-cache invariant

`TemporalRoiCache` compares the current ROI against the **last emitted high-resolution ROI state**, not only against the immediately previous frame.

Expected behavior:

```text
first observation      -> SEND
unchanged ROI          -> REUSE
unchanged ROI          -> REUSE
material change        -> SEND
max refresh timeout    -> SEND
```

Do not reset the cache/runtime per frame.

## Background-cache invariant

`BackgroundTileCache` is opt-in and intended for mostly static cameras/backgrounds.

Do not enable it blindly for a freely moving camera. Global camera motion naturally invalidates many tiles unless the host first provides camera/world compensation.

A future moving-camera cache should be world-locked using pose/homography/depth rather than pretending image-space tiles are static.

## Multi-source relevance

An ROI is NOT a semantic class. It means only: spatial support whose loss of detail would disproportionately harm the current downstream task.

Relevance may come from:

- residual motion;
- saliency;
- user tap/selection;
- gaze/head direction;
- detector/segmenter output;
- OCR/text;
- depth/autofocus;
- AR anchor;
- hand/object interaction;
- task/planner state;
- downstream model feedback;
- remote operator/controller;
- future learned relevance model.

`EvidenceBus` provides timestamped TTL-bound fusion. Dense map modes currently include `max`, `noisy_or`, and clipped additive fusion.

Do not replace this abstraction with hardcoded face/person/car recognition in core.

## Low-resolution analysis rule

Proposal generation may run on a cheap low-resolution frame while the original full-resolution frame stays available for output/encoding.

`LowResProposalAdapter` formalizes this and keeps coordinates normalized.

Do not unnecessarily run generic proposal generation at source resolution when normalized low-resolution analysis is sufficient.

## Latency-aware prediction

The tracker prediction horizon should model the delay between capture and downstream use.

`LatencyBudget` may include:

- capture/analysis delay;
- encode delay;
- network delay;
- decode delay;
- consumer/model delay;
- safety margin.

Preserve where relevance is expected to matter at downstream time, not only where it was at capture time.

## Closed-loop budget rule

Presets are initial conditions, not guaranteed bitrate modes.

`AdaptiveBudgetController` may consume real encoded-byte feedback and/or layered-pixel feedback to adjust:

- peripheral scale;
- falloff;
- ROI area budget;
- ROI count;
- context scale;
- quality/uncertainty floors;
- periphery delta-QP.

If integrating a real encoder, feed actual encoded output size back through `feedback_encoded(...)` instead of estimating bitrate from image appearance.

## Realtime callback path

For callback-driven capture:

```python
from foveastream import CallbackFrameSink, RealtimeBridge

sink = CallbackFrameSink(lambda packet: downstream.send(packet.frame_rgb))
bridge = RealtimeBridge(
    optimizer,
    sink,
    queue_size=1,
    drop_policy="latest",
)

camera.on_frame(lambda frame, ts: bridge.submit(frame, ts))
```

The latest-frame queue is deliberate. Realtime perception should not turn overload into an ever-growing queue of stale frames.

Use `drop_policy="block"` only when every frame must be processed and source backpressure is acceptable.

## Emit-policy invariant

`every_frame` is the safe default for continuous camera/video/encoder paths.

`when_send` is opt-in for request/event pipelines where redundant requests may be suppressed.

Do not silently apply SEND/SKIP to a transport that requires continuous cadence.

## Metadata invariant

`FramePacket.metadata` is opaque and must be preserved through the drop-in middleware. Source adapters may store camera IDs, frame IDs, RTP timestamps, tracing IDs or platform handles there.

Do not teach core about provider-specific metadata.

## Native Rust integration

Use `StreamMiddleware` for the simple native drop-in path. The native control plane additionally exports:

- `LatencyBudget`;
- `AdaptiveBudgetController`;
- `EvidenceBus`;
- `RegionSignature` / `TemporalRegionCache`;
- `EncoderSpatialHints`;
- low-resolution analysis sizing helpers.

The native core remains synchronous. Platform capture threads, bounded queues, zero-copy buffers, encoder threads and sockets belong to host/platform adapters.

## Multi-rate rule

Never assume capture FPS == optimizer FPS == output/model FPS.

Example:

```text
camera:      60 FPS
optimizer:   30-60 FPS depending on device/path
IMU:        200 Hz
model:        1-10 requests/s or event-driven
```

For latency-sensitive processing keep queues bounded and preserve source timestamps.

## Performance rule

Python/OpenCV is the reference, benchmark and integration layer. Production hot paths should progressively use:

- Rust/native core;
- NV12/YUV instead of RGB round trips;
- coarse encoder-block quality fields;
- zero/low-copy platform buffers;
- real hardware encoder ROI/QP controls where available.

Do not claim zero-copy/hardware encoder support until an actual platform adapter exists and is measured.

## Demo / regression

```bash
python examples/live_webcam.py --preset aggressive
python examples/adaptive_transport.py
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive
python bench/benchmark_transport_stack.py example1.mp4 example2.mp4 --preset aggressive --out output/transport_benchmark.json
pytest -q
cargo test --all-targets
cargo build --release
```

## Engineering invariants

- Source and sink stay replaceable.
- Preserve source timestamps and opaque metadata.
- Use monotonic timestamps.
- Preserve temporal state across frames.
- Zero/one/many ROIs are all valid.
- ROI pixel budget is a hard cap.
- Uncertainty spends bandwidth instead of silently losing a target.
- Temporal reuse never means permanently stale content; max-refresh remains available.
- Static background cache is opt-in.
- `EveryFrame` is the safe default for continuous video.
- `WhenSend` is explicit and opt-in.
- Realtime queues are bounded.
- Keep provider auth/network/model SDKs outside core.
- Keep concrete encoder integrations outside relevance policy.
- Benchmark downstream task quality, not only visual appearance or bytes.

## Before handing work to another agent

Update `docs/STATUS.md` with:

- exact branch/commit;
- what is actually implemented;
- tests/CI status;
- known limitations;
- precise next engineering steps.

Never mark untested platform support as complete.
