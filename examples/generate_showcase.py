#!/usr/bin/env python3
"""Generate a visual FoveaStream functionality showcase.

Produces MP4 + GIF examples for multi-ROI relevance, several logical tile policies,
encoder QP hints, temporal ROI reuse and the packed ROI atlas.

Use a real video:
    python examples/generate_showcase.py example3.mp4

Or generate the reproducible documentation source used by CI:
    python examples/generate_showcase.py --synthetic
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

from foveastream import (
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    TilePlanner,
    TilePlannerConfig,
    render_tile_plan,
)


TILE_VARIANTS = [
    ("02_tiles_10_linear", 10, "linear", 1.0),
    ("03_tiles_25_smoothstep", 25, "smoothstep", 1.0),
    ("04_tiles_100_gaussian", 100, "gaussian", 3.0),
    ("05_tiles_100_exponential", 100, "exponential", 3.0),
    ("06_tiles_400_gaussian", 400, "gaussian", 3.0),
]


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is required to generate showcase MP4/GIF files")
    return exe


def writer(ffmpeg: str, path: Path, width: int, height: int, fps: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", str(path),
    ], stdin=subprocess.PIPE)


def mp4_to_gif(ffmpeg: str, src: Path, dst: Path, fps: float, width: int, colors: int) -> None:
    vf = (
        f"fps={fps:.3f},scale={width}:-2:flags=lanczos,"
        f"split[s0][s1];[s0]palettegen=max_colors={int(np.clip(colors, 16, 256))}[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
        "-filter_complex", vf, "-loop", "0", str(dst),
    ], check=True)


def synthetic_source(ffmpeg: str, path: Path, *, seconds: float = 6.0, fps: float = 30.0,
                     width: int = 640, height: int = 360) -> None:
    """Create a deterministic moving scene with several independent motion regions."""
    proc = writer(ffmpeg, path, width, height, fps)
    count = max(1, round(seconds * fps))
    yy, xx = np.mgrid[0:height, 0:width]
    try:
        for i in range(count):
            t = i / fps
            # Slowly shifting structured background gives global motion for compensation to remove.
            shift = int(round(10.0 * math.sin(t * .65)))
            base = np.zeros((height, width, 3), np.uint8)
            base[..., 0] = np.uint8(np.clip(26 + (xx + shift) * 32 / width, 0, 255))
            base[..., 1] = np.uint8(np.clip(34 + yy * 42 / height, 0, 255))
            base[..., 2] = 42
            for gx in range(-40 + shift, width + 40, 40):
                cv2.line(base, (gx, 0), (gx, height), (55, 62, 68), 1)
            for gy in range(0, height, 40):
                cv2.line(base, (0, gy), (width, gy), (55, 62, 68), 1)

            # Three independently moving regions; sizes and speeds differ.
            x1 = int(55 + (width - 190) * (0.5 + 0.5 * math.sin(t * 1.2)))
            y1 = int(70 + 42 * math.sin(t * 1.75))
            cv2.rectangle(base, (x1, y1), (x1 + 95, y1 + 62), (40, 175, 238), -1)
            cv2.putText(base, "VALVE", (x1 + 8, y1 + 38), cv2.FONT_HERSHEY_SIMPLEX, .55, (15, 25, 30), 2, cv2.LINE_AA)

            x2 = int(width * .68 + 62 * math.sin(t * 1.55 + 1.2))
            y2 = int(height * .58 + 54 * math.cos(t * 1.05))
            cv2.circle(base, (x2, y2), 34, (85, 220, 90), -1)
            cv2.circle(base, (x2, y2), 11, (20, 55, 20), 3)

            x3 = int(48 + 105 * (0.5 + 0.5 * math.sin(t * 2.2 + .4)))
            y3 = int(height - 96 + 20 * math.sin(t * 1.35))
            pts = np.array([[x3, y3], [x3 + 54, y3 - 32], [x3 + 88, y3 + 18], [x3 + 28, y3 + 36]], np.int32)
            cv2.fillConvexPoly(base, pts, (210, 90, 130))

            # Static detail demonstrates that motion is only a fallback relevance source.
            cv2.rectangle(base, (width - 158, 28), (width - 25, 82), (175, 175, 185), 2)
            cv2.putText(base, "STATIC LABEL", (width - 148, 61), cv2.FONT_HERSHEY_SIMPLEX, .42, (205,205,215), 1, cv2.LINE_AA)
            cv2.putText(base, "Synthetic multi-motion scene", (14, height - 16), cv2.FONT_HERSHEY_SIMPLEX, .45, (205,205,215), 1, cv2.LINE_AA)
            proc.stdin.write(base.tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("failed to create synthetic showcase source")


def fit(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = min(width / max(1, w), height / max(1, h))
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.zeros((height, width, 3), np.uint8)
    x, y = (width - nw) // 2, (height - nh) // 2
    canvas[y:y+nh, x:x+nw] = resized
    return canvas


def banner(frame: np.ndarray, title: str, detail: str = "") -> np.ndarray:
    out = frame.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 58), (0, 0, 0), -1)
    cv2.putText(out, title, (14, 24), cv2.FONT_HERSHEY_SIMPLEX, .62, (255, 255, 255), 2, cv2.LINE_AA)
    if detail:
        cv2.putText(out, detail, (14, 48), cv2.FONT_HERSHEY_SIMPLEX, .43, (220, 220, 220), 1, cv2.LINE_AA)
    return out


def relevance_view(bgr: np.ndarray, qmap: np.ndarray, rois) -> np.ndarray:
    heat = cv2.applyColorMap(np.uint8(np.clip(qmap, 0, 1) * 255), cv2.COLORMAP_TURBO)
    out = cv2.addWeighted(bgr, .62, heat, .38, 0)
    h, w = bgr.shape[:2]
    for i, r in enumerate(rois, 1):
        x0, y0 = round(r.x*w), round(r.y*h)
        x1, y1 = round((r.x+r.w)*w), round((r.y+r.h)*h)
        cv2.rectangle(out, (x0, y0), (x1, y1), (255, 255, 255), 2)
        cv2.putText(out, f"ROI {i}", (x0, max(18, y0-5)), cv2.FONT_HERSHEY_SIMPLEX, .45, (255,255,255), 1, cv2.LINE_AA)
    return out


def qp_view(bgr: np.ndarray, qp: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    f = qp.astype(np.float32)
    lo, hi = float(f.min()) if f.size else -4.0, float(f.max()) if f.size else 18.0
    norm = 1.0 - (f - lo) / max(1e-6, hi - lo)
    heat = cv2.applyColorMap(np.uint8(np.clip(norm, 0, 1) * 255), cv2.COLORMAP_TURBO)
    heat = cv2.resize(heat, (w, h), interpolation=cv2.INTER_NEAREST)
    return cv2.addWeighted(bgr, .55, heat, .45, 0)


def temporal_view(bgr: np.ndarray, enhancements) -> np.ndarray:
    out = bgr.copy(); h, w = out.shape[:2]
    for e in enhancements:
        r = e.roi
        x0, y0 = round(r.x*w), round(r.y*h); x1, y1 = round((r.x+r.w)*w), round((r.y+r.h)*h)
        color = (70, 220, 70) if e.changed else (150, 150, 150)
        cv2.rectangle(out, (x0, y0), (x1, y1), color, 2)
        label = "SEND" if e.changed else "REUSE"
        cv2.putText(out, label, (x0, max(18, y0-5)), cv2.FONT_HERSHEY_SIMPLEX, .43, color, 1, cv2.LINE_AA)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("video", nargs="?", type=Path, help="real input video")
    p.add_argument("--synthetic", action="store_true", help="generate deterministic documentation source")
    p.add_argument("--outdir", type=Path, default=Path("docs/assets/showcase"))
    p.add_argument("--preset", choices=("balanced", "aggressive", "extreme"), default="aggressive")
    p.add_argument("--fps", type=float, default=6.0)
    p.add_argument("--seconds", type=float, default=4.0)
    p.add_argument("--width", type=int, default=540)
    p.add_argument("--height", type=int, default=540)
    p.add_argument("--gif-width", type=int, default=360)
    p.add_argument("--gif-colors", type=int, default=48)
    args = p.parse_args()
    if bool(args.video) == bool(args.synthetic):
        p.error("provide either a video path or --synthetic")

    ffmpeg = ffmpeg_path()
    args.outdir.mkdir(parents=True, exist_ok=True)
    remove_source = False
    if args.synthetic:
        source_path = args.outdir / ".synthetic_showcase_source.mp4"
        synthetic_source(ffmpeg, source_path, seconds=max(args.seconds + 1.0, 5.0))
        source_label = "synthetic deterministic multi-motion scene"
        remove_source = True
    else:
        source_path = args.video
        source_label = str(args.video)

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise SystemExit(f"could not open {source_path}")
    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    sample_every = max(1, round(src_fps / max(args.fps, .1)))
    out_fps = src_fps / sample_every
    max_frames = max(1, round(args.seconds * out_fps))

    runtime = AdaptiveTransportRuntime(AdaptiveTransportConfig(
        preset=args.preset,
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=False,
        build_atlas=True,
    ))
    planners = {
        name: TilePlanner(TilePlannerConfig(target_tiles=count, curve=curve, curve_strength=strength))
        for name, count, curve, strength in TILE_VARIANTS
    }
    names = ["01_multi_roi", *[v[0] for v in TILE_VARIANTS], "07_qp_map", "08_temporal_reuse", "09_roi_atlas"]
    mp4_paths = {name: args.outdir / f"{name}.mp4" for name in names}
    writers = {name: writer(ffmpeg, path, args.width, args.height, out_fps) for name, path in mp4_paths.items()}

    stats = {name: {"frames": 0} for name in names}
    decoded = emitted = 0
    try:
        while emitted < max_frames:
            ok, bgr = cap.read()
            if not ok:
                break
            if decoded % sample_every:
                decoded += 1
                continue
            timestamp_s = decoded / src_fps
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            result = runtime.process(rgb, timestamp_s)
            qmap = result.process_result.quality_map
            if qmap is None:
                raise RuntimeError("showcase requires quality_map output")

            rel = relevance_view(bgr, qmap, result.process_result.rois)
            rel = banner(rel, "MULTI-ROI RELEVANCE", f"active ROI: {len(result.process_result.rois)}")
            writers["01_multi_roi"].stdin.write(fit(rel, args.width, args.height).tobytes())
            stats["01_multi_roi"]["frames"] += 1

            for name, count, curve, strength in TILE_VARIANTS:
                plan = planners[name].plan(qmap)
                tiled_rgb = render_tile_plan(rgb, plan)
                tiled = cv2.cvtColor(tiled_rgb, cv2.COLOR_RGB2BGR)
                tiled = banner(
                    tiled,
                    f"{count} LOGICAL TILES / {curve.upper()}",
                    f"effective pixels {100*plan.effective_pixel_fraction:.1f}% | mean quality {plan.mean_quality:.2f}",
                )
                writers[name].stdin.write(fit(tiled, args.width, args.height).tobytes())
                stats[name]["frames"] += 1
                stats[name].setdefault("effective_pixel_fraction", []).append(plan.effective_pixel_fraction)

            qp = qp_view(bgr, result.encoder_hints.qp_delta_map)
            qp = banner(qp, "ENCODER DELTA-QP MAP", f"mean {result.encoder_hints.qp_delta_map.mean():+.1f}")
            writers["07_qp_map"].stdin.write(fit(qp, args.width, args.height).tobytes())
            stats["07_qp_map"]["frames"] += 1

            temp = temporal_view(bgr, result.layered.roi_enhancements)
            temp = banner(
                temp,
                "TEMPORAL ROI CACHE",
                f"changed {len(result.layered.changed_rois)} / active {len(result.layered.roi_enhancements)} | green=send gray=reuse",
            )
            writers["08_temporal_reuse"].stdin.write(fit(temp, args.width, args.height).tobytes())
            stats["08_temporal_reuse"]["frames"] += 1

            if result.layered.atlas is not None:
                atlas_bgr = cv2.cvtColor(result.layered.atlas.image, cv2.COLOR_RGB2BGR)
            else:
                atlas_bgr = np.zeros_like(bgr)
            atlas_bgr = banner(
                atlas_bgr,
                "PACKED CONTEXT + CHANGED ROI ATLAS",
                f"placements {len(result.layered.atlas.placements) if result.layered.atlas else 0}",
            )
            writers["09_roi_atlas"].stdin.write(fit(atlas_bgr, args.width, args.height).tobytes())
            stats["09_roi_atlas"]["frames"] += 1

            emitted += 1
            decoded += 1
    finally:
        cap.release()
        for proc in writers.values():
            if proc.stdin:
                proc.stdin.close()
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg showcase writer failed")
        if remove_source and source_path.exists():
            source_path.unlink()

    gif_paths = {}
    for name, mp4 in mp4_paths.items():
        gif = args.outdir / f"{name}.gif"
        mp4_to_gif(ffmpeg, mp4, gif, min(args.fps, out_fps), args.gif_width, args.gif_colors)
        gif_paths[name] = gif

    for name in stats:
        values = stats[name].get("effective_pixel_fraction")
        if values:
            stats[name]["mean_effective_pixel_fraction"] = float(np.mean(values))
            del stats[name]["effective_pixel_fraction"]

    manifest = {
        "schema": "foveastream.showcase.v2",
        "source": source_label,
        "preset": args.preset,
        "output_fps": out_fps,
        "frames": emitted,
        "tile_variants": [
            {"name": name, "tiles": count, "curve": curve, "strength": strength}
            for name, count, curve, strength in TILE_VARIANTS
        ],
        "outputs": {
            name: {"mp4": str(mp4_paths[name]), "gif": str(gif_paths[name]), **stats[name]}
            for name in names
        },
    }
    (args.outdir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
