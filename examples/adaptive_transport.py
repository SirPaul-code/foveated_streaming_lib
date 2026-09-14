#!/usr/bin/env python3
"""Camera/file frame -> adaptive relevance transport -> ordinary downstream consumer.

This example intentionally does not depend on a provider SDK. Replace `send_*` with your own
encoder, WebRTC, HTTP, VLM, recorder or message-bus adapter.
"""
from __future__ import annotations

import argparse
import time
import cv2

from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    LatencyBudget,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--camera', type=int, default=0)
    p.add_argument('--target-mbps', type=float, default=0.0,
                   help='optional closed-loop encoder bitrate target; feed real encoded bytes back in production')
    p.add_argument('--background-cache', action='store_true')
    args = p.parse_args()

    budget = None
    if args.target_mbps > 0:
        budget = AdaptiveBudgetConfig(target_bitrate_bps=args.target_mbps * 1_000_000)

    optimizer = AdaptiveTransportRuntime(AdaptiveTransportConfig(
        preset='aggressive',
        auto_motion_proposals=True,
        temporal_cache=True,
        background_cache=args.background_cache,
        build_atlas=True,
        latency=LatencyBudget(encode_s=.015, network_s=.040, consumer_s=.020, safety_s=.015),
        budget=budget,
    ))

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f'could not open camera {args.camera}')

    last = time.monotonic()
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            now = time.monotonic()
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            out = optimizer.process(rgb, now)

            # Path A: ordinary existing video/image pipeline.
            optimized_bgr = cv2.cvtColor(out.frame_rgb, cv2.COLOR_RGB2BGR)

            # Path B: VLM/multi-image transport.
            # send_images([out.layered.context, *[e.image for e in out.layered.changed_rois]])

            # Path C: one-image API: context + changed high-res ROIs packed into one atlas.
            # send_image(out.layered.atlas.image)

            # Path D: encoder integration. Translate this block delta-QP map to the encoder API.
            # encoder.encode(original_frame, qp_delta_map=out.encoder_hints.qp_delta_map)

            # Feed *actual* encoder output bytes back to the controller in production:
            # optimizer.feedback_encoded(encoded_bytes, max(now - last, 1e-6))

            text = (
                f'ROI={len(out.process_result.rois)} '
                f'changed={len(out.layered.changed_rois)} '
                f'payload={100*out.payload_pixel_fraction:.1f}% '
                f'horizon={optimizer.config.latency.total_s*1000:.0f}ms'
            )
            cv2.putText(optimized_bgr, text, (12, 28), cv2.FONT_HERSHEY_SIMPLEX,
                        .55, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow('FoveaStream adaptive transport - q to quit', optimized_bgr)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            last = now
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
