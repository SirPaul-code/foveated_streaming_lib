#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, subprocess, time
from pathlib import Path
import cv2
import numpy as np


def robust_motion(prev, cur):
    """Estimate camera motion with sparse KLT and return residual temporal motion."""
    h, w = cur.shape
    p0 = cv2.goodFeaturesToTrack(prev, maxCorners=220, qualityLevel=.015, minDistance=7, blockSize=5)
    M = None
    if p0 is not None and len(p0) >= 6:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(
            prev, cur, p0, None, winSize=(17, 17), maxLevel=2,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 15, .03),
        )
        if p1 is not None:
            a = p0[st.ravel() == 1].reshape(-1, 2)
            b = p1[st.ravel() == 1].reshape(-1, 2)
            if len(a) >= 6:
                M, _ = cv2.estimateAffinePartial2D(
                    a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=300
                )
    aligned = cv2.warpAffine(prev, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT) if M is not None else prev
    diff = cv2.absdiff(aligned, cur).astype(np.float32) / 255.0
    return cv2.GaussianBlur(diff, (5, 5), 0) * 4.0


def attention_from_motion(mag, prev_center, prev_conf):
    med = float(np.median(mag))
    mad = float(np.median(np.abs(mag - med))) + 1e-6
    threshold = max(med + 4.0 * mad, 0.35)
    mask = (mag > threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    h, w = mag.shape
    best, best_score = None, -1.0
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 12 or area > .25 * h * w:
            continue
        cx, cy = cents[i]
        px, py = prev_center
        distance = math.hypot(cx / w - px, cy / h - py)
        compactness = area / max(1, bw * bh)
        strength = float(np.mean(mag[labels == i]))
        score = math.log1p(area) * compactness * min(strength, 8.0) * math.exp(-3.0 * distance * (.4 + prev_conf))
        if score > best_score:
            best_score = score
            best = (cx / w, cy / h, bw / w, bh / h, min(1.0, score / 12.0))
    return best


def quality_map(h, w, cx, cy, roi_w, roi_h, confidence):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = (xx + .5) / w, (yy + .5) / h
    rx, ry = max(.055, roi_w * .8), max(.055, roi_h * .8)
    dx = np.maximum(np.abs(nx - cx) - roi_w * .55, 0) / max(rx, 1e-3)
    dy = np.maximum(np.abs(ny - cy) - roi_h * .55, 0) / max(ry, 1e-3)
    d = np.sqrt(dx * dx + dy * dy)
    q = np.exp(-2.8 * d * d)
    q = np.maximum(q, .04 + .08 * (1 - confidence))
    return np.clip(q, 0, 1).astype(np.float32)


def foveate(frame, q):
    h, w = frame.shape[:2]
    low = cv2.resize(frame, (max(1, w // 8), max(1, h // 8)), interpolation=cv2.INTER_AREA)
    peripheral = cv2.resize(low, (w, h), interpolation=cv2.INTER_LINEAR)
    q3 = q[..., None].astype(np.float32)
    out = peripheral.astype(np.float32) + (frame.astype(np.float32) - peripheral.astype(np.float32)) * q3
    return np.clip(out, 0, 255).astype(np.uint8)


def encoder(path, w, h, fps, crf):
    return subprocess.Popen([
        'ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
        '-s', f'{w}x{h}', '-r', f'{fps:.6f}', '-i', '-', '-an', '-c:v', 'libx264',
        '-preset', 'veryfast', '-crf', str(crf), '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path)
    ], stdin=subprocess.PIPE)


def process(src: Path, outdir: Path, name: str, process_fps=30, preview_fps=12):
    outdir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(src))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, round(src_fps / process_fps))
    fps = src_fps / step

    baseline_path = outdir / f'{name}_baseline_crf23.mp4'
    foveated_path = outdir / f'{name}_foveated_crf23.mp4'
    visualization_path = outdir / f'{name}_visualization.mp4'
    base_enc = encoder(baseline_path, width, height, fps, 23)
    fov_enc = encoder(foveated_path, width, height, fps, 23)

    vis_w = 1200
    vis_h = round(height * (vis_w / (width * 3)))
    vis_h += vis_h % 2
    vis_enc = encoder(visualization_path, vis_w, vis_h, min(preview_fps, fps), 24)

    small_w = 256
    small_h = max(1, round(height * small_w / width))
    prev = None
    center = np.array([.5, .5], np.float32)
    velocity = np.zeros(2, np.float32)
    confidence = 0.0
    roi_w = roi_h = .18
    total_pixels = transmitted_pixels = 0
    preprocess_s = 0.0
    preview_step = max(1, round(fps / preview_fps))
    processed = 0

    for src_i in range(frame_count):
        ok, frame = cap.read()
        if not ok:
            break
        if src_i % step:
            continue
        gray = cv2.cvtColor(cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        t0 = time.perf_counter()
        if prev is not None:
            mag = robust_motion(prev, gray)
            candidate = attention_from_motion(mag, center, confidence)
            predicted = np.clip(center + velocity, 0, 1)
            if candidate:
                z = np.array(candidate[:2], np.float32)
                c = candidate[4]
                innovation = z - predicted
                alpha = .30 + .45 * c
                new_center = np.clip(predicted + alpha * innovation, 0, 1)
                velocity = .72 * velocity + .28 * (new_center - center)
                center = new_center
                confidence = min(1.0, .88 * confidence + .28 * c)
                roi_w = float(np.clip(.86 * roi_w + .14 * max(.10, candidate[2] * 2.4), .10, .42))
                roi_h = float(np.clip(.86 * roi_h + .14 * max(.10, candidate[3] * 2.4), .10, .42))
            else:
                center = predicted
                velocity *= .90
                confidence *= .96
                roi_w, roi_h = min(.46, roi_w * 1.012), min(.46, roi_h * 1.012)

        q = quality_map(height, width, float(center[0]), float(center[1]), roi_w, roi_h, confidence)
        fov = foveate(frame, q)
        preprocess_s += time.perf_counter() - t0
        base_enc.stdin.write(frame.tobytes())
        fov_enc.stdin.write(fov.tobytes())

        total_pixels += width * height
        context_pixels = round(width * .25) * round(height * .25)
        roi_pixels = max(1, round(roi_w * width)) * max(1, round(roi_h * height))
        transmitted_pixels += context_pixels + roi_pixels

        if processed % preview_step == 0:
            left = frame.copy()
            cv2.putText(left, 'ORIGINAL', (18, 36), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2, cv2.LINE_AA)
            heat = cv2.applyColorMap(np.uint8(np.clip(q, 0, 1) * 255), cv2.COLORMAP_TURBO)
            mid = cv2.addWeighted(frame, .65, heat, .35, 0)
            x0, y0 = int((center[0] - roi_w / 2) * width), int((center[1] - roi_h / 2) * height)
            x1, y1 = int((center[0] + roi_w / 2) * width), int((center[1] + roi_h / 2) * height)
            cv2.rectangle(mid, (x0, y0), (x1, y1), (255, 255, 255), 2)
            cv2.circle(mid, (int(center[0] * width), int(center[1] * height)), 5, (255, 255, 255), -1)
            cv2.putText(mid, 'PREDICTED ATTENTION', (18, 36), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2, cv2.LINE_AA)
            right = fov.copy()
            cv2.putText(right, 'FOVEATED TRANSPORT', (18, 36), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2, cv2.LINE_AA)
            canvas = cv2.resize(np.hstack([left, mid, right]), (vis_w, vis_h))
            saving = (1 - (context_pixels + roi_pixels) / (width * height)) * 100
            text = f'auto tracking | ROI {roi_w*100:.1f}% x {roi_h*100:.1f}% | model pixel budget -{saving:.1f}%'
            cv2.rectangle(canvas, (0, canvas.shape[0] - 30), (canvas.shape[1], canvas.shape[0]), (0, 0, 0), -1)
            cv2.putText(canvas, text, (12, canvas.shape[0] - 9), cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 255, 255), 1, cv2.LINE_AA)
            vis_enc.stdin.write(canvas.tobytes())

        prev = gray
        processed += 1

    cap.release()
    for proc in (base_enc, fov_enc, vis_enc):
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError('ffmpeg encoder failed')

    result = {
        'source': src.name,
        'display_width': width,
        'display_height': height,
        'source_fps': src_fps,
        'benchmark_fps': fps,
        'frames': processed,
        'duration_s': processed / fps,
        'source_bytes': src.stat().st_size,
        'baseline_reencode_bytes': baseline_path.stat().st_size,
        'foveated_reencode_bytes': foveated_path.stat().st_size,
        'h264_byte_saving_pct': 100 * (1 - foveated_path.stat().st_size / baseline_path.stat().st_size),
        'context_plus_roi_pixel_saving_pct': 100 * (1 - transmitted_pixels / total_pixels),
        'processing_ms_per_frame': 1000 * preprocess_s / max(1, processed),
        'attention_source': 'automatic camera-motion-compensated residual motion + temporal prediction; no manual ROI',
    }
    (outdir / f'{name}_benchmark.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('videos', nargs='+')
    p.add_argument('--outdir', default='docs/assets/real_demo')
    p.add_argument('--process-fps', type=float, default=30)
    args = p.parse_args()
    out = Path(args.outdir)
    results = [process(Path(v), out, f'example{i}', process_fps=args.process_fps) for i, v in enumerate(args.videos, 1)]
    (out / 'benchmark_summary.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
