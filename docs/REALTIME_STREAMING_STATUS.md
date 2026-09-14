# Realtime streaming readiness

Last updated: 2026-09-14

## What works now

The current real-video reference pipeline is frame-sequential: each decoded frame is analyzed, the attention state is updated from camera-motion-compensated residual motion, a quality field is generated, the frame is foveated, and output is written immediately to persistent FFmpeg encoder pipes.

This proves that the algorithm can operate causally frame-by-frame. The benchmark does not need future frames and is not a batch-only image transform.

The existing repository also contains native Rust primitives for predictive attention, uncertainty-aware ROI generation, QP-delta maps, adaptive send/skip scheduling and a C ABI.

## What is not production-ready yet

The Python/OpenCV benchmark is not itself a production realtime streaming SDK. Missing production transport work includes:

- live camera capture adapters rather than `cv2.VideoCapture` file decoding;
- explicit asynchronous capture / analysis / encode / network queues and backpressure;
- monotonic timestamp synchronization across camera, IMU, flow and semantic/model feedback;
- native YUV/NV12 processing instead of RGB conversion and full-frame reconstruction;
- zero-copy platform buffers;
- hardware encoder integration and direct ROI/QP maps;
- WebRTC/GStreamer/provider transport adapters;
- end-to-end p50/p95 latency measurement including capture, encode, network and model queue;
- overload policy and frame dropping when processing cannot keep up;
- downstream task-accuracy validation under aggressive foveation.

## Intended realtime architecture

```text
camera 30/60 fps
    |
    +--> local low-cost tracking / pose / IMU at high rate
    |         |
    |         v
    |   predictive attention state + uncertainty
    |         |
    |         v
    +----> quality/QP/tile/ROI policy
              |
              +--> video encoder / WebRTC stream
              |
              +--> low-res context + hi-res ROI VLM request
              |
              +--> scheduler may send nothing for redundant frames
```

Capture FPS, local analysis FPS and model/request FPS should be independent. A camera may run at 60 FPS while local attention propagation runs at 30-200 Hz and the expensive vision model receives only the frames or ROIs selected by the scheduler.

## Current conclusion

The current code is a causal frame-by-frame reference implementation and is suitable for demonstrating realtime-capable preprocessing. It must not yet be described as a production-ready realtime streaming transport SDK.

The production path should move the hot loop into the existing native Rust/C ABI layer, keep data in native camera/encoder buffers, generate coarse block/tile quality policies, and connect those policies to hardware encoders or VLM payload planners without reconstructing full RGB frames when avoidable.
