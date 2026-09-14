#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,math,sys,time
from pathlib import Path
import cv2,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'python'))
from foveastream import FoveationConfig,Roi,context_and_roi_views,foveate,pixel_budget,quality_map

def jpeg_bytes(rgb,q):
    ok,enc=cv2.imencode('.jpg',cv2.cvtColor(rgb,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,q]);
    if not ok:raise RuntimeError('JPEG encode failed')
    return int(enc.nbytes)
def psnr(a,b):
    mse=float(np.mean((a.astype(np.float32)-b.astype(np.float32))**2));return float('inf') if mse<=1e-12 else 10*math.log10(255**2/mse)
def run(a):
    cap=cv2.VideoCapture(str(a.input));cfg=FoveationConfig(radius_x=a.radius_x,radius_y=a.radius_y,falloff_x=a.falloff_x,falloff_y=a.falloff_y,peripheral_scale=a.peripheral_scale);roi=Roi(*a.roi);point=(roi.x+roi.w/2,roi.y+roi.h/2)
    n=0;qtime=ftime=0;baseb=fovb=viewb=basepx=viewpx=0;rp=[];gp=[]
    while n<a.frames:
        ok,bgr=cap.read()
        if not ok:break
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);h,w=rgb.shape[:2];t=time.perf_counter();q=quality_map(rgb.shape,points=[point],rois=[roi],config=cfg);t1=time.perf_counter();out=foveate(rgb,q,cfg);t2=time.perf_counter();views=context_and_roi_views(rgb,[roi],context_scale=a.context_scale,roi_max_side=a.roi_max_side);qtime+=t1-t;ftime+=t2-t1;baseb+=jpeg_bytes(rgb,a.jpeg_quality);fovb+=jpeg_bytes(out,a.jpeg_quality);viewb+=sum(jpeg_bytes(v,a.jpeg_quality) for v in views);basepx+=h*w;viewpx+=pixel_budget(views);r=roi.clipped();ys=slice(int(r.y*h),max(int((r.y+r.h)*h),int(r.y*h)+1));xs=slice(int(r.x*w),max(int((r.x+r.w)*w),int(r.x*w)+1));rp.append(psnr(rgb[ys,xs],out[ys,xs]));gp.append(psnr(rgb,out));n+=1
    cap.release();
    if not n:raise SystemExit('no frames decoded')
    result={'frames':n,'preprocess':{'quality_map_ms_per_frame':qtime*1000/n,'foveate_ms_per_frame':ftime*1000/n,'combined_fps_python_reference':n/max(qtime+ftime,1e-12)},'jpeg_transport':{'baseline_bytes':baseb,'foveated_bytes':fovb,'saved_fraction':1-fovb/baseb},'vlm_views':{'baseline_pixels':basepx,'context_plus_roi_pixels':viewpx,'pixel_saved_fraction':1-viewpx/basepx,'pixel_reduction_ratio':basepx/max(viewpx,1),'baseline_jpeg_bytes':baseb,'views_jpeg_bytes':viewb,'jpeg_saved_fraction':1-viewb/baseb},'fidelity':{'roi_pixel_identical':bool(all(math.isinf(x) for x in rp)),'global_psnr_db_mean':float(np.mean(gp))}}
    if a.output_json:Path(a.output_json).write_text(json.dumps(result,indent=2))
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--frames',type=int,default=120);p.add_argument('--roi',type=float,nargs=4,default=(.35,.30,.30,.35));p.add_argument('--radius-x',type=float,default=.12);p.add_argument('--radius-y',type=float,default=.12);p.add_argument('--falloff-x',type=float,default=.28);p.add_argument('--falloff-y',type=float,default=.28);p.add_argument('--peripheral-scale',type=float,default=.20);p.add_argument('--context-scale',type=float,default=.25);p.add_argument('--roi-max-side',type=int,default=512);p.add_argument('--jpeg-quality',type=int,default=85);p.add_argument('--output-json',type=Path);a=p.parse_args();print(json.dumps(run(a),indent=2))
if __name__=='__main__':main()
