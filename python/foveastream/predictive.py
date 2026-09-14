from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import exp, sqrt, sin, cos, radians, tan
import numpy as np

class MotionMode(str, Enum):
    FIXATION = 'fixation'
    PURSUIT = 'pursuit'
    SACCADE = 'saccade'
    LOST = 'lost'

@dataclass(slots=True)
class PredictionConfig:
    process_noise_fixation: float = 2e-5
    process_noise_pursuit: float = 2.5e-4
    process_noise_saccade: float = 4e-3
    process_noise_lost: float = 1e-2
    measurement_noise: float = 6e-4
    confidence_decay_per_s: float = 0.30
    saccade_speed_threshold: float = 1.4
    fixation_speed_threshold: float = 0.05
    lost_confidence_threshold: float = 0.12
    max_horizon_s: float = 1.5

@dataclass(slots=True)
class AttentionMeasurement:
    x: float
    y: float
    confidence: float = 1.0
    variance: float = 1e-4
    timestamp_s: float = 0.0

@dataclass(slots=True)
class AttentionState:
    x: float = 0.5
    y: float = 0.5
    vx: float = 0.0
    vy: float = 0.0
    log_scale: float = 0.0
    log_scale_velocity: float = 0.0
    var_x: float = 0.01
    var_y: float = 0.01
    cov_xy: float = 0.0
    confidence: float = 0.0
    mode: MotionMode = MotionMode.LOST
    age_s: float = 0.0

class PredictiveAttentionFilter:
    def __init__(self, config: PredictionConfig | None = None):
        self.cfg = config or PredictionConfig()
        self.state = AttentionState()
        self.last_timestamp_s: float | None = None

    def _q(self) -> float:
        return {
            MotionMode.FIXATION: self.cfg.process_noise_fixation,
            MotionMode.PURSUIT: self.cfg.process_noise_pursuit,
            MotionMode.SACCADE: self.cfg.process_noise_saccade,
            MotionMode.LOST: self.cfg.process_noise_lost,
        }[self.state.mode]

    def _mode(self):
        s = self.state
        if s.confidence < self.cfg.lost_confidence_threshold:
            s.mode = MotionMode.LOST
            return
        speed = float(np.hypot(s.vx, s.vy))
        if speed >= self.cfg.saccade_speed_threshold:
            s.mode = MotionMode.SACCADE
        elif speed <= self.cfg.fixation_speed_threshold:
            s.mode = MotionMode.FIXATION
        else:
            s.mode = MotionMode.PURSUIT

    def predict_by(self, dt_s: float) -> AttentionState:
        dt = float(np.clip(dt_s, 0.0, self.cfg.max_horizon_s))
        s = self.state
        s.x = float(np.clip(s.x + s.vx * dt, 0, 1))
        s.y = float(np.clip(s.y + s.vy * dt, 0, 1))
        s.log_scale += s.log_scale_velocity * dt
        q = self._q() * (1 + dt * dt)
        s.var_x = min(.25, s.var_x + q)
        s.var_y = min(.25, s.var_y + q)
        s.confidence = float(np.clip(s.confidence - self.cfg.confidence_decay_per_s * dt, 0, 1))
        s.age_s += dt
        self._mode()
        return s

    def predict_to(self, timestamp_s: float) -> AttentionState:
        if self.last_timestamp_s is not None:
            self.predict_by(max(0.0, timestamp_s - self.last_timestamp_s))
        self.last_timestamp_s = timestamp_s
        return self.state

    def correct(self, m: AttentionMeasurement) -> AttentionState:
        self.predict_to(m.timestamp_s)
        s = self.state
        zx, zy = np.clip([m.x, m.y], 0, 1)
        r = (self.cfg.measurement_noise + max(m.variance, 1e-8)) / np.clip(m.confidence, .05, 1)
        kx, ky = s.var_x / (s.var_x + r), s.var_y / (s.var_y + r)
        ex, ey = float(zx - s.x), float(zy - s.y)
        dt = max(s.age_s, 1 / 240)
        s.x = float(np.clip(s.x + kx * ex, 0, 1)); s.y = float(np.clip(s.y + ky * ey, 0, 1))
        beta = .22 * float(np.clip(m.confidence, 0, 1))
        s.vx += beta * ex / dt; s.vy += beta * ey / dt
        s.var_x = max(1e-8, (1-kx)*s.var_x); s.var_y = max(1e-8, (1-ky)*s.var_y)
        s.confidence = float(np.clip(.65*s.confidence + .35*m.confidence, 0, 1)); s.age_s = 0
        self._mode(); return s

    def apply_flow(self, dx_norm: float, dy_norm: float, dt_s: float, confidence: float = 1.0):
        c = float(np.clip(confidence, 0, 1)); dt = max(dt_s, 1e-4)
        self.state.vx = (1-c)*self.state.vx + c*dx_norm/dt
        self.state.vy = (1-c)*self.state.vy + c*dy_norm/dt
        self.state.var_x *= 1-.35*c; self.state.var_y *= 1-.35*c
        self._mode()

    def apply_gyro(self, yaw_rad_s: float, pitch_rad_s: float, dt_s: float, *, hfov_deg=70.0, vfov_deg=50.0):
        # normalized pinhole ray; inverse camera rotation for world-fixed target
        tx, ty = tan(radians(hfov_deg)/2), tan(radians(vfov_deg)/2)
        x = (2*self.state.x-1)*tx; y = (2*self.state.y-1)*ty; z = 1.0
        yaw, pitch = yaw_rad_s*dt_s, pitch_rad_s*dt_s
        sy, cy, sp, cp = sin(yaw), cos(yaw), sin(pitch), cos(pitch)
        x, z = cy*x - sy*z, sy*x + cy*z
        y, z = cp*y + sp*z, -sp*y + cp*z
        z = max(z, 1e-4)
        self.state.x = float(np.clip(.5*(x/z/tx+1), 0, 1)); self.state.y = float(np.clip(.5*(y/z/ty+1), 0, 1))

    def future(self, horizon_s: float) -> AttentionState:
        h = float(np.clip(horizon_s, 0, self.cfg.max_horizon_s)); s = self.state
        q = self._q() * (1+h*h)
        return AttentionState(
            x=float(np.clip(s.x+s.vx*h,0,1)), y=float(np.clip(s.y+s.vy*h,0,1)),
            vx=s.vx, vy=s.vy, log_scale=s.log_scale+s.log_scale_velocity*h,
            log_scale_velocity=s.log_scale_velocity, var_x=min(.25,s.var_x+q), var_y=min(.25,s.var_y+q),
            cov_xy=s.cov_xy, confidence=s.confidence, mode=s.mode, age_s=s.age_s+h)

    def roi(self, base_w=.2, base_h=.2, sigma=2.5, horizon_s=.15):
        s = self.future(horizon_s); scale=exp(s.log_scale)
        hw=.5*base_w*scale+max(0,sigma)*sqrt(s.var_x); hh=.5*base_h*scale+max(0,sigma)*sqrt(s.var_y)
        x0,y0=max(0,s.x-hw),max(0,s.y-hh); x1,y1=min(1,s.x+hw),min(1,s.y+hh)
        from . import Roi
        return Roi(x0,y0,x1-x0,y1-y0,max(.05,s.confidence))

@dataclass(slots=True)
class SchedulerConfig:
    threshold: float=.45; max_refresh_interval_s: float=1.0; min_interval_s: float=.08; uncertainty_hard_limit: float=.18
    pose_weight: float=.14; roi_weight: float=.24; flow_weight: float=.14; scene_weight: float=.18; semantic_weight: float=.20; age_weight: float=.10

class AdaptiveScheduler:
    def __init__(self,cfg:SchedulerConfig|None=None): self.cfg=cfg or SchedulerConfig(); self.since=float('inf')
    def step(self,dt_s,*,pose=0,roi_error=0,residual_motion=0,scene_change=0,semantic_uncertainty=0,uncertainty_radius=0,hard_trigger=False):
        c=self.cfg; self.since=min(1e6,self.since+max(0,dt_s)); age=np.clip(self.since/max(c.max_refresh_interval_s,1e-6),0,1)
        score=float(c.pose_weight*np.clip(pose,0,1)+c.roi_weight*np.clip(roi_error,0,1)+c.flow_weight*np.clip(residual_motion,0,1)+c.scene_weight*np.clip(scene_change,0,1)+c.semantic_weight*np.clip(semantic_uncertainty,0,1)+c.age_weight*age)
        if hard_trigger: send,reason=True,'hard_trigger'
        elif self.since>=c.max_refresh_interval_s: send,reason=True,'max_staleness'
        elif uncertainty_radius>=c.uncertainty_hard_limit: send,reason=True,'uncertainty_limit'
        elif score>=c.threshold: send,reason=True,'innovation'
        else: send,reason=False,'below_threshold'
        if send and not hard_trigger and self.since<c.min_interval_s: send,reason=False,'min_interval'
        if send:self.since=0
        return {'send':send,'score':score,'reason':reason}
