#!/usr/bin/env python3
"""Template: insert FoveaStream between an existing frame source and downstream consumer."""
from __future__ import annotations
import time
import cv2

from foveastream import CallbackFrameSink, FoveaStreamTransform, RealtimeBridge


def send_to_existing_workflow(packet):
    """Replace this body only. Everything upstream stays provider-agnostic."""
    result = packet.result

    # Ordinary image/video path:
    # my_connection.send(encode(packet.frame_rgb))

    # Multi-image VLM path:
    # my_model.send_images([result.context, *result.roi_views])

    # Encoder with ROI/QP support:
    # my_encoder.encode(original_frame, delta_qp=result.qp_map)

    print(
        f'emit t={packet.timestamp_s:.3f} '
        f'rois={len(result.rois)} reason={result.decision.reason} '
        f'frame={packet.frame_rgb.shape}'
    )


def main():
    transform = FoveaStreamTransform(
        preset='aggressive',
        auto_motion_proposals=True,
        emit_policy='every_frame',
    )
    sink = CallbackFrameSink(send_to_existing_workflow)

    # queue_size=1 + latest keeps latency bounded if the camera temporarily outruns processing.
    bridge = RealtimeBridge(transform, sink, queue_size=1, drop_policy='latest')

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        bridge.close(drain=False)
        raise SystemExit('could not open camera 0')

    seq = 0
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            seq += 1

            # This is the only line an existing callback-driven source needs.
            bridge.submit(rgb, time.monotonic(), metadata={'seq': seq})
    finally:
        cap.release()
        bridge.close(drain=True, timeout=5.0)
        print('bridge stats:', bridge.stats)


if __name__ == '__main__':
    main()
