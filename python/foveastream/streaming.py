from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence
import math
import numpy as np

from . import FoveationConfig, Roi, context_and_roi_views, foveate, qp_delta_map, quality_map

try:
    import cv2
except Exception:  # pragma: no cover - video is an optional extra
    cv2 = None


@dataclass(slots=True)
class RoiProposal:
    """Class-agnostic evidence that a normalized rectangle deserves more fidelity."""
    roi: Roi
    confidence: float = 1.0
    priority: float = 1.0
    source: str = "external"
    track_hint: int | None = None

    @property
    def score(self) -> float:
        return float(np.clip(self.confidence, 0, 1)) * max(0.0, float(self.priority))


@dataclass(slots=True)
class RoiTrackerConfig:
    max_tracks: int = 8
    association_iou: float = 0.12
    association_center_distance: float = 0.18
    smoothing: float = 0.55
    velocity_smoothing: float = 0.35
    max_stale_s: float = 0.75
    confidence_decay_per_s: float = 0.45
    uncertainty_growth_per_s: float = 0.08
    prediction_horizon_s: float = 0.12
    min_confidence: float = 0.05
    proposal_merge_iou: float = 0.55
    pixel_budget_fraction: float = 0.30


@dataclass(slots=True)
class RoiTrack:
    id: int
    roi: Roi
    velocity: np.ndarray
    confidence: float
    priority: float
    source: str
    age_s: float = 0.0
    stale_s: float = 0.0

    @property
    def score(self) -> float:
        return float(np.clip(self.confidence, 0, 1)) * max(0.0, self.priority)


def roi_iou(a: Roi, b: Roi) -> float:
    a, b = a.clipped(), b.clipped()
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 1e-12 else 0.0


def _center_distance(a: Roi, b: Roi) -> float:
    a, b = a.clipped(), b.clipped()
    return math.hypot((a.x + a.w / 2) - (b.x + b.w / 2), (a.y + a.h / 2) - (b.y + b.h / 2))


class MultiRoiTracker:
    """Maintains multiple independent ROI tracks without assuming semantic classes."""

    def __init__(self, config: RoiTrackerConfig | None = None):
        self.config = config or RoiTrackerConfig()
        self.tracks: list[RoiTrack] = []
        self._next_id = 1

    def reset(self) -> None:
        self.tracks.clear()
        self._next_id = 1

    def _dedupe(self, proposals: Sequence[RoiProposal]) -> list[RoiProposal]:
        kept: list[RoiProposal] = []
        for p in sorted(proposals, key=lambda x: x.score, reverse=True):
            for i, q in enumerate(kept):
                if roi_iou(p.roi, q.roi) >= np.clip(self.config.proposal_merge_iou, 0, 1):
                    if p.score > q.score:
                        kept[i] = p
                    break
            else:
                kept.append(p)
        return kept

    def _predict(self, dt: float) -> None:
        cfg = self.config
        for t in self.tracks:
            t.age_s += dt
            t.stale_s += dt
            r = t.roi.clipped()
            v = t.velocity
            t.roi = Roi(
                r.x + float(v[0]) * dt,
                r.y + float(v[1]) * dt,
                max(0.005, r.w + float(v[2]) * dt),
                max(0.005, r.h + float(v[3]) * dt),
                t.confidence,
            ).clipped()
            t.confidence = float(np.clip(t.confidence - max(0.0, cfg.confidence_decay_per_s) * dt, 0, 1))
            t.roi.confidence = t.confidence

    def _correct(self, track: RoiTrack, proposal: RoiProposal, dt: float) -> None:
        cfg = self.config
        old, z = track.roi.clipped(), proposal.roi.clipped()
        obs = np.array([(z.x-old.x)/dt, (z.y-old.y)/dt, (z.w-old.w)/dt, (z.h-old.h)/dt], np.float32)
        beta = float(np.clip(cfg.velocity_smoothing, 0, 1))
        track.velocity = track.velocity * (1-beta) + obs * beta
        alpha = float(np.clip(cfg.smoothing, 0, 1))
        blend = lambda a, b: a * (1-alpha) + b * alpha
        track.roi = Roi(blend(old.x,z.x), blend(old.y,z.y), blend(old.w,z.w), blend(old.h,z.h), proposal.confidence).clipped()
        track.confidence = float(np.clip(.35 * track.confidence + .65 * proposal.confidence, 0, 1))
        track.roi.confidence = track.confidence
        track.priority = max(track.priority, float(proposal.priority))
        track.source = proposal.source
        track.stale_s = 0.0

    def update(self, proposals: Sequence[RoiProposal] = (), dt_s: float = 0.0) -> list[Roi]:
        cfg = self.config
        dt = float(np.clip(dt_s, 0, 1))
        self._predict(dt)
        used: set[int] = set()

        for p in self._dedupe(proposals):
            r = p.roi.clipped()
            if r.w <= 0 or r.h <= 0 or p.confidence <= 0:
                continue
            best_idx, best_score = None, -1e9
            for i, track in enumerate(self.tracks):
                if i in used:
                    continue
                if p.track_hint is not None and p.track_hint == track.id:
                    best_idx, best_score = i, 10.0
                    break
                overlap = roi_iou(track.roi, r)
                dist = _center_distance(track.roi, r)
                if overlap < cfg.association_iou and dist > cfg.association_center_distance:
                    continue
                compatibility = overlap * 2 + (1-min(1.0, dist)) + .25 * p.score
                if compatibility > best_score:
                    best_idx, best_score = i, compatibility
            if best_idx is not None:
                self._correct(self.tracks[best_idx], p, max(dt, 1e-4))
                used.add(best_idx)
            elif len(self.tracks) < max(1, cfg.max_tracks):
                self.tracks.append(RoiTrack(
                    id=self._next_id,
                    roi=Roi(r.x, r.y, r.w, r.h, p.confidence).clipped(),
                    velocity=np.zeros(4, np.float32),
                    confidence=float(np.clip(p.confidence, 0, 1)),
                    priority=max(0.0, float(p.priority)),
                    source=p.source,
                ))
                used.add(len(self.tracks)-1)
                self._next_id += 1

        self.tracks = [t for t in self.tracks if t.stale_s <= cfg.max_stale_s and t.confidence >= cfg.min_confidence]
        self.tracks.sort(key=lambda t: t.score, reverse=True)
        del self.tracks[max(1, cfg.max_tracks):]
        return self.active_rois()

    def active_rois(self) -> list[Roi]:
        cfg = self.config
        area = 0.0
        out: list[Roi] = []
        for t in sorted(self.tracks, key=lambda x: x.score, reverse=True)[:max(1, cfg.max_tracks)]:
            r = t.roi.clipped(); h = max(0.0, cfg.prediction_horizon_s)
            speed = math.hypot(float(t.velocity[0]), float(t.velocity[1]))
            margin = max(0.0, cfg.uncertainty_growth_per_s) * t.stale_s + speed * h * .35
            pred = Roi(
                r.x + float(t.velocity[0])*h - margin,
                r.y + float(t.velocity[1])*h - margin,
                max(.005, r.w + float(t.velocity[2])*h) + 2*margin,
                max(.005, r.h + float(t.velocity[3])*h) + 2*margin,
                t.confidence,
            ).clipped()
            a = pred.w * pred.h
            if out and cfg.pixel_budget_fraction > 0 and area + a > cfg.pixel_budget_fraction:
                continue
            out.append(pred); area += a
        return out


@dataclass(slots=True)
class MotionDetectorConfig:
    analysis_width: int = 256
    max_proposals: int = 8
    min_area_ratio: float = 0.0002
    max_area_ratio: float = 0.25
    padding: float = 0.35
    mad_multiplier: float = 4.0
    absolute_threshold: float = 0.35


class ClassAgnosticMotionRoiDetector:
    """Cheap proposal source: compensates camera motion, then returns multiple residual-motion regions."""

    def __init__(self, config: MotionDetectorConfig | None = None):
        if cv2 is None:
            raise RuntimeError("ClassAgnosticMotionRoiDetector requires opencv-python; install foveastream[video]")
        self.config = config or MotionDetectorConfig()
        self._prev: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def propose(self, frame_rgb: np.ndarray) -> list[RoiProposal]:
        h0, w0 = frame_rgb.shape[:2]
        w = max(32, int(self.config.analysis_width)); h = max(1, round(h0*w/w0))
        gray = cv2.cvtColor(cv2.resize(frame_rgb, (w,h), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        prev = self._prev; self._prev = gray
        if prev is None:
            return []

        p0 = cv2.goodFeaturesToTrack(prev, maxCorners=220, qualityLevel=.015, minDistance=7, blockSize=5)
        transform = None
        if p0 is not None and len(p0) >= 6:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, gray, p0, None, winSize=(17,17), maxLevel=2,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,15,.03))
            if p1 is not None:
                a = p0[st.ravel()==1].reshape(-1,2); b = p1[st.ravel()==1].reshape(-1,2)
                if len(a) >= 6:
                    transform, _ = cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.0,maxIters=300)
        aligned = cv2.warpAffine(prev, transform, (w,h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT) if transform is not None else prev
        mag = cv2.GaussianBlur(cv2.absdiff(aligned,gray).astype(np.float32)/255.0, (5,5), 0) * 4.0
        med = float(np.median(mag)); mad = float(np.median(np.abs(mag-med))) + 1e-6
        threshold = max(med + self.config.mad_multiplier*mad, self.config.absolute_threshold)
        mask = (mag > threshold).astype(np.uint8)*255
        mask = cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        mask = cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask,8)
        proposals: list[RoiProposal] = []
        frame_area = h*w
        for i in range(1,n):
            x,y,bw,bh,area = stats[i]
            ratio = area/max(1,frame_area)
            if ratio < self.config.min_area_ratio or ratio > self.config.max_area_ratio:
                continue
            pad_x, pad_y = bw*self.config.padding, bh*self.config.padding
            rx, ry = (x-pad_x)/w, (y-pad_y)/h
            rw, rh = (bw+2*pad_x)/w, (bh+2*pad_y)/h
            strength = float(np.mean(mag[labels==i]))
            compactness = area/max(1,bw*bh)
            confidence = float(np.clip(.18 + .38*min(strength,1.5) + .24*compactness + .20*min(1,ratio*30),0,1))
            proposals.append(RoiProposal(Roi(rx,ry,rw,rh,confidence).clipped(), confidence, source="residual-motion"))
        proposals.sort(key=lambda p: p.score, reverse=True)
        return proposals[:max(1,self.config.max_proposals)]


@dataclass(slots=True)
class SchedulerConfig:
    threshold: float = 0.45
    max_refresh_interval_s: float = 1.0
    min_interval_s: float = 0.08
    uncertainty_hard_limit: float = 0.18
    pose_weight: float = 0.14
    roi_weight: float = 0.24
    flow_weight: float = 0.14
    scene_weight: float = 0.18
    semantic_weight: float = 0.20
    age_weight: float = 0.10


@dataclass(slots=True)
class InnovationSignals:
    pose_novelty: float = 0.0
    roi_prediction_error: float = 0.0
    residual_motion: float = 0.0
    scene_change: float = 0.0
    semantic_uncertainty: float = 0.0
    uncertainty_radius: float = 0.0
    hard_trigger: bool = False


@dataclass(slots=True)
class SendDecision:
    send: bool
    score: float
    reason: str


class AdaptiveScheduler:
    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig(); self.time_since_send_s = float("inf")

    def reset(self) -> None:
        self.time_since_send_s = float("inf")

    def step(self, dt_s: float, s: InnovationSignals) -> SendDecision:
        c = self.config
        self.time_since_send_s = min(1e6, self.time_since_send_s + max(0.0, dt_s))
        age = np.clip(self.time_since_send_s/max(c.max_refresh_interval_s,1e-6),0,1)
        score = (c.pose_weight*np.clip(s.pose_novelty,0,1) + c.roi_weight*np.clip(s.roi_prediction_error,0,1)
            + c.flow_weight*np.clip(s.residual_motion,0,1) + c.scene_weight*np.clip(s.scene_change,0,1)
            + c.semantic_weight*np.clip(s.semantic_uncertainty,0,1) + c.age_weight*age)
        if s.hard_trigger: send,reason=True,"hard-trigger"
        elif self.time_since_send_s >= c.max_refresh_interval_s: send,reason=True,"max-staleness"
        elif s.uncertainty_radius >= c.uncertainty_hard_limit: send,reason=True,"uncertainty-limit"
        elif score >= c.threshold: send,reason=True,"innovation"
        else: send,reason=False,"below-threshold"
        if send and not s.hard_trigger and self.time_since_send_s < c.min_interval_s:
            send,reason=False,"suppressed-min-interval"
        if send: self.time_since_send_s = 0.0
        return SendDecision(bool(send),float(score),reason)


@dataclass(slots=True)
class StreamRuntimeConfig:
    foveation: FoveationConfig = field(default_factory=FoveationConfig)
    tracker: RoiTrackerConfig = field(default_factory=RoiTrackerConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    context_scale: float = 0.25
    roi_max_side: int = 512
    qp_block: int = 16
    fovea_qp_delta: int = -4
    periphery_qp_delta: int = 12
    produce_foveated_frame: bool = True
    produce_context_roi_views: bool = True
    produce_qp_map: bool = True
    auto_motion_proposals: bool = False


@dataclass(slots=True)
class ProcessResult:
    timestamp_s: float
    decision: SendDecision
    rois: list[Roi]
    tracks: list[RoiTrack]
    quality_map: np.ndarray
    foveated_frame: np.ndarray | None
    views: list[np.ndarray]
    qp_map: np.ndarray | None

    @property
    def context(self) -> np.ndarray | None:
        return self.views[0] if self.views else None

    @property
    def roi_views(self) -> list[np.ndarray]:
        return self.views[1:] if len(self.views)>1 else []


class StreamSink(Protocol):
    def consume(self, result: ProcessResult) -> None: ...


class CallbackSink:
    def __init__(self, callback: Callable[[ProcessResult], None], *, only_when_send: bool = False):
        self.callback = callback; self.only_when_send = only_when_send
    def consume(self, result: ProcessResult) -> None:
        if not self.only_when_send or result.decision.send:
            self.callback(result)


class FoveaStreamRuntime:
    """Transport/provider-agnostic push-frame runtime.

    The caller owns capture and transport. Feed frames and arbitrary ROI proposals in; consume
    ProcessResult or attach any sink that understands the target workflow/provider.
    """

    def __init__(self, config: StreamRuntimeConfig | None = None):
        self.config = config or StreamRuntimeConfig()
        self.tracker = MultiRoiTracker(self.config.tracker)
        self.scheduler = AdaptiveScheduler(self.config.scheduler)
        self.motion = ClassAgnosticMotionRoiDetector() if self.config.auto_motion_proposals else None
        self._last_timestamp_s: float | None = None

    def reset(self) -> None:
        self.tracker.reset(); self.scheduler.reset(); self._last_timestamp_s = None
        if self.motion is not None: self.motion.reset()

    def process(
        self,
        frame_rgb: np.ndarray,
        timestamp_s: float,
        *,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float,float]] = (),
        signals: InnovationSignals | None = None,
    ) -> ProcessResult:
        frame = np.ascontiguousarray(frame_rgb, dtype=np.uint8)
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame_rgb must be HxWx3 uint8 RGB")
        timestamp_s = float(timestamp_s)
        if not np.isfinite(timestamp_s): raise ValueError("timestamp_s must be finite")
        if self._last_timestamp_s is not None and timestamp_s + 1e-9 < self._last_timestamp_s:
            raise ValueError("timestamps must be monotonic")
        dt = 0.0 if self._last_timestamp_s is None else max(0.0, timestamp_s-self._last_timestamp_s)
        self._last_timestamp_s = timestamp_s

        all_proposals = list(proposals)
        if self.motion is not None:
            all_proposals.extend(self.motion.propose(frame))
        rois = self.tracker.update(all_proposals, dt)
        q = quality_map(frame.shape, points=points, rois=rois, config=self.config.foveation)

        sig = signals or InnovationSignals()
        if sig.uncertainty_radius <= 0 and self.tracker.tracks:
            sig.uncertainty_radius = max(t.stale_s*self.config.tracker.uncertainty_growth_per_s for t in self.tracker.tracks)
        if sig.semantic_uncertainty <= 0 and self.tracker.tracks:
            sig.semantic_uncertainty = float(np.clip(1-np.mean([t.confidence for t in self.tracker.tracks]),0,1))
        decision = self.scheduler.step(dt,sig)

        fov = foveate(frame,q,self.config.foveation) if self.config.produce_foveated_frame else None
        views = context_and_roi_views(frame,rois,context_scale=self.config.context_scale,roi_max_side=self.config.roi_max_side) if self.config.produce_context_roi_views else []
        qp = qp_delta_map(q,block=max(1,self.config.qp_block),fovea_delta=self.config.fovea_qp_delta,periphery_delta=self.config.periphery_qp_delta) if self.config.produce_qp_map else None
        return ProcessResult(timestamp_s,decision,rois,list(self.tracker.tracks),q,fov,views,qp)

    def push(self, frame_rgb: np.ndarray, timestamp_s: float, sink: StreamSink, **kwargs) -> ProcessResult:
        result = self.process(frame_rgb,timestamp_s,**kwargs); sink.consume(result); return result


def run_stream(
    frames: Iterable[tuple[float,np.ndarray]],
    runtime: FoveaStreamRuntime,
    sink: StreamSink,
) -> None:
    """Generic pull-source bridge. Provider/camera adapters only need to yield (timestamp_s, RGB frame)."""
    for timestamp_s, frame_rgb in frames:
        runtime.push(frame_rgb,timestamp_s,sink)
