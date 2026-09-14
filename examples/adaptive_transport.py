#!/usr/bin/env python3
"""Run the full adaptive FoveaStream stack on a camera or ordinary video file.

Provider agnostic by design. Replace the preview/write path with your own encoder,
WebRTC, HTTP, VLM, recorder or message-bus adapter.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    LatencyBudget,
)


def build_runtime(args) -> AdaptiveTransportRuntime:
    budget = None
    if args.target_mbps > 0 or args.target_pixel_fraction > 0:
        budget = AdaptiveBudgetConfig(
            target_bitrate_bps=(args.target_mbps * 1_000_000) if args.target_mbps > 0 else None,
            target_pixel_fraction=args.target_pixel_fraction if args.target_pixel_fraction > 0 else None,
        )

    return AdaptiveTransportRuntime(AdaptiveTransportConfig(
        preset=args.preset,
        auto_motion_proposals=True,
        temporal_cache=not args.no_temporal_cache,
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


def main() -> None:
    p = argparse.ArgumentParser(description='Run adaptive FoveaStream on a camera or MP4/video file.')
    source = p.add_mutually_exclusive_group()
    source.add_argument('--video', type=Path, help='video file, e.g. example3.mp4')
    source.add_argument('--camera', type=int, default=0, help='camera index when --video is not used')
    p.add_argument('--preset', choices=('balanced', 'aggressive', 'extreme'), default='aggressive')
    p.add_argument('--target-mbps', type=float, default=0.0,
                   help='optional bitrate target; feed actual encoded bytes back in a real encoder adapter')
    p.add_argument('--target-pixel-fraction', type=float, default=0.0,
                   help='optional layered-payload pixel target, e.g. 0.20')
    p.add_argument('--background-cache', action='store_true',
                   help='enable image-space background tile cache; best for mostly static cameras')
    p.add_argument('--no-temporal-cache', action='store_true')
    p.add_argument('--encode-ms', type=float, default=15.0)
    p.add_argument('--network-ms', type=float, default=40.0)
    p.add_argument('--consumer-ms', type=float, default=20.0)
    p.add_argument('--safety-ms', type=float, default=15.0)
    p.add_argument('--max-frames', type=int, default=0, help='0 = until source ends')
    p.add_argument('--no-preview', action='store_true')
    args = p.parse_args()

    runtime = build_runtime(args)
    source_value = str(args.video) if args.video else args.camera
    cap = cv2.VideoCapture(source_value)
    if not cap.isOpened():
        raise SystemExit(f'could not open source: {source_value}')

    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_index = 0
    last_feedback_ts: float | None = None
    processing_ms: list[float] = []
    active_rois: list[int] = []
    changed_rois: list[int] = []
    payload_fractions: list[float] = []

    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            if args.max_frames > 0 and frame_index >= args.max_frames:
                break

            if args.video:
                timestamp_s = frame_index / source_fps if source_fps > 0 else frame_index / 30.0
            else:
                timestamp_s = time.monotonic()

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            t0 = time.perf_counter()
            out = runtime.process(rgb, timestamp_s)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            processing_ms.append(elapsed_ms)
            active_rois.append(len(out.process_result.rois))
            changed_rois.append(len(out.layered.changed_rois))
            payload_fractions.append(float(out.payload_pixel_fraction))

            # Path A: ordinary existing video/image pipeline.
            optimized_bgr = cv2.cvtColor(out.frame_rgb, cv2.COLOR_RGB2BGR)

            # Path B: VLM/multi-image transport.
            # send_images([out.layered.context, *[e.image for e in out.layered.changed_rois if e.image is not None]])

            # Path C: one-image API: low-res context + changed high-res ROIs in one atlas.
            # if out.layered.atlas is not None:
            #     send_image(out.layered.atlas.image)

            # Path D: encoder integration. Translate the portable block delta-QP map.
            # encoder.encode(original_frame, qp_delta_map=out.encoder_hints.qp_delta_map)
            # encoded_bytes = ...
            # dt = max(timestamp_s - last_feedback_ts, 1e-6) if last_feedback_ts is not None else 1/30
            # runtime.feedback_encoded(encoded_bytes, dt)
            last_feedback_ts = timestamp_s

            qp = out.encoder_hints.qp_delta_map.astype(np.float32)
            text = (
                f'ROI={len(out.process_result.rois)} '
                f'changed={len(out.layered.changed_rois)} '
                f'payload={100*out.payload_pixel_fraction:.1f}% '
                f'QP={qp.mean():+.1f} '
                f'{elapsed_ms:.1f}ms'
            )
            cv2.putText(optimized_bgr, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        .52, (255, 255, 255), 2, cv2.LINE_AA)

            if not args.no_preview:
                cv2.imshow('FoveaStream adaptive transport - q to quit', optimized_bgr)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            frame_index += 1
    finally:
        cap.release()
        if not args.no_preview:
            cv2.destroyAllWindows()

    mean_ms = float(np.mean(processing_ms)) if processing_ms else 0.0
    roi_total = sum(active_rois)
    changed_total = sum(changed_rois)
    reuse = 1.0 - changed_total / roi_total if roi_total else 0.0
    payload = float(np.mean(payload_fractions)) if payload_fractions else 0.0
    print(
        f'frames={frame_index} mean={mean_ms:.2f}ms '
        f'effective_fps={(1000.0/mean_ms if mean_ms else 0.0):.1f} '
        f'mean_rois={(float(np.mean(active_rois)) if active_rois else 0.0):.2f} '
        f'temporal_reuse={reuse*100:.1f}% '
        f'mean_layered_pixels={payload*100:.1f}%'
    )


if __name__ == '__main__':
    main()
