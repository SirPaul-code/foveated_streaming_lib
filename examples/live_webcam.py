#!/usr/bin/env python3
"""Live source -> FoveaStream -> local preview.

This is intentionally provider-agnostic. Replace the preview section with your own transport/model sink.
"""
from __future__ import annotations
import argparse
import time
import cv2

from foveastream import FoveaStreamRuntime, StreamRuntimeConfig


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--camera', type=int, default=0)
    p.add_argument('--preset', choices=('balanced','aggressive','extreme'), default='aggressive')
    args = p.parse_args()

    cfg = StreamRuntimeConfig.preset(args.preset, auto_motion_proposals=True)
    runtime = FoveaStreamRuntime(cfg)
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise SystemExit(f'could not open camera {args.camera}')

    try:
        while True:
            ok, bgr = cap.read()
            if not ok: break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            result = runtime.process(rgb, time.monotonic())

            preview = cv2.cvtColor(result.foveated_frame, cv2.COLOR_RGB2BGR)
            h,w = preview.shape[:2]
            for i,r in enumerate(result.rois,1):
                x0,y0 = int(r.x*w),int(r.y*h); x1,y1 = int((r.x+r.w)*w),int((r.y+r.h)*h)
                cv2.rectangle(preview,(x0,y0),(x1,y1),(255,255,255),2)
                cv2.putText(preview,f'ROI {i}',(x0,max(18,y0-5)),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1,cv2.LINE_AA)
            cv2.putText(preview,f'{args.preset} | ROI={len(result.rois)} | {result.decision.reason}',(12,28),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2,cv2.LINE_AA)
            cv2.imshow('FoveaStream live - press q to quit',preview)
            if cv2.waitKey(1) & 0xFF == ord('q'): break
    finally:
        cap.release(); cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
