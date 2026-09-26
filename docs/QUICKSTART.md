# Quickstart

## Install

Canonical metadata lives in `pyproject.toml`. Convenience requirement files are included so a fresh clone can also use the familiar workflow:

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
```

For tests/development:

```bash
python -m pip install -r requirements-dev.txt
```

FFmpeg with `libx264` is a system dependency only for the MP4 benchmark/output path.

## Windows bootstrap

From the repository root:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
```

The setup script creates `.venv`, installs the Python package with development/video dependencies, checks Python 3.10+, and installs FFmpeg through `winget` when available.

If FFmpeg was installed during setup, reopen PowerShell if the new executable is not visible in `PATH` yet.

## Linux / macOS bootstrap

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
```

## Real-video aggressive demo

```powershell
python bench\real_video_visualization.py example1.mp4 --outdir output --preset aggressive
```

The demo now tracks **multiple simultaneous class-agnostic ROIs**, not a single rectangle. The visualization draws every active ROI.

Increase ROI capacity/budget explicitly:

```powershell
python bench\real_video_visualization.py example1.mp4 `
  --outdir output `
  --preset aggressive `
  --max-rois 10 `
  --roi-budget-fraction 0.30
```

## Foveation presets

| Preset | Peripheral source scale | Falloff | Quality floor | Context scale | Max ROI reference |
|---|---:|---:|---:|---:|---:|
| `balanced` | 1/8 per axis | 2.8 | 0.04 + uncertainty | 0.25 | 8 |
| `aggressive` | 1/16 per axis | 4.5 | 0.01 + uncertainty | 0.15 | 6 |
| `extreme` | 1/24 per axis | 6.5 | 0.00 + uncertainty | 0.10 | 4 |

Every benchmark value can be overridden independently:

```powershell
python bench\real_video_visualization.py example1.mp4 `
  --outdir output `
  --preset aggressive `
  --peripheral-downscale 20 `
  --falloff-strength 5.2 `
  --quality-floor 0.005 `
  --uncertainty-floor 0.015 `
  --context-scale 0.12 `
  --max-rois 10 `
  --roi-budget-fraction 0.30 `
  --process-fps 30
```

Relevant output files:

- `example1_baseline_crf23.mp4` — ordinary same-settings H.264 re-encode;
- `example1_foveated_crf23.mp4` — same-size foveated transport output;
- `example1_visualization.mp4` — original / multi-ROI attention / foveated output side-by-side;
- `example1_benchmark.json` — bytes, model-pixel budget, processing time, mean/max ROI count and exact settings.

## Live provider-agnostic streaming runtime

Run the local webcam reference example:

```bash
python examples/live_webcam.py --preset aggressive
```

Core API:

```python
from foveastream import FoveaStreamRuntime, StreamRuntimeConfig

runtime = FoveaStreamRuntime(
    StreamRuntimeConfig.preset('aggressive', auto_motion_proposals=True)
)

result = runtime.process(frame_rgb, timestamp_s)

if result.decision.send:
    # Existing one-frame/video workflow:
    send(result.foveated_frame)

    # OR a multi-image vision workflow:
    # send_images([result.context, *result.roi_views])

    # OR an ROI-aware encoder:
    # encode(original_frame, delta_qp=result.qp_map)
```

Do not modify core to add a provider. Implement an adapter/sink around `ProcessResult`.

See:

- `AGENTS.md` — durable instructions for coding agents;
- `docs/AGENT_INTEGRATION.md` — full integration guide;
- `examples/custom_sink_adapter.py` — adapter template.

## Important benchmark interpretation

The same-size foveated MP4 measures encoder entropy/byte reduction. It is not automatically the most efficient VLM representation because raster dimensions stay unchanged.

For model-input efficiency, use low-resolution global context + one or more high-resolution ROI views when the downstream API/model can consume multiple images. That reduces actual pixels rather than only H.264 entropy.

## License

The repository is source-available for non-commercial evaluation/research under the root `LICENSE` terms.

Commercial use requires a separate written license. Contact: `p.duplinsky@gmail.com`.
