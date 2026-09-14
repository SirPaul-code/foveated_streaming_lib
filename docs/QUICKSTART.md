# Quickstart

## Windows

From the repository root:

```powershell
.\scripts\setup.ps1
.\.venv\Scripts\Activate.ps1
python bench\real_video_visualization.py example1.mp4 --outdir output --preset aggressive
```

The setup script creates `.venv`, installs the Python package with development/video dependencies, checks Python 3.10+, and installs FFmpeg through `winget` when available.

If FFmpeg was installed during setup, reopen PowerShell if the new executable is not visible in `PATH` yet.

## Linux / macOS

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive
```

FFmpeg with `libx264` must be available in `PATH`.

## Foveation presets

The real-video benchmark exposes three presets:

| Preset | Peripheral source scale | Falloff | Quality floor | Context scale |
|---|---:|---:|---:|---:|
| `balanced` | 1/8 per axis | 2.8 | 0.04 + uncertainty | 0.25 |
| `aggressive` | 1/16 per axis | 4.5 | 0.01 + uncertainty | 0.15 |
| `extreme` | 1/24 per axis | 6.5 | 0.00 + uncertainty | 0.10 |

Example:

```powershell
python bench\real_video_visualization.py example1.mp4 --outdir output --preset extreme
```

Every preset value can be overridden independently:

```powershell
python bench\real_video_visualization.py example1.mp4 `
  --outdir output `
  --preset aggressive `
  --peripheral-downscale 20 `
  --falloff-strength 5.2 `
  --quality-floor 0.005 `
  --uncertainty-floor 0.015 `
  --context-scale 0.12 `
  --process-fps 30
```

Relevant output files:

- `example1_baseline_crf23.mp4` — ordinary same-settings H.264 re-encode;
- `example1_foveated_crf23.mp4` — same-size foveated transport visualization;
- `example1_visualization.mp4` — original / predicted attention / foveated transport side-by-side;
- `example1_benchmark.json` — measured bytes, model-pixel budget, processing time and exact settings used.

## Important benchmark interpretation

The same-size foveated MP4 measures encoder entropy/byte reduction. It is not the most efficient VLM representation by itself because its raster dimensions stay unchanged.

For model-input efficiency, the benchmark separately estimates the low-resolution global context + high-resolution ROI path. That representation reduces actual pixels sent to the model and should be evaluated against downstream task quality, not only visual appearance.
