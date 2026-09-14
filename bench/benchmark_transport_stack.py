#!/usr/bin/env python3
"""Benchmark the full adaptive relevance transport stack on real video.

This complements the codec benchmark. It measures preprocessing latency, temporal ROI reuse,
layered pixel load, atlas load, background delta activity, encoder hints and logical tile planning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    LatencyBudget,
    TilePlanner,
    TilePlannerConfig,
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def pct(values, q):
    return float(np.percentile(np.asarray(values, np.float64), q)) if values else 0.0


def benchmark(path: Path, args) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f'could not open {path}')
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    source_dt = 1.0 / fps if fps > 0 else 1.0 / max(args.process_fps, 1.0)
    sample_every = max(1, round(fps / args.process_fps)) if fps > args.process_fps > 0 else 1

    budget = AdaptiveBudgetConfig(target_pixel_fraction=args.target_pixel_fraction) if args.target_pixel_fraction > 0 else None
    runtime = AdaptiveTransportRuntime(AdaptiveTransportConfig(
        preset=args.preset,
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=args.background_cache,
        build_atlas=True,
        latency=LatencyBudget(
            encode_s=args.encode_ms / 1000.0,
            network_s=args.network_ms / 1000.0,
            consumer_s=args.consumer_ms / 1000.0,
            safety_s=args.safety_ms / 1000.0,
        ),
        budget=budget,
    ))
    tile_planner = None
    if args.tiles > 0:
        tile_planner = TilePlanner(TilePlannerConfig(
            target_tiles=args.tiles,
            curve=args.tile_curve,
            curve_strength=args.tile_strength,
            aggregation=args.tile_aggregation,
            min_quality=args.tile_min_quality,
            min_resolution_scale=args.tile_min_scale,
        ))

    times = []
    roi_counts = []
    changed_counts = []
    pixel_fractions = []
    atlas_fractions = []
    bg_counts = []
    qp_means = []
    qp_mins = []
    qp_maxs = []
    strengths = []
    tile_pixel_fractions = []
    tile_mean_quality = []
    tile_mean_qp = []
    processed = 0
    decoded_index = 0

    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            if decoded_index % sample_every:
                decoded_index += 1
                continue
            timestamp_s = decoded_index * source_dt
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            t0 = time.perf_counter()
            out = runtime.process(rgb, timestamp_s)
            tile_plan = None
            if tile_planner is not None and out.process_result.quality_map is not None:
                tile_plan = tile_planner.plan(out.process_result.quality_map)
            times.append((time.perf_counter() - t0) * 1000.0)
            roi_counts.append(len(out.process_result.rois))
            changed_counts.append(len(out.layered.changed_rois))
            pixel_fractions.append(out.payload_pixel_fraction)
            if out.layered.atlas is not None:
                atlas = out.layered.atlas.image
                atlas_fractions.append((atlas.shape[0] * atlas.shape[1]) / max(1, width * height))
            bg_counts.append(len(out.layered.background_updates))
            qp = out.encoder_hints.qp_delta_map.astype(np.float32)
            qp_means.append(float(qp.mean()) if qp.size else 0.0)
            qp_mins.append(float(qp.min()) if qp.size else 0.0)
            qp_maxs.append(float(qp.max()) if qp.size else 0.0)
            if out.controller_state is not None:
                strengths.append(out.controller_state.strength)
            if tile_plan is not None:
                tile_pixel_fractions.append(tile_plan.effective_pixel_fraction)
                tile_mean_quality.append(tile_plan.mean_quality)
                tile_mean_qp.append(tile_plan.mean_qp_delta)
            processed += 1
            decoded_index += 1
    finally:
        cap.release()

    mean_ms = float(np.mean(times)) if times else 0.0
    changed_total = sum(changed_counts)
    roi_total = sum(roi_counts)
    return {
        'source': str(path),
        'sha256': sha256(path),
        'source_bytes': path.stat().st_size,
        'width': width,
        'height': height,
        'source_fps': fps,
        'source_frame_count': total,
        'processed_frames': processed,
        'sample_every': sample_every,
        'preset': args.preset,
        'latency_budget_ms': runtime.config.latency.total_s * 1000.0,
        'processing_ms': {
            'mean': mean_ms,
            'p50': pct(times, 50),
            'p95': pct(times, 95),
            'p99': pct(times, 99),
            'effective_fps_from_mean': 1000.0 / mean_ms if mean_ms > 0 else 0.0,
        },
        'roi': {
            'mean_active': float(np.mean(roi_counts)) if roi_counts else 0.0,
            'max_active': max(roi_counts, default=0),
            'mean_changed': float(np.mean(changed_counts)) if changed_counts else 0.0,
            'max_changed': max(changed_counts, default=0),
            'temporal_reuse_fraction': 1.0 - changed_total / roi_total if roi_total else 0.0,
        },
        'layered_payload': {
            'mean_pixel_fraction': float(np.mean(pixel_fractions)) if pixel_fractions else 0.0,
            'p95_pixel_fraction': pct(pixel_fractions, 95),
            'mean_pixels_saved_percent': 100.0 * (1.0 - float(np.mean(pixel_fractions))) if pixel_fractions else 0.0,
            'mean_atlas_pixel_fraction': float(np.mean(atlas_fractions)) if atlas_fractions else 0.0,
        },
        'logical_tiles': {
            'enabled': tile_planner is not None,
            'target_tiles': args.tiles if tile_planner is not None else 0,
            'curve': args.tile_curve if tile_planner is not None else None,
            'curve_strength': args.tile_strength if tile_planner is not None else None,
            'aggregation': args.tile_aggregation if tile_planner is not None else None,
            'mean_effective_pixel_fraction': float(np.mean(tile_pixel_fractions)) if tile_pixel_fractions else None,
            'mean_pixels_saved_percent': 100.0 * (1.0 - float(np.mean(tile_pixel_fractions))) if tile_pixel_fractions else None,
            'mean_quality': float(np.mean(tile_mean_quality)) if tile_mean_quality else None,
            'mean_delta_qp': float(np.mean(tile_mean_qp)) if tile_mean_qp else None,
        },
        'background_cache': {
            'enabled': bool(args.background_cache),
            'mean_changed_tiles': float(np.mean(bg_counts)) if bg_counts else 0.0,
            'max_changed_tiles': max(bg_counts, default=0),
        },
        'encoder_hints': {
            'block_size': runtime.runtime.config.qp_block,
            'mean_delta_qp': float(np.mean(qp_means)) if qp_means else 0.0,
            'min_delta_qp_seen': min(qp_mins, default=0.0),
            'max_delta_qp_seen': max(qp_maxs, default=0.0),
        },
        'controller': {
            'enabled': budget is not None,
            'target_pixel_fraction': args.target_pixel_fraction if budget else None,
            'mean_strength': float(np.mean(strengths)) if strengths else None,
            'final_strength': strengths[-1] if strengths else None,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('videos', nargs='+', type=Path)
    p.add_argument('--out', type=Path, default=Path('output/transport_benchmark.json'))
    p.add_argument('--preset', choices=('balanced', 'aggressive', 'extreme'), default='aggressive')
    p.add_argument('--process-fps', type=float, default=30.0)
    p.add_argument('--background-cache', action='store_true')
    p.add_argument('--target-pixel-fraction', type=float, default=0.0)
    p.add_argument('--encode-ms', type=float, default=15.0)
    p.add_argument('--network-ms', type=float, default=40.0)
    p.add_argument('--consumer-ms', type=float, default=20.0)
    p.add_argument('--safety-ms', type=float, default=15.0)
    p.add_argument('--tiles', type=int, default=100, help='exact logical tile count; 0 disables')
    p.add_argument('--tile-curve', choices=('linear', 'smoothstep', 'gaussian', 'exponential', 'power'), default='gaussian')
    p.add_argument('--tile-strength', type=float, default=3.0)
    p.add_argument('--tile-aggregation', choices=('mean', 'max', 'p90'), default='max')
    p.add_argument('--tile-min-quality', type=float, default=0.04)
    p.add_argument('--tile-min-scale', type=float, default=0.125)
    args = p.parse_args()

    results = [benchmark(path, args) for path in args.videos]
    report = {
        'schema': 'foveastream.transport-benchmark.v2',
        'environment': {
            'python': sys.version,
            'platform': platform.platform(),
            'opencv': cv2.__version__,
            'numpy': np.__version__,
        },
        'results': results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
