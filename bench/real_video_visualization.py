#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil, subprocess, sys, time
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from foveastream import (
    ClassAgnosticMotionRoiDetector,
    MotionDetectorConfig,
    MultiRoiTracker,
    RoiTrackerConfig,
)


PRESETS = {
    'balanced': {
        'peripheral_downscale': 8,
        'falloff_strength': 2.8,
        'quality_floor': 0.04,
        'uncertainty_floor': 0.08,
        'context_scale': 0.25,
        'max_rois': 8,
        'roi_budget_fraction': 0.30,
    },
    'aggressive': {
        'peripheral_downscale': 16,
        'falloff_strength': 4.5,
        'quality_floor': 0.01,
        'uncertainty_floor': 0.02,
        'context_scale': 0.15,
        'max_rois': 6,
        'roi_budget_fraction': 0.22,
    },
    'extreme': {
        'peripheral_downscale': 24,
        'falloff_strength': 6.5,
        'quality_floor': 0.0,
        'uncertainty_floor': 0.01,
        'context_scale': 0.10,
        'max_rois': 4,
        'roi_budget_fraction': 0.15,
    },
}


def check_ffmpeg():
    exe = shutil.which('ffmpeg')
    if not exe:
        raise SystemExit(
            'ERROR: ffmpeg was not found in PATH. Run scripts/setup.ps1 on Windows '
            'or scripts/setup.sh on Linux/macOS, then reopen your shell.'
        )
    try:
        encoders = subprocess.run([exe, '-hide_banner', '-encoders'], check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'ERROR: failed to query ffmpeg encoders: {exc}') from exc
    if 'libx264' not in encoders:
        raise SystemExit('ERROR: this benchmark requires an ffmpeg build with libx264 support.')
    return exe


def multi_quality_map(h, w, rois, *, falloff_strength, quality_floor, uncertainty_floor):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = (xx + .5) / w, (yy + .5) / h
    mean_conf = float(np.mean([r.confidence for r in rois])) if rois else 0.0
    floor = float(quality_floor) + float(uncertainty_floor) * (1 - mean_conf)
    q = np.full((h, w), floor, np.float32)
    for r0 in rois:
        r = r0.clipped()
        x0, x1, y0, y1 = r.x, r.x + r.w, r.y, r.y + r.h
        rx, ry = max(.035, r.w * .8), max(.035, r.h * .8)
        dx = np.maximum(np.maximum(x0 - nx, nx - x1), 0) / rx
        dy = np.maximum(np.maximum(y0 - ny, ny - y1), 0) / ry
        d = np.sqrt(dx * dx + dy * dy)
        local = np.exp(-float(falloff_strength) * d * d)
        inside = (nx >= x0) & (nx <= x1) & (ny >= y0) & (ny <= y1)
        local[inside] = 1.0
        # A tracked ROI stays high-detail; confidence controls how broadly it is trusted, not whether
        # the exact selected support is silently destroyed.
        q = np.maximum(q, local * (.35 + .65 * float(np.clip(r.confidence, 0, 1))))
        q[inside] = 1.0
    return np.clip(q, 0, 1).astype(np.float32)


def foveate(frame, q, *, peripheral_downscale):
    h, w = frame.shape[:2]
    d = max(1, int(peripheral_downscale))
    low = cv2.resize(frame, (max(1, w // d), max(1, h // d)), interpolation=cv2.INTER_AREA)
    peripheral = cv2.resize(low, (w, h), interpolation=cv2.INTER_LINEAR)
    q3 = q[..., None].astype(np.float32)
    out = peripheral.astype(np.float32) + (frame.astype(np.float32) - peripheral.astype(np.float32)) * q3
    return np.clip(out, 0, 255).astype(np.uint8)


def encoder(ffmpeg, path, w, h, fps, crf):
    return subprocess.Popen([
        ffmpeg, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
        '-s', f'{w}x{h}', '-r', f'{fps:.6f}', '-i', '-', '-an', '-c:v', 'libx264',
        '-preset', 'veryfast', '-crf', str(crf), '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path)
    ], stdin=subprocess.PIPE)


def process(
    src: Path,
    outdir: Path,
    name: str,
    *,
    ffmpeg: str,
    process_fps=30,
    preview_fps=12,
    peripheral_downscale=8,
    falloff_strength=2.8,
    quality_floor=.04,
    uncertainty_floor=.08,
    context_scale=.25,
    max_rois=8,
    roi_budget_fraction=.30,
    crf=23,
):
    outdir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise SystemExit(f'ERROR: could not open video: {src}')
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(src_fps / process_fps))
    fps = src_fps / step

    baseline_path = outdir / f'{name}_baseline_crf{crf}.mp4'
    foveated_path = outdir / f'{name}_foveated_crf{crf}.mp4'
    visualization_path = outdir / f'{name}_visualization.mp4'
    base_enc = encoder(ffmpeg, baseline_path, width, height, fps, crf)
    fov_enc = encoder(ffmpeg, foveated_path, width, height, fps, crf)

    vis_w = 1200
    vis_h = round(height * (vis_w / (width * 3))); vis_h += vis_h % 2
    vis_enc = encoder(ffmpeg, visualization_path, vis_w, vis_h, min(preview_fps, fps), 24)

    detector = ClassAgnosticMotionRoiDetector(MotionDetectorConfig(max_proposals=max(2, max_rois * 2)))
    tracker = MultiRoiTracker(RoiTrackerConfig(max_tracks=max_rois, pixel_budget_fraction=roi_budget_fraction))
    total_pixels = transmitted_pixels = 0
    preprocess_s = 0.0
    preview_step = max(1, round(fps / preview_fps))
    processed = 0
    roi_count_sum = 0
    roi_count_max = 0
    roi_area_sum = 0.0
    roi_area_max = 0.0
    quality_mean_sum = 0.0

    for src_i in range(frame_count):
        ok, frame = cap.read()
        if not ok: break
        if src_i % step: continue

        t0 = time.perf_counter()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        proposals = detector.propose(rgb)
        rois = tracker.update(proposals, 1.0 / max(fps, 1e-6))
        q = multi_quality_map(
            height, width, rois,
            falloff_strength=falloff_strength,
            quality_floor=quality_floor,
            uncertainty_floor=uncertainty_floor,
        )
        fov = foveate(frame, q, peripheral_downscale=peripheral_downscale)
        preprocess_s += time.perf_counter() - t0

        base_enc.stdin.write(frame.tobytes()); fov_enc.stdin.write(fov.tobytes())
        total_pixels += width * height
        context_pixels = round(width * context_scale) * round(height * context_scale)
        roi_pixels = sum(max(1, round(r.w * width)) * max(1, round(r.h * height)) for r in rois)
        transmitted_pixels += context_pixels + roi_pixels
        roi_count_sum += len(rois); roi_count_max = max(roi_count_max, len(rois))
        roi_area = sum(r.w * r.h for r in rois)
        roi_area_sum += roi_area; roi_area_max = max(roi_area_max, roi_area)
        quality_mean_sum += float(np.mean(q))

        if processed % preview_step == 0:
            left = frame.copy(); cv2.putText(left, 'ORIGINAL', (18,36), cv2.FONT_HERSHEY_SIMPLEX,.8,(255,255,255),2,cv2.LINE_AA)
            heat = cv2.applyColorMap(np.uint8(np.clip(q,0,1)*255), cv2.COLORMAP_TURBO)
            mid = cv2.addWeighted(frame,.65,heat,.35,0)
            for idx, r in enumerate(rois, 1):
                x0,y0 = int(r.x*width), int(r.y*height); x1,y1 = int((r.x+r.w)*width), int((r.y+r.h)*height)
                cv2.rectangle(mid,(x0,y0),(x1,y1),(255,255,255),2)
                cv2.putText(mid,f'ROI {idx}',(max(0,x0),max(18,y0-5)),cv2.FONT_HERSHEY_SIMPLEX,.45,(255,255,255),1,cv2.LINE_AA)
            cv2.putText(mid,'MULTI-ROI ATTENTION',(18,36),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,255,255),2,cv2.LINE_AA)
            right = fov.copy(); cv2.putText(right,'FOVEATED TRANSPORT',(18,36),cv2.FONT_HERSHEY_SIMPLEX,.8,(255,255,255),2,cv2.LINE_AA)
            canvas = cv2.resize(np.hstack([left,mid,right]),(vis_w,vis_h))
            saving = (1 - (context_pixels + roi_pixels) / (width * height)) * 100
            text = f'{len(rois)} ROI | periphery /{peripheral_downscale} | context {context_scale:.2f} | model pixel budget {saving:+.1f}% saved'
            cv2.rectangle(canvas,(0,canvas.shape[0]-30),(canvas.shape[1],canvas.shape[0]),(0,0,0),-1)
            cv2.putText(canvas,text,(12,canvas.shape[0]-9),cv2.FONT_HERSHEY_SIMPLEX,.48,(255,255,255),1,cv2.LINE_AA)
            vis_enc.stdin.write(canvas.tobytes())
        processed += 1

    cap.release()
    for proc in (base_enc,fov_enc,vis_enc):
        proc.stdin.close(); proc.wait()
        if proc.returncode != 0: raise RuntimeError('ffmpeg encoder failed')
    if not processed: raise SystemExit('ERROR: no frames decoded')

    baseline_bytes = baseline_path.stat().st_size
    foveated_bytes = foveated_path.stat().st_size
    source_bytes = src.stat().st_size
    result = {
        'source': src.name,
        'display_width': width, 'display_height': height,
        'source_fps': src_fps, 'benchmark_fps': fps,
        'frames': processed, 'duration_s': processed/fps,
        'source_bytes': source_bytes,
        'baseline_reencode_bytes': baseline_bytes,
        'foveated_reencode_bytes': foveated_bytes,
        'h264_byte_saving_pct': 100*(1-foveated_bytes/baseline_bytes),
        'source_vs_foveated_saving_pct': 100*(1-foveated_bytes/source_bytes),
        'context_plus_roi_pixel_saving_pct': 100*(1-transmitted_pixels/total_pixels),
        'processing_ms_per_frame': 1000*preprocess_s/processed,
        'processing_throughput_fps': processed/preprocess_s,
        'mean_active_roi_count': roi_count_sum/processed,
        'max_active_roi_count': roi_count_max,
        'mean_active_roi_area_fraction': roi_area_sum/processed,
        'max_active_roi_area_fraction': roi_area_max,
        'mean_quality': quality_mean_sum/processed,
        'attention_source': 'class-agnostic camera-motion-compensated residual-motion proposals + persistent multi-ROI tracker; no semantic classes required',
        'settings': {
            'peripheral_downscale': peripheral_downscale,
            'falloff_strength': falloff_strength,
            'quality_floor': quality_floor,
            'uncertainty_floor': uncertainty_floor,
            'context_scale': context_scale,
            'max_rois': max_rois,
            'roi_budget_fraction': roi_budget_fraction,
            'crf': crf,
        },
    }
    (outdir/f'{name}_benchmark.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('videos',nargs='+'); p.add_argument('--outdir',default='docs/assets/real_demo')
    p.add_argument('--process-fps',type=float,default=30); p.add_argument('--preview-fps',type=float,default=12)
    p.add_argument('--preset',choices=sorted(PRESETS),default='balanced')
    p.add_argument('--peripheral-downscale',type=int); p.add_argument('--falloff-strength',type=float)
    p.add_argument('--quality-floor',type=float); p.add_argument('--uncertainty-floor',type=float)
    p.add_argument('--context-scale',type=float); p.add_argument('--max-rois',type=int)
    p.add_argument('--roi-budget-fraction',type=float); p.add_argument('--crf',type=int,default=23)
    a=p.parse_args(); settings=dict(PRESETS[a.preset])
    for name in ('peripheral_downscale','falloff_strength','quality_floor','uncertainty_floor','context_scale','max_rois','roi_budget_fraction'):
        value=getattr(a,name)
        if value is not None: settings[name]=value
    if settings['peripheral_downscale']<1: p.error('--peripheral-downscale must be >= 1')
    if settings['max_rois']<1: p.error('--max-rois must be >= 1')
    if not 0 <= settings['context_scale'] <= 1: p.error('--context-scale must be in [0,1]')
    if not 0 <= settings['roi_budget_fraction'] <= 1: p.error('--roi-budget-fraction must be in [0,1]')
    ffmpeg=check_ffmpeg(); out=Path(a.outdir)
    results=[process(Path(v),out,f'example{i}',ffmpeg=ffmpeg,process_fps=a.process_fps,preview_fps=a.preview_fps,crf=a.crf,**settings) for i,v in enumerate(a.videos,1)]
    (out/'benchmark_summary.json').write_text(json.dumps(results,indent=2)); print(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
