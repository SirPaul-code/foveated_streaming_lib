#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: python3 was not found. Python 3.10+ is required." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('ERROR: Python 3.10+ is required.')
PY

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

if ! command -v ffmpeg >/dev/null 2>&1; then
  cat >&2 <<'EOF'
WARNING: ffmpeg was not found in PATH.
Install an ffmpeg build with libx264 support, for example:
  Ubuntu/Debian: sudo apt install ffmpeg
  macOS/Homebrew: brew install ffmpeg
Then rerun the demo.
EOF
fi

echo
echo 'Python environment ready.'
echo 'Activate with:'
echo '  source .venv/bin/activate'
echo 'Run the demo with:'
echo '  python bench/real_video_visualization.py example1.mp4 --outdir output --preset aggressive'
