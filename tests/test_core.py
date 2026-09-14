import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from foveastream import FoveationConfig,FocusCandidate,FocusTracker,Roi,context_and_roi_views,foveate,qp_delta_map,quality_map

def test_quality_center_and_edge():
    q=quality_map((101,101,3),points=[(.5,.5)]); assert q[50,50]>.99; assert q[0,0]<.05

def test_roi_is_preserved():
    frame=np.random.default_rng(1).integers(0,255,(240,320,3),dtype=np.uint8); roi=Roi(.4,.35,.2,.3); cfg=FoveationConfig(peripheral_scale=.125); q=quality_map(frame.shape,rois=[roi],config=cfg); out=foveate(frame,q,cfg); ys=slice(int(.35*240),int(.65*240)); xs=slice(int(.4*320),int(.6*320)); assert np.array_equal(out[ys,xs],frame[ys,xs])

def test_context_roi_reduces_pixel_budget():
    frame=np.zeros((1080,1920,3),np.uint8); views=context_and_roi_views(frame,[Roi(.4,.4,.2,.2)],context_scale=.25,roi_max_side=384); assert sum(v.shape[0]*v.shape[1] for v in views)<frame.shape[0]*frame.shape[1]

def test_qp_map_shape():
    q=quality_map((64,64,3),points=[(.5,.5)]); m=qp_delta_map(q,block=16); assert m.shape==(4,4)

def test_focus_fusion():
    p=FocusTracker.fuse([FocusCandidate(0,.5,1,1),FocusCandidate(1,.5,1,3)]); assert p is not None and abs(p[0]-.75)<1e-5
