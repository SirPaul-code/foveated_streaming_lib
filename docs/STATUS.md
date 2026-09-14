# STATUS / handoff

Last updated: 2026-09-14

## Goal

Commercial cross-platform foveated input/streaming SDK for machine-vision/VLM workloads. It accepts explicit/inferred attention evidence, generates a quality field, and reduces network/model input while preserving task-relevant detail.

## Research conclusion

- Foveated XR streaming is established.
- Hardware/software video encoders already provide ROI/QP primitives.
- VLM research is actively moving toward adaptive pixel/token allocation.
- `fnvision` is a close Python foveated-vision prior art project; `fvideo` is older Rust/EyeLink/H.264 prior art.
- Differentiation must be unified **attention policy -> quality field -> transport/VLM/encoder backend**, native integrations, and task-aware benchmarking.

## Implemented v0.1

### Rust core
- normalized points/ROIs;
- configurable linear/smoothstep/Gaussian falloff;
- quality-field generation;
- same-size RGB foveation;
- context + ROI views;
- per-block signed QP delta map;
- weighted focus-source fusion;
- alpha-beta temporal prediction;
- IMU yaw/pitch camera-motion compensation;
- C ABI.

### Python reference
- equivalent quality map/foveation behavior;
- manual ROI and arbitrary custom-map input;
- depth-plane prior helper;
- focus fusion/tracking/IMU compensation;
- actual-pixel-saving context + ROI views;
- QP map generation.

### Validation performed locally
- Python tests: 5 passed.
- Synthetic 1280x720 x 60-frame smoke benchmark:
  - same-size foveated JPEG: 26.55% fewer bytes;
  - context + ROI: 83.25% fewer pixels (5.97x reduction);
  - context + ROI JPEG: 70.91% fewer bytes;
  - protected ROI pixel-identical;
  - Python reference qmap+foveation around 10 FPS in the current container.

Rust compiler was unavailable in the local execution container. Rust compilation must therefore be treated as CI-verified only after GitHub Actions succeeds.

## Next engineering steps

1. CI compile/test Rust core on Linux/Windows/macOS; fix ABI/build issues.
2. Android AAR/JNI adapter: CameraX/ARCore metadata -> MediaCodec QP offset maps/rects, with RGB fallback.
3. NVENC backend mapping `Q` to emphasis/QP maps.
4. FFmpeg/VAAPI/x264/x265 ROI adapter.
5. Apple XCFramework/Metal adapter for CVPixelBuffer/texture crop+resize.
6. Timestamped `Evidence` interface: point/rect/mask, confidence, priority, TTL.
7. Model-aware benchmark for at least one open/local VLM and one paid API.
8. Sweep real egocentric/inspection video for Pareto curves: task accuracy vs pixels/bytes/latency.
9. Add signed offline-license verification after package boundaries stabilize.
10. Before public release decide what stays open vs proprietary.

## Product checkpoint

Continue only if representative model/task benchmarks beat naive global downscale, fixed thumbnail+crop, provider-native resizing, and comparable foveated preprocessing at equivalent task accuracy. The win condition is not prettier foveation; it is **lower total bytes/tokens/latency at equivalent application accuracy**.
