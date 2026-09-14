#!/usr/bin/env python3
"""One-command benchmark suite for arbitrary videos.

Runs both:
1. codec benchmark / visualization (baseline vs same-encoder foveated H.264), and
2. adaptive transport benchmark (latency, temporal reuse, layered pixels, atlas, QP hints).

Each input gets its own output directory and a combined machine-readable JSON report.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODEC = ROOT / "bench" / "real_video_visualization.py"
TRANSPORT = ROOT / "bench" / "benchmark_transport_stack.py"


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    return stem or "video"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def benchmark_video(path: Path, args) -> dict:
    stem = safe_stem(path)
    root_out = args.outdir / stem
    codec_out = root_out / "codec"
    codec_out.mkdir(parents=True, exist_ok=True)

    codec_cmd = [
        sys.executable, str(CODEC), str(path),
        "--outdir", str(codec_out),
        "--preset", args.preset,
        "--process-fps", str(args.process_fps),
        "--preview-fps", str(args.preview_fps),
        "--crf", str(args.crf),
    ]
    run(codec_cmd)

    # real_video_visualization historically calls a single input "example1".
    # Normalize filenames here so arbitrary inputs keep their own name.
    for item in list(codec_out.glob("example1_*")):
        item.rename(codec_out / item.name.replace("example1_", f"{stem}_", 1))
    codec_json = codec_out / f"{stem}_benchmark.json"
    if not codec_json.exists():
        raise RuntimeError(f"codec benchmark JSON missing: {codec_json}")

    transport_json = root_out / f"{stem}_transport.json"
    transport_cmd = [
        sys.executable, str(TRANSPORT), str(path),
        "--out", str(transport_json),
        "--preset", args.preset,
        "--process-fps", str(args.process_fps),
        "--encode-ms", str(args.encode_ms),
        "--network-ms", str(args.network_ms),
        "--consumer-ms", str(args.consumer_ms),
        "--safety-ms", str(args.safety_ms),
    ]
    if args.background_cache:
        transport_cmd.append("--background-cache")
    if args.target_pixel_fraction > 0:
        transport_cmd += ["--target-pixel-fraction", str(args.target_pixel_fraction)]
    run(transport_cmd)

    codec = json.loads(codec_json.read_text(encoding="utf-8"))
    transport_report = json.loads(transport_json.read_text(encoding="utf-8"))
    transport = transport_report["results"][0]

    combined = {
        "schema": "foveastream.benchmark-suite.v1",
        "video": str(path),
        "preset": args.preset,
        "codec": codec,
        "transport": transport,
        "artifacts": {
            "baseline_mp4": str(codec_out / f"{stem}_baseline_crf{args.crf}.mp4"),
            "foveated_mp4": str(codec_out / f"{stem}_foveated_crf{args.crf}.mp4"),
            "visualization_mp4": str(codec_out / f"{stem}_visualization.mp4"),
            "codec_json": str(codec_json),
            "transport_json": str(transport_json),
        },
    }
    combined_path = root_out / f"{stem}_benchmark_suite.json"
    combined_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")

    print("\n=== FoveaStream benchmark summary ===")
    print(f"video:                  {path}")
    print(f"preset:                 {args.preset}")
    print(f"H.264 bytes saved:      {codec['h264_byte_saving_pct']:.2f}%")
    print(f"context+ROI pixels:     {codec['context_plus_roi_pixel_saving_pct']:.2f}% saved")
    print(f"codec path processing:  {codec['processing_ms_per_frame']:.2f} ms/frame")
    print(f"transport mean:         {transport['processing_ms']['mean']:.2f} ms/frame")
    print(f"transport p95:          {transport['processing_ms']['p95']:.2f} ms/frame")
    print(f"temporal ROI reuse:     {transport['roi']['temporal_reuse_fraction']*100:.2f}%")
    print(f"layered pixels:         {transport['layered_payload']['mean_pixel_fraction']*100:.2f}% of full frame")
    print(f"combined report:        {combined_path}")
    return combined


def main() -> None:
    p = argparse.ArgumentParser(description="Run the full FoveaStream benchmark suite on arbitrary videos.")
    p.add_argument("videos", nargs="+", type=Path)
    p.add_argument("--outdir", type=Path, default=Path("output/benchmark_suite"))
    p.add_argument("--preset", choices=("balanced", "aggressive", "extreme"), default="aggressive")
    p.add_argument("--process-fps", type=float, default=30.0)
    p.add_argument("--preview-fps", type=float, default=12.0)
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--background-cache", action="store_true")
    p.add_argument("--target-pixel-fraction", type=float, default=0.0)
    p.add_argument("--encode-ms", type=float, default=15.0)
    p.add_argument("--network-ms", type=float, default=40.0)
    p.add_argument("--consumer-ms", type=float, default=20.0)
    p.add_argument("--safety-ms", type=float, default=15.0)
    args = p.parse_args()

    for video in args.videos:
        if not video.exists():
            p.error(f"video not found: {video}")
        benchmark_video(video, args)


if __name__ == "__main__":
    main()
