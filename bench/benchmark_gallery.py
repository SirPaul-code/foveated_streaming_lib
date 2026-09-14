#!/usr/bin/env python3
"""Run a curated all-modes FoveaStream benchmark/visual gallery on one video.

This is intentionally not a Cartesian product of every numeric parameter. It exercises each major
algorithm/actuator and the representative settings a developer needs to understand the system:

- balanced/aggressive/extreme end-to-end codec+transport benchmarks;
- core actuator showcase (relevance, QP, temporal reuse, atlas);
- logical tile count sweep;
- all built-in tile degradation curves;
- all tile aggregation modes;
- multiple custom policy hook examples;
- optional user-provided custom degradation/quality/resolution/QP callbacks.

Each policy receives its own folder with MP4, GIF and metrics.json. A top-level INDEX.md and
report.json make the run browsable and machine-readable.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from foveastream import (
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    TilePlanner,
    TilePlannerConfig,
    TilePolicyContext,
    apply_tile_plan,
    render_tile_plan,
)

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_SUITE = ROOT / "bench" / "benchmark_suite.py"
SHOWCASE = ROOT / "examples" / "generate_showcase.py"


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    return stem or "video"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is required; run scripts/setup.ps1 or scripts/setup.sh first")
    return exe


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def percentile(values: list[float], p: float) -> float:
    return float(np.percentile(np.asarray(values, np.float64), p)) if values else 0.0


def load_callable(spec: str | None) -> Callable | None:
    """Load ``path/to/file.py:function_name`` without requiring a package install."""
    if not spec:
        return None
    if ":" not in spec:
        raise ValueError(f"custom callback must be FILE.py:function, got: {spec}")
    file_text, function_name = spec.rsplit(":", 1)
    path = Path(file_text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    module_name = f"foveastream_user_policy_{hashlib.sha1(str(path).encode()).hexdigest()[:12]}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot import custom policy module: {path}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    fn = getattr(module, function_name, None)
    if not callable(fn):
        raise TypeError(f"{spec} does not resolve to a callable")
    return fn


def gentle_distance(distance: float) -> float:
    d = float(np.clip(distance, 0.0, 1.0))
    return 1.0 - d ** 1.7


def focus_cliff(distance: float) -> float:
    d = float(np.clip(distance, 0.0, 1.0))
    if d <= 0.18:
        return 1.0
    return max(0.0, 1.0 - ((d - 0.18) / 0.82) ** 0.55)


def context_aware_quality(ctx: TilePolicyContext) -> float:
    center_distance = math.hypot(ctx.center_x - 0.5, ctx.center_y - 0.5) / math.sqrt(0.5)
    center_prior = max(0.0, 1.0 - center_distance)
    spatial = max(ctx.max_relevance, 0.8 * ctx.p90_relevance, 0.55 * ctx.mean_relevance)
    return min(1.0, 0.90 * spatial + 0.10 * center_prior)


def stepped_resolution(ctx: TilePolicyContext, quality: float) -> float:
    del ctx
    if quality >= 0.82:
        return 1.0
    if quality >= 0.55:
        return 0.5
    if quality >= 0.25:
        return 0.25
    return 0.125


def tiered_qp(ctx: TilePolicyContext, quality: float) -> int:
    del ctx
    if quality >= 0.85:
        return -6
    if quality >= 0.60:
        return 0
    if quality >= 0.30:
        return 8
    return 18


@dataclass(slots=True)
class PolicyVariant:
    category: str
    name: str
    description: str
    config: TilePlannerConfig
    degradation_fn: Callable[[float], float] | None = None
    quality_fn: Callable[[TilePolicyContext], float] | None = None
    resolution_fn: Callable[[TilePolicyContext, float], float] | None = None
    qp_fn: Callable[[TilePolicyContext, float], int] | None = None

    def planner(self) -> TilePlanner:
        return TilePlanner(
            self.config,
            degradation_fn=self.degradation_fn,
            quality_fn=self.quality_fn,
            resolution_fn=self.resolution_fn,
            qp_fn=self.qp_fn,
        )


def variants(args) -> list[PolicyVariant]:
    out: list[PolicyVariant] = []
    for count in (10, 25, 100, 400):
        out.append(PolicyVariant(
            "03_tile_counts", f"tiles_{count:04d}_gaussian", f"Exact {count} logical tiles, Gaussian degradation.",
            TilePlannerConfig(target_tiles=count, curve="gaussian", curve_strength=3.0, aggregation="max"),
        ))
    for curve in ("linear", "smoothstep", "gaussian", "exponential", "power"):
        out.append(PolicyVariant(
            "04_tile_curves", f"curve_{curve}", f"100 logical tiles using the built-in {curve} degradation curve.",
            TilePlannerConfig(target_tiles=100, curve=curve, curve_strength=3.0, aggregation="max"),
        ))
    for aggregation in ("mean", "p90", "max"):
        out.append(PolicyVariant(
            "05_tile_aggregation", f"aggregation_{aggregation}", f"100 Gaussian tiles using {aggregation} relevance aggregation.",
            TilePlannerConfig(target_tiles=100, curve="gaussian", curve_strength=3.0, aggregation=aggregation),
        ))
    out.extend([
        PolicyVariant(
            "06_custom_policies", "custom_gentle_distance",
            "Simple distance->quality callback with a gentle nonlinear falloff.",
            TilePlannerConfig(target_tiles=100, aggregation="max", min_quality=0.03, min_resolution_scale=0.10),
            degradation_fn=gentle_distance,
        ),
        PolicyVariant(
            "06_custom_policies", "custom_focus_cliff",
            "Simple distance->quality callback that keeps a protected near-relevance plateau then drops aggressively.",
            TilePlannerConfig(target_tiles=100, aggregation="max", min_quality=0.03, min_resolution_scale=0.10),
            degradation_fn=focus_cliff,
        ),
        PolicyVariant(
            "06_custom_policies", "custom_context_aware_quality",
            "Context-aware quality function using max/p90/mean relevance plus a mild center prior.",
            TilePlannerConfig(target_tiles=100, aggregation="max", min_quality=0.03, min_resolution_scale=0.10),
            quality_fn=context_aware_quality,
        ),
        PolicyVariant(
            "06_custom_policies", "custom_stepped_resolution",
            "Built-in Gaussian quality with custom transport-friendly resolution tiers 1.0/0.5/0.25/0.125.",
            TilePlannerConfig(target_tiles=100, curve="gaussian", aggregation="max", min_resolution_scale=0.125),
            resolution_fn=stepped_resolution,
        ),
        PolicyVariant(
            "06_custom_policies", "custom_tiered_qp",
            "Built-in Gaussian quality with explicit custom delta-QP tiers.",
            TilePlannerConfig(target_tiles=100, curve="gaussian", aggregation="max"),
            qp_fn=tiered_qp,
        ),
    ])

    if args.matrix == "full":
        for strength in (1.0, 2.0, 4.5, 7.0):
            out.append(PolicyVariant(
                "07_strength_sweep", f"gaussian_strength_{strength:g}",
                f"100 Gaussian tiles with curve_strength={strength:g}.",
                TilePlannerConfig(target_tiles=100, curve="gaussian", curve_strength=strength, aggregation="max"),
            ))
        for scale in (0.0625, 0.125, 0.25, 0.5):
            out.append(PolicyVariant(
                "08_min_scale_sweep", f"min_scale_{scale:g}",
                f"100 Gaussian tiles with minimum resolution scale {scale:g}.",
                TilePlannerConfig(target_tiles=100, curve="gaussian", aggregation="max", min_resolution_scale=scale),
            ))

    user_hooks = {
        "degradation_fn": load_callable(args.custom_degradation),
        "quality_fn": load_callable(args.custom_quality),
        "resolution_fn": load_callable(args.custom_resolution),
        "qp_fn": load_callable(args.custom_qp),
    }
    if any(user_hooks.values()):
        out.append(PolicyVariant(
            "09_user_policy", "user_policy",
            "User-provided custom tile policy callbacks loaded from FILE.py:function CLI arguments.",
            TilePlannerConfig(
                target_tiles=args.user_tiles,
                curve=args.user_fallback_curve,
                aggregation=args.user_aggregation,
                min_quality=args.user_min_quality,
                min_resolution_scale=args.user_min_scale,
            ),
            **user_hooks,
        ))
    return out


def open_writer(ffmpeg: str, path: Path, width: int, height: int, fps: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", str(path),
    ], stdin=subprocess.PIPE)


def mp4_to_gif(ffmpeg: str, src: Path, dst: Path, fps: float, width: int) -> None:
    vf = (
        f"fps={fps:.3f},scale={width}:-2:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=max_colors=128[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
                    "-filter_complex", vf, "-loop", "0", str(dst)], check=True)


def panel(frame_rgb: np.ndarray, plan, title: str, detail: str, output_width: int) -> np.ndarray:
    degraded = apply_tile_plan(frame_rgb, plan)
    heat = render_tile_plan(frame_rgb, plan, alpha=0.48, show_labels=False)
    left = cv2.cvtColor(degraded, cv2.COLOR_RGB2BGR)
    right = cv2.cvtColor(heat, cv2.COLOR_RGB2BGR)
    h, w = left.shape[:2]
    combined = np.concatenate([left, right], axis=1)
    cv2.rectangle(combined, (0, 0), (combined.shape[1], 64), (0, 0, 0), -1)
    cv2.putText(combined, title, (14, 25), cv2.FONT_HERSHEY_SIMPLEX, .62, (255,255,255), 2, cv2.LINE_AA)
    cv2.putText(combined, detail, (14, 51), cv2.FONT_HERSHEY_SIMPLEX, .42, (220,220,220), 1, cv2.LINE_AA)
    if combined.shape[1] > output_width:
        scale = output_width / combined.shape[1]
        combined = cv2.resize(combined, (output_width, max(2, round(combined.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    return combined


def run_policy_matrix(video: Path, out_root: Path, args) -> list[dict]:
    ffmpeg = ffmpeg_path()
    policy_variants = variants(args)
    planners = {v.name: v.planner() for v in policy_variants}
    metrics = {v.name: {"planner_ms": [], "effective": [], "quality": [], "qp": []} for v in policy_variants}

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"could not open {video}")
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    sample_every = max(1, round(source_fps / max(args.gallery_fps, .1)))
    output_fps = source_fps / sample_every
    max_frames = max(1, round(args.gallery_seconds * output_fps))
    runtime = AdaptiveTransportRuntime(AdaptiveTransportConfig(
        preset=args.gallery_preset,
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=False,
        build_atlas=True,
    ))

    writers = {}
    output_sizes: dict[str, tuple[int, int]] = {}
    mp4s: dict[str, Path] = {}
    gifs: dict[str, Path] = {}
    base_ms: list[float] = []
    decoded = emitted = 0

    try:
        while emitted < max_frames:
            ok, bgr = cap.read()
            if not ok:
                break
            if decoded % sample_every:
                decoded += 1
                continue
            timestamp_s = decoded / source_fps
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            t0 = time.perf_counter()
            result = runtime.process(rgb, timestamp_s)
            base_ms.append((time.perf_counter() - t0) * 1000.0)
            qmap = result.process_result.quality_map
            if qmap is None:
                raise RuntimeError("gallery requires quality_map output")

            for variant in policy_variants:
                planner = planners[variant.name]
                p0 = time.perf_counter()
                plan = planner.plan(qmap)
                metrics[variant.name]["planner_ms"].append((time.perf_counter() - p0) * 1000.0)
                metrics[variant.name]["effective"].append(plan.effective_pixel_fraction)
                metrics[variant.name]["quality"].append(plan.mean_quality)
                metrics[variant.name]["qp"].append(plan.mean_qp_delta)
                detail = (
                    f"tiles={plan.tile_count} curve={plan.curve} agg={plan.aggregation} | "
                    f"effective pixels={100*plan.effective_pixel_fraction:.1f}% | "
                    f"meanQ={plan.mean_quality:.2f} mean dQP={plan.mean_qp_delta:+.1f}"
                )
                view = panel(rgb, plan, variant.name, detail, args.gallery_width)
                if variant.name not in writers:
                    folder = out_root / variant.category / variant.name
                    folder.mkdir(parents=True, exist_ok=True)
                    mp4 = folder / "preview.mp4"
                    gif = folder / "preview.gif"
                    mp4s[variant.name] = mp4
                    gifs[variant.name] = gif
                    output_sizes[variant.name] = (view.shape[1], view.shape[0])
                    writers[variant.name] = open_writer(ffmpeg, mp4, view.shape[1], view.shape[0], output_fps)
                writers[variant.name].stdin.write(view.tobytes())

            emitted += 1
            decoded += 1
    finally:
        cap.release()
        for proc in writers.values():
            if proc.stdin:
                proc.stdin.close()
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg gallery writer failed")

    reports: list[dict] = []
    for variant in policy_variants:
        m = metrics[variant.name]
        gif_width = min(args.gif_width, output_sizes[variant.name][0])
        if not args.no_gif:
            mp4_to_gif(ffmpeg, mp4s[variant.name], gifs[variant.name], min(args.gallery_fps, output_fps), gif_width)
        record = {
            "category": variant.category,
            "name": variant.name,
            "description": variant.description,
            "frames": emitted,
            "preview_fps": output_fps,
            "config": {
                "target_tiles": variant.config.target_tiles,
                "curve": variant.config.curve,
                "curve_strength": variant.config.curve_strength,
                "aggregation": variant.config.aggregation,
                "min_quality": variant.config.min_quality,
                "min_resolution_scale": variant.config.min_resolution_scale,
                "fovea_qp_delta": variant.config.fovea_qp_delta,
                "periphery_qp_delta": variant.config.periphery_qp_delta,
                "custom_degradation": variant.degradation_fn is not None,
                "custom_quality": variant.quality_fn is not None,
                "custom_resolution": variant.resolution_fn is not None,
                "custom_qp": variant.qp_fn is not None,
            },
            "metrics": {
                "planner_ms_mean": float(np.mean(m["planner_ms"])) if m["planner_ms"] else 0.0,
                "planner_ms_p95": percentile(m["planner_ms"], 95),
                "effective_pixel_fraction_mean": float(np.mean(m["effective"])) if m["effective"] else 0.0,
                "mean_tile_quality": float(np.mean(m["quality"])) if m["quality"] else 0.0,
                "mean_tile_qp_delta": float(np.mean(m["qp"])) if m["qp"] else 0.0,
            },
            "artifacts": {
                "mp4": str(mp4s[variant.name]),
                "gif": str(gifs[variant.name]) if not args.no_gif else None,
            },
        }
        folder = out_root / variant.category / variant.name
        (folder / "metrics.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        reports.append(record)

    base_record = {
        "preset": args.gallery_preset,
        "frames": emitted,
        "mean_ms": float(np.mean(base_ms)) if base_ms else 0.0,
        "p50_ms": percentile(base_ms, 50),
        "p95_ms": percentile(base_ms, 95),
        "p99_ms": percentile(base_ms, 99),
    }
    (out_root / "tile_matrix_runtime.json").write_text(json.dumps(base_record, indent=2), encoding="utf-8")
    return reports


def run_preset_benchmarks(video: Path, root: Path, args) -> list[dict]:
    if args.skip_preset_benchmarks:
        return []
    records = []
    for preset in ("balanced", "aggressive", "extreme"):
        outdir = root / "01_presets" / preset
        cmd = [
            sys.executable, str(BENCHMARK_SUITE), str(video),
            "--outdir", str(outdir),
            "--preset", preset,
            "--process-fps", str(args.process_fps),
            "--preview-fps", str(args.preview_fps),
            "--crf", str(args.crf),
            "--tiles", "100",
            "--tile-curve", "gaussian",
        ]
        run(cmd)
        combined = outdir / safe_stem(video) / f"{safe_stem(video)}_benchmark_suite.json"
        record = json.loads(combined.read_text(encoding="utf-8"))
        records.append(record)
    return records


def run_core_showcase(video: Path, root: Path, args) -> None:
    if args.skip_showcase:
        return
    cmd = [
        sys.executable, str(SHOWCASE), str(video),
        "--outdir", str(root / "02_core_actuators"),
        "--preset", args.gallery_preset,
        "--fps", str(args.gallery_fps),
        "--seconds", str(args.gallery_seconds),
        "--width", str(args.showcase_size),
        "--height", str(args.showcase_size),
    ]
    run(cmd)


def write_index(root: Path, video: Path, report: dict) -> None:
    lines = [
        f"# FoveaStream benchmark gallery — `{video.name}`",
        "",
        "This directory was generated by `bench/benchmark_gallery.py`.",
        "",
        "## Source",
        "",
        f"- SHA256: `{report['source']['sha256']}`",
        f"- bytes: `{report['source']['bytes']}`",
        "",
        "## What to inspect",
        "",
        "- `01_presets/` — balanced/aggressive/extreme end-to-end codec + transport results.",
        "- `02_core_actuators/` — multi-ROI relevance, representative tiles, QP map, temporal reuse and atlas.",
        "- `03_tile_counts/` — exact logical tile-count sweep.",
        "- `04_tile_curves/` — every built-in degradation curve at the same tile count.",
        "- `05_tile_aggregation/` — mean/p90/max relevance aggregation.",
        "- `06_custom_policies/` — copyable callback examples.",
        "- `07_strength_sweep/` and `08_min_scale_sweep/` — added by `--matrix full`.",
        "- `09_user_policy/` — appears when user callback CLI options are supplied.",
        "",
        "## Logical tile policy matrix",
        "",
        "| Policy | Tiles | Curve | Aggregation | Effective pixels | Mean quality | Mean dQP | Planner p95 |",
        "|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for item in report["tile_policies"]:
        cfg, m = item["config"], item["metrics"]
        lines.append(
            f"| `{item['category']}/{item['name']}` | {cfg['target_tiles']} | {cfg['curve']} | {cfg['aggregation']} | "
            f"{100*m['effective_pixel_fraction_mean']:.2f}% | {m['mean_tile_quality']:.3f} | "
            f"{m['mean_tile_qp_delta']:+.2f} | {m['planner_ms_p95']:.3f} ms |"
        )
    lines += [
        "",
        "`effective pixels` is the logical multi-resolution estimate `sum(tile area * scale^2)`. It is not an encoded-byte measurement.",
        "For each policy open `preview.mp4` or `preview.gif`: left is the actual reference tile-resolution degradation, right is the quality heat/grid.",
        "",
    ]
    (root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Run all major FoveaStream modes/settings on one video and build a visual benchmark gallery.")
    p.add_argument("video", type=Path)
    p.add_argument("--outdir", type=Path, default=Path("output/gallery"))
    p.add_argument("--matrix", choices=("standard", "full"), default="standard",
                   help="standard covers every major function; full adds strength/min-scale sweeps")
    p.add_argument("--gallery-preset", choices=("balanced", "aggressive", "extreme"), default="aggressive")
    p.add_argument("--gallery-fps", type=float, default=8.0)
    p.add_argument("--gallery-seconds", type=float, default=6.0)
    p.add_argument("--gallery-width", type=int, default=1280, help="max width of two-panel tile policy previews")
    p.add_argument("--gif-width", type=int, default=720)
    p.add_argument("--showcase-size", type=int, default=720)
    p.add_argument("--no-gif", action="store_true")
    p.add_argument("--skip-preset-benchmarks", action="store_true")
    p.add_argument("--skip-showcase", action="store_true")
    p.add_argument("--process-fps", type=float, default=30.0)
    p.add_argument("--preview-fps", type=float, default=12.0)
    p.add_argument("--crf", type=int, default=23)

    p.add_argument("--custom-degradation", help="FILE.py:function implementing distance -> raw quality")
    p.add_argument("--custom-quality", help="FILE.py:function implementing TilePolicyContext -> raw quality")
    p.add_argument("--custom-resolution", help="FILE.py:function implementing (TilePolicyContext, quality) -> scale")
    p.add_argument("--custom-qp", help="FILE.py:function implementing (TilePolicyContext, quality) -> signed delta QP")
    p.add_argument("--user-tiles", type=int, default=100)
    p.add_argument("--user-fallback-curve", choices=("linear", "smoothstep", "gaussian", "exponential", "power"), default="gaussian")
    p.add_argument("--user-aggregation", choices=("mean", "p90", "max"), default="max")
    p.add_argument("--user-min-quality", type=float, default=0.04)
    p.add_argument("--user-min-scale", type=float, default=0.125)
    args = p.parse_args()

    video = args.video.resolve()
    if not video.exists():
        p.error(f"video not found: {video}")
    ffmpeg_path()  # fail before doing expensive work

    root = args.outdir / safe_stem(video)
    root.mkdir(parents=True, exist_ok=True)
    print(f"\nFoveaStream gallery output: {root}\n")

    preset_reports = run_preset_benchmarks(video, root, args)
    run_core_showcase(video, root, args)
    tile_reports = run_policy_matrix(video, root, args)

    report = {
        "schema": "foveastream.benchmark-gallery.v1",
        "source": {
            "path": str(video),
            "name": video.name,
            "sha256": sha256(video),
            "bytes": video.stat().st_size,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
        },
        "matrix": args.matrix,
        "preset_benchmarks": preset_reports,
        "tile_policies": tile_reports,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_index(root, video, report)

    print("\n=== Gallery complete ===")
    print(f"index:  {root / 'INDEX.md'}")
    print(f"report: {root / 'report.json'}")
    print(f"open:   {root}")


if __name__ == "__main__":
    main()
