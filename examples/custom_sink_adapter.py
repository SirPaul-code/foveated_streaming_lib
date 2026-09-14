#!/usr/bin/env python3
"""Template: adapt ProcessResult to any existing model/transport workflow."""
from __future__ import annotations
import time
import cv2

from foveastream import CallbackSink, FoveaStreamRuntime, StreamRuntimeConfig


def send_to_existing_workflow(result):
    """Replace this body only. Do not modify FoveaStream core for a provider API."""
    # Example choices:
    # 1) ordinary one-frame consumer:
    #    my_connection.send(encode_jpeg(result.foveated_frame))
    # 2) multi-image vision API:
    #    my_model.send_images([result.context, *result.roi_views])
    # 3) encoder with spatial QP support:
    #    my_encoder.encode(original_frame, delta_qp=result.qp_map)
    print(
        f'send t={result.timestamp_s:.3f} '
        f'rois={len(result.rois)} reason={result.decision.reason} '
        f'context={None if result.context is None else result.context.shape}'
    )


def main():
    runtime = FoveaStreamRuntime(StreamRuntimeConfig.preset('aggressive', auto_motion_proposals=True))
    sink = CallbackSink(send_to_existing_workflow, only_when_send=True)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened(): raise SystemExit('could not open camera 0')
    try:
        while True:
            ok,bgr = cap.read()
            if not ok: break
            rgb = cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            runtime.push(rgb,time.monotonic(),sink)
    finally:
        cap.release()


if __name__ == '__main__':
    main()
