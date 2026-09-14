#!/usr/bin/env python3
"""Synthetic benchmark for low-rate semantic refresh + high-rate local tracking.

This does not claim gaze accuracy. It measures a systems property: whether a predictive
ROI policy can keep a moving target inside a smaller transmitted support than a naive
1 Hz reacquisition policy when local flow/IMU-rate evidence is available.
"""
from __future__ import annotations
import argparse, json, math, pathlib, sys
import numpy as np

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from foveastream.predictive import AttentionMeasurement, PredictiveAttentionFilter, PredictionConfig


def target(t):
    # smooth pursuit + two rapid transitions, normalized image coordinates
    x=.5+.22*math.sin(2*math.pi*.19*t)
    y=.5+.16*math.sin(2*math.pi*.13*t+.4)
    if 2.4<t<2.62: x += .16*(t-2.4)/.22
    elif t>=2.62: x += .16
    if 5.0<t<5.18: y -= .13*(t-5.0)/.18
    elif t>=5.18: y -= .13
    return np.clip([x,y],.02,.98)


def rect_contains(cx,cy,w,h,p):
    return abs(p[0]-cx)<=w/2 and abs(p[1]-cy)<=h/2


def run(seconds=8.0, local_fps=60, semantic_fps=1, seed=7, flow_noise=.0025, semantic_noise=.018, base_roi=.18):
    rng=np.random.default_rng(seed); dt=1/local_fps; n=int(seconds*local_fps)
    f=PredictiveAttentionFilter(PredictionConfig(confidence_decay_per_s=.08,max_horizon_s=1.5))
    semantic_period=max(1,round(local_fps/semantic_fps)); last_true=target(0)
    covered_pred=covered_naive=0; area_pred=area_naive=0.; naive_center=np.array([.5,.5]); trace=[]

    for i in range(n):
        t=i*dt; true=target(t)
        if i%semantic_period==0:
            z=np.clip(true+rng.normal(0,semantic_noise,2),0,1)
            f.correct(AttentionMeasurement(float(z[0]),float(z[1]),confidence=.92,variance=semantic_noise**2,timestamp_s=t))
            naive_center=z.copy()
        else:
            f.predict_to(t)

        # stand-in for a robust residual optical-flow observation available on local frames
        delta=true-last_true
        obs=delta+rng.normal(0,flow_noise,2)
        f.apply_flow(float(obs[0]),float(obs[1]),dt,confidence=.72)

        roi=f.roi(base_w=base_roi,base_h=base_roi,sigma=2.3,horizon_s=.12)
        pc=np.array([roi.x+roi.w/2,roi.y+roi.h/2])
        pred_hit=rect_contains(pc[0],pc[1],roi.w,roi.h,true)
        naive_hit=rect_contains(naive_center[0],naive_center[1],base_roi,base_roi,true)
        covered_pred+=pred_hit; covered_naive+=naive_hit
        area_pred+=roi.w*roi.h; area_naive+=base_roi*base_roi
        if i%(local_fps//10 or 1)==0:
            trace.append({'t':round(t,3),'target':true.tolist(),'predictive_center':pc.tolist(),'roi':[roi.x,roi.y,roi.w,roi.h],'naive_center':naive_center.tolist()})
        last_true=true

    return {
        'settings':{'seconds':seconds,'local_fps':local_fps,'semantic_fps':semantic_fps,'flow_noise':flow_noise,'semantic_noise':semantic_noise,'base_roi':base_roi},
        'predictive':{'coverage':covered_pred/n,'mean_frame_area_fraction':area_pred/n},
        'naive_1hz':{'coverage':covered_naive/n,'mean_frame_area_fraction':area_naive/n},
        'relative':{'coverage_gain_points':100*(covered_pred-covered_naive)/n,'area_ratio_predictive_to_naive':area_pred/max(area_naive,1e-12)},
        'trace_10hz':trace,
    }


def main():
    p=argparse.ArgumentParser(); p.add_argument('--seconds',type=float,default=8); p.add_argument('--local-fps',type=int,default=60); p.add_argument('--semantic-fps',type=float,default=1); p.add_argument('--out',default='docs/benchmark_predictive_synthetic.json'); a=p.parse_args()
    result=run(a.seconds,a.local_fps,a.semantic_fps); out=ROOT/a.out; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2)); print(json.dumps({k:v for k,v in result.items() if k!='trace_10hz'},indent=2))
if __name__=='__main__':main()
