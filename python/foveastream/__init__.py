from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from math import atan, radians, tan
from typing import Iterable, Sequence
import numpy as np
try:
    import cv2
except Exception:
    cv2 = None

class Falloff(IntEnum):
    LINEAR=0; SMOOTHSTEP=1; GAUSSIAN=2

@dataclass(slots=True)
class FoveationConfig:
    radius_x:float=0.12; radius_y:float=0.12; falloff_x:float=0.28; falloff_y:float=0.28
    peripheral_scale:float=0.25; peripheral_desaturate:float=0.0; gamma:float=1.0; falloff:Falloff=Falloff.SMOOTHSTEP

@dataclass(slots=True)
class Roi:
    x:float; y:float; w:float; h:float; confidence:float=1.0
    def clipped(self):
        x0=float(np.clip(self.x,0,1)); y0=float(np.clip(self.y,0,1)); x1=float(np.clip(self.x+self.w,x0,1)); y1=float(np.clip(self.y+self.h,y0,1))
        return Roi(x0,y0,x1-x0,y1-y0,float(np.clip(self.confidence,0,1)))

@dataclass(slots=True)
class FocusCandidate:
    x:float; y:float; confidence:float=1.0; weight:float=1.0

@dataclass(slots=True)
class FocusTrackerConfig:
    alpha:float=0.65; beta:float=0.08; latency_ms:float=50.0; horizontal_fov_deg:float=70.0; vertical_fov_deg:float=50.0

class FocusTracker:
    def __init__(self,cfg=None): self.cfg=cfg or FocusTrackerConfig(); self.pos=np.array([.5,.5],np.float32); self.vel=np.zeros(2,np.float32); self.initialized=False
    @staticmethod
    def fuse(candidates:Iterable[FocusCandidate]):
        ps=[]; ws=[]
        for c in candidates:
            w=max(0,c.confidence)*max(0,c.weight)
            if w>0: ps.append((np.clip(c.x,0,1),np.clip(c.y,0,1))); ws.append(w)
        if not ws:return None
        p=np.average(np.asarray(ps,np.float32),axis=0,weights=np.asarray(ws)); return float(p[0]),float(p[1])
    def update(self,measurement,dt_s):
        z=np.clip(np.asarray(measurement,np.float32),0,1); dt=max(float(dt_s),1e-4)
        if not self.initialized: self.pos[:]=z; self.vel[:]=0; self.initialized=True; return self.predicted()
        pred=self.pos+self.vel*dt; r=z-pred; self.pos=np.clip(pred+np.clip(self.cfg.alpha,0,1)*r,0,1); self.vel=self.vel+(max(0,self.cfg.beta)/dt)*r; return self.predicted()
    def predicted(self):
        h=min(max(self.cfg.latency_ms,0)/1000,.5); p=np.clip(self.pos+self.vel*h,0,1); return float(p[0]),float(p[1])
    def compensate_imu(self,point,gyro_yaw_rad_s,gyro_pitch_rad_s,dt_s):
        hf=radians(max(1,self.cfg.horizontal_fov_deg)); vf=radians(max(1,self.cfg.vertical_fov_deg)); tx,ty=tan(hf/2),tan(vf/2); x,y=np.clip(point,0,1); px,py=(2*x-1)*tx,(2*y-1)*ty; ax=atan(px)-gyro_yaw_rad_s*dt_s; ay=atan(py)+gyro_pitch_rad_s*dt_s; o=np.clip([.5*(tan(ax)/tx+1),.5*(tan(ay)/ty+1)],0,1); return float(o[0]),float(o[1])

def _curve(t,falloff,gamma):
    t=np.clip(t,0,1)
    if falloff==Falloff.LINEAR:q=1-t
    elif falloff==Falloff.GAUSSIAN:q=np.exp(-4.5*t*t)
    else:s=t*t*(3-2*t); q=1-s
    return np.power(np.clip(q,0,1),max(gamma,.01)).astype(np.float32)

def quality_map(shape,*,points=(),rois=(),config=None,custom_map=None):
    cfg=config or FoveationConfig(); h,w=int(shape[0]),int(shape[1]); yy,xx=np.mgrid[0:h,0:w].astype(np.float32); nx=xx/max(w-1,1); ny=yy/max(h-1,1); out=np.zeros((h,w),np.float32)
    for px,py in points:
        px,py=float(np.clip(px,0,1)),float(np.clip(py,0,1)); rx,ry=max(cfg.radius_x,1e-6),max(cfg.radius_y,1e-6); ox,oy=rx+max(cfg.falloff_x,1e-6),ry+max(cfg.falloff_y,1e-6); dx,dy=np.abs(nx-px),np.abs(ny-py); inner=np.sqrt((dx/rx)**2+(dy/ry)**2); outer=np.sqrt((dx/ox)**2+(dy/oy)**2); den=np.maximum(inner/np.maximum(outer,1e-6)-1,1e-6); t=np.clip((inner-1)/den,0,1); q=_curve(t,cfg.falloff,cfg.gamma); q[inner<=1]=1; q[outer>=1]=0; out=np.maximum(out,q)
    for r0 in rois:
        r=r0.clipped(); x0,x1=r.x,r.x+r.w; y0,y1=r.y,r.y+r.h; dx=np.maximum(np.maximum(x0-nx,nx-x1),0); dy=np.maximum(np.maximum(y0-ny,ny-y1),0); d=np.sqrt((dx/max(cfg.falloff_x,1e-6))**2+(dy/max(cfg.falloff_y,1e-6))**2); q=_curve(d,cfg.falloff,cfg.gamma)*r.confidence; inside=(nx>=x0)&(nx<=x1)&(ny>=y0)&(ny<=y1); q[inside]=r.confidence; q[d>=1]=0; out=np.maximum(out,q)
    if custom_map is not None:
        cm=np.asarray(custom_map,np.float32)
        if cm.shape!=(h,w): raise ValueError(f'custom_map must be {(h,w)}, got {cm.shape}')
        out=np.maximum(out,np.clip(cm,0,1))
    return np.ascontiguousarray(out,np.float32)

def _resize(frame,size,interp=None):
    if cv2 is not None:return cv2.resize(frame,size,interpolation=interp or cv2.INTER_LINEAR)
    oh,ow=size[1],size[0]; ys=np.round(np.linspace(0,frame.shape[0]-1,oh)).astype(int); xs=np.round(np.linspace(0,frame.shape[1]-1,ow)).astype(int); return frame[np.ix_(ys,xs)]

def _down_up(frame,scale):
    h,w=frame.shape[:2]; s=float(np.clip(scale,.01,1)); dw,dh=max(1,round(w*s)),max(1,round(h*s)); low=_resize(frame,(dw,dh),cv2.INTER_AREA if cv2 is not None else None); return _resize(low,(w,h))

def foveate(frame_rgb,qmap,config=None):
    cfg=config or FoveationConfig(); frame=np.ascontiguousarray(frame_rgb,np.uint8); h,w=frame.shape[:2]; q=np.asarray(qmap,np.float32)
    if q.shape!=(h,w):raise ValueError('qmap shape mismatch')
    s0=float(np.clip(cfg.peripheral_scale,.03125,1)); s1=float(np.sqrt(s0)); s2=float(np.sqrt(s1)); levels=[_down_up(frame,s0),_down_up(frame,s1),_down_up(frame,s2),frame]; lv=np.clip(q,0,1)*3; base=np.floor(lv).astype(np.int8); frac=(lv-base)[...,None]; out=np.empty_like(frame,dtype=np.float32)
    for b in range(4):
        mask=base==b
        if np.any(mask):
            lo=levels[b].astype(np.float32); hi=levels[min(b+1,3)].astype(np.float32); blend=lo+(hi-lo)*frac; out[mask]=blend[mask]
    return np.clip(out,0,255).astype(np.uint8)

def context_and_roi_views(frame_rgb,rois:Sequence[Roi],*,context_scale=.25,roi_max_side=512):
    frame=np.asarray(frame_rgb,np.uint8); h,w=frame.shape[:2]; s=float(np.clip(context_scale,.02,1)); views=[_resize(frame,(max(1,round(w*s)),max(1,round(h*s))))]
    for r0 in rois:
        r=r0.clipped(); x0,y0=int(np.floor(r.x*w)),int(np.floor(r.y*h)); x1,y1=int(np.ceil((r.x+r.w)*w)),int(np.ceil((r.y+r.h)*h)); crop=frame[max(0,y0):min(h,y1),max(0,x0):min(w,x1)]
        if not crop.size:continue
        ch,cw=crop.shape[:2]; rs=min(1,max(1,roi_max_side)/max(ch,cw)); views.append(_resize(crop,(max(1,round(cw*rs)),max(1,round(ch*rs)))) if rs<1 else np.ascontiguousarray(crop))
    return views

def qp_delta_map(qmap,*,block=16,fovea_delta=-4,periphery_delta=12):
    q=np.asarray(qmap,np.float32); h,w=q.shape; b=max(1,int(block)); bh,bw=(h+b-1)//b,(w+b-1)//b; out=np.empty((bh,bw),np.int8)
    for by in range(bh):
        for bx in range(bw):
            tile=q[by*b:min((by+1)*b,h),bx*b:min((bx+1)*b,w)]; mean=float(tile.mean()) if tile.size else 0; out[by,bx]=np.int8(np.clip(round(periphery_delta+mean*(fovea_delta-periphery_delta)),-128,127))
    return out

def depth_focus_map(depth,*,focus_xy=(.5,.5),relative_tolerance=.08,softness=2.0):
    d=np.asarray(depth,np.float32); h,w=d.shape; x=int(np.clip(round(focus_xy[0]*(w-1)),0,w-1)); y=int(np.clip(round(focus_xy[1]*(h-1)),0,h-1)); target=float(d[y,x]); scale=max(abs(target)*max(relative_tolerance,1e-6),1e-6); return np.exp(-np.power(np.abs(d-target)/scale,max(softness,.1))).astype(np.float32)

def pixel_budget(views):return int(sum(v.shape[0]*v.shape[1] for v in views))

# Provider-agnostic streaming/reference API. Imported last because streaming.py builds on the
# spatial primitives above; this keeps the core functions usable without the video extra.
from .streaming import (
    AdaptiveScheduler,
    CallbackSink,
    ClassAgnosticMotionRoiDetector,
    FoveaStreamRuntime,
    InnovationSignals,
    MotionDetectorConfig,
    MultiRoiTracker,
    ProcessResult,
    RoiProposal,
    RoiTrack,
    RoiTrackerConfig,
    SchedulerConfig,
    SendDecision,
    StreamRuntimeConfig,
    StreamSink,
    roi_iou,
    run_stream,
)

# Drop-in frame middleware. This sits one level above StreamRuntime and lets existing camera or
# callback pipelines insert FoveaStream without adopting a provider-specific transport API.
from .middleware import (
    CallbackFrameSink,
    FramePacket,
    FrameSink,
    FrameTransform,
    FoveaStreamTransform,
    InlinePipeline,
    OptimizedFrame,
    PipelineStats,
    RealtimeBridge,
    transform_source,
)

# Logical multi-resolution tile planning. This is separate from codec-native block QP maps:
# applications may request exactly N transport tiles and customize quality/scale/QP independently.
from .tiles import (
    TileAggregation,
    TileCurve,
    TileDecision,
    TileDegradationFn,
    TilePlan,
    TilePlanner,
    TilePlannerConfig,
    TilePolicyContext,
    TileQualityFn,
    TileResolutionFn,
    TileQpFn,
    apply_tile_plan,
    plan_tiles,
    rasterize_tile_plan,
    render_tile_plan,
)

# Higher-level adaptive transport actuators built on the same relevance state/runtime.
from .optimization import (
    AdaptiveBudgetConfig,
    AdaptiveBudgetController,
    AdaptiveBudgetState,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    AtlasPlacement,
    BackgroundTileCache,
    BackgroundTileCacheConfig,
    BackgroundTileUpdate,
    EncoderSpatialHints,
    EvidenceBus,
    EvidenceRecord,
    LatencyBudget,
    LayeredPayload,
    LowResProposalAdapter,
    RelevanceSnapshot,
    RoiAtlas,
    RoiEnhancement,
    TemporalRoiCache,
    TemporalRoiCacheConfig,
    TransportResult,
    pack_roi_atlas,
    relevance_map_to_proposals,
)
