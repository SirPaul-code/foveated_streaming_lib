from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable, Literal, Mapping, Sequence

import numpy as np

from . import Roi
from .streaming import (
    FoveaStreamRuntime,
    InnovationSignals,
    ProcessResult,
    RoiProposal,
    RoiTrack,
    StreamRuntimeConfig,
    roi_iou,
)

try:
    import cv2
except Exception:  # pragma: no cover - video extra is optional
    cv2 = None

FusionMode = Literal["max", "noisy_or", "add"]


def _resize_image(image: np.ndarray, width: int, height: int) -> np.ndarray:
    width, height = max(1, int(width)), max(1, int(height))
    if image.shape[1] == width and image.shape[0] == height:
        return np.ascontiguousarray(image)
    if cv2 is not None:
        interpolation = cv2.INTER_AREA if width < image.shape[1] or height < image.shape[0] else cv2.INTER_LINEAR
        return cv2.resize(image, (width, height), interpolation=interpolation)
    ys = np.round(np.linspace(0, image.shape[0] - 1, height)).astype(int)
    xs = np.round(np.linspace(0, image.shape[1] - 1, width)).astype(int)
    return np.ascontiguousarray(image[np.ix_(ys, xs)])


def _crop_roi(frame: np.ndarray, roi: Roi) -> np.ndarray:
    r = roi.clipped()
    h, w = frame.shape[:2]
    x0 = int(np.floor(r.x * w)); y0 = int(np.floor(r.y * h))
    x1 = int(np.ceil((r.x + r.w) * w)); y1 = int(np.ceil((r.y + r.h) * h))
    return np.ascontiguousarray(frame[max(0, y0):min(h, y1), max(0, x0):min(w, x1)])


def _thumbnail(image: np.ndarray, side: int) -> np.ndarray:
    if image.size == 0:
        return np.zeros((max(1, side), max(1, side)), np.float32)
    small = _resize_image(image, max(1, side), max(1, side))
    if small.ndim == 3:
        if cv2 is not None:
            gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        else:
            gray = small.astype(np.float32).mean(axis=2)
    else:
        gray = small
    return np.asarray(gray, np.float32) / 255.0


def _normalized_area(rois: Sequence[Roi]) -> float:
    return float(sum(r.clipped().w * r.clipped().h for r in rois))


@dataclass(slots=True)
class LatencyBudget:
    """Latency horizon used to predict relevance where it will matter downstream."""

    capture_s: float = 0.0
    analysis_s: float = 0.0
    encode_s: float = 0.0
    network_s: float = 0.0
    decode_s: float = 0.0
    consumer_s: float = 0.0
    safety_s: float = 0.02
    max_horizon_s: float = 0.50

    @property
    def total_s(self) -> float:
        total = (
            self.capture_s + self.analysis_s + self.encode_s + self.network_s +
            self.decode_s + self.consumer_s + self.safety_s
        )
        return float(np.clip(total, 0.0, max(0.0, self.max_horizon_s)))

    def apply(self, runtime: FoveaStreamRuntime) -> float:
        runtime.config.tracker.prediction_horizon_s = self.total_s
        runtime.tracker.config.prediction_horizon_s = self.total_s
        return self.total_s


@dataclass(slots=True)
class EvidenceRecord:
    source: str
    timestamp_s: float
    ttl_s: float
    weight: float = 1.0
    proposals: tuple[RoiProposal, ...] = ()
    points: tuple[tuple[float, float], ...] = ()
    quality_map: np.ndarray | None = None


@dataclass(slots=True)
class RelevanceSnapshot:
    timestamp_s: float
    proposals: list[RoiProposal]
    points: list[tuple[float, float]]
    quality_map: np.ndarray | None
    active_sources: tuple[str, ...]


class EvidenceBus:
    """Timestamped multi-source relevance fusion with TTL and source weights.

    The bus deliberately understands only spatial relevance, not semantic classes. A source may be
    motion, gaze, OCR, a detector, an AR anchor, a user selection, a task planner, etc.
    """

    def __init__(self, *, fusion: FusionMode = "max"):
        if fusion not in ("max", "noisy_or", "add"):
            raise ValueError("fusion must be max, noisy_or or add")
        self.fusion: FusionMode = fusion
        self._records: dict[str, EvidenceRecord] = {}

    def clear(self, source: str | None = None) -> None:
        if source is None:
            self._records.clear()
        else:
            self._records.pop(source, None)

    def publish(
        self,
        source: str,
        timestamp_s: float,
        *,
        ttl_s: float = 0.25,
        weight: float = 1.0,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float, float]] = (),
        quality_map: np.ndarray | None = None,
    ) -> None:
        if not source:
            raise ValueError("source must be non-empty")
        timestamp_s = float(timestamp_s)
        if not np.isfinite(timestamp_s):
            raise ValueError("timestamp_s must be finite")
        q = None if quality_map is None else np.ascontiguousarray(quality_map, np.float32)
        self._records[source] = EvidenceRecord(
            source=source,
            timestamp_s=timestamp_s,
            ttl_s=max(0.0, float(ttl_s)),
            weight=max(0.0, float(weight)),
            proposals=tuple(proposals),
            points=tuple(points),
            quality_map=q,
        )

    def snapshot(self, timestamp_s: float, frame_shape: Sequence[int]) -> RelevanceSnapshot:
        timestamp_s = float(timestamp_s)
        h, w = int(frame_shape[0]), int(frame_shape[1])
        proposals: list[RoiProposal] = []
        points: list[tuple[float, float]] = []
        maps: list[np.ndarray] = []
        sources: list[str] = []
        expired: list[str] = []

        for source, record in self._records.items():
            age = timestamp_s - record.timestamp_s
            if age < -1e-6:
                continue
            if age > record.ttl_s:
                expired.append(source)
                continue
            sources.append(source)
            for p in record.proposals:
                proposals.append(RoiProposal(
                    roi=p.roi,
                    confidence=p.confidence,
                    priority=p.priority * record.weight,
                    source=f"{source}:{p.source}",
                    track_hint=p.track_hint,
                ))
            points.extend(record.points)
            if record.quality_map is not None:
                q = record.quality_map
                if q.shape != (h, w):
                    q = _resize_image(q, w, h)
                maps.append(np.clip(np.asarray(q, np.float32) * record.weight, 0.0, 1.0))

        for source in expired:
            self._records.pop(source, None)

        fused: np.ndarray | None = None
        if maps:
            if self.fusion == "max":
                fused = np.maximum.reduce(maps)
            elif self.fusion == "noisy_or":
                inv = np.ones((h, w), np.float32)
                for q in maps:
                    inv *= 1.0 - q
                fused = 1.0 - inv
            else:
                fused = np.clip(np.sum(maps, axis=0), 0.0, 1.0)
            fused = np.ascontiguousarray(fused, np.float32)

        return RelevanceSnapshot(timestamp_s, proposals, points, fused, tuple(sources))


def relevance_map_to_proposals(
    qmap: np.ndarray,
    *,
    threshold: float = 0.65,
    max_proposals: int = 8,
    min_area_fraction: float = 0.0005,
    source: str = "relevance-map",
) -> list[RoiProposal]:
    """Convert a dense relevance field into a small class-agnostic ROI proposal set."""
    q = np.clip(np.asarray(qmap, np.float32), 0.0, 1.0)
    h0, w0 = q.shape[:2]
    analysis_w = min(256, w0)
    analysis_h = max(1, round(h0 * analysis_w / max(w0, 1)))
    small = _resize_image(q, analysis_w, analysis_h)
    mask = (small >= float(np.clip(threshold, 0.0, 1.0))).astype(np.uint8)
    out: list[RoiProposal] = []

    if cv2 is not None:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            frac = area / max(1, analysis_w * analysis_h)
            if frac < min_area_fraction:
                continue
            strength = float(np.mean(small[labels == i]))
            out.append(RoiProposal(
                Roi(x / analysis_w, y / analysis_h, bw / analysis_w, bh / analysis_h, strength),
                confidence=strength,
                priority=max(0.01, strength),
                source=source,
            ))
    else:
        ys, xs = np.where(mask > 0)
        if len(xs):
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            strength = float(np.mean(small[mask > 0]))
            out.append(RoiProposal(
                Roi(x0 / analysis_w, y0 / analysis_h, (x1-x0) / analysis_w, (y1-y0) / analysis_h, strength),
                confidence=strength,
                priority=max(0.01, strength),
                source=source,
            ))
    out.sort(key=lambda p: p.score, reverse=True)
    return out[:max(1, int(max_proposals))]


class LowResProposalAdapter:
    """Run an arbitrary proposal source on a cheap low-res frame while preserving full-res pixels."""

    def __init__(self, proposal_fn: Callable[[np.ndarray, float], Sequence[RoiProposal]], *, analysis_width: int = 256):
        self.proposal_fn = proposal_fn
        self.analysis_width = max(32, int(analysis_width))

    def propose(self, frame_rgb: np.ndarray, timestamp_s: float) -> list[RoiProposal]:
        h, w = frame_rgb.shape[:2]
        aw = min(self.analysis_width, w)
        ah = max(1, round(h * aw / max(w, 1)))
        low = _resize_image(frame_rgb, aw, ah)
        return list(self.proposal_fn(low, float(timestamp_s)))


@dataclass(slots=True)
class TemporalRoiCacheConfig:
    thumbnail_side: int = 24
    change_threshold: float = 0.055
    max_refresh_s: float = 1.5
    match_iou: float = 0.30


@dataclass(slots=True)
class RoiEnhancement:
    key: int
    roi: Roi
    changed: bool
    change_score: float
    age_s: float
    image: np.ndarray | None


@dataclass(slots=True)
class _CachedRoi:
    key: int
    roi: Roi
    thumbnail: np.ndarray
    last_sent_s: float


class TemporalRoiCache:
    """Avoid re-sending high-resolution ROI crops that have not materially changed."""

    def __init__(self, config: TemporalRoiCacheConfig | None = None):
        self.config = config or TemporalRoiCacheConfig()
        self._entries: dict[int, _CachedRoi] = {}
        self._next_key = 1_000_000

    def reset(self) -> None:
        self._entries.clear()
        self._next_key = 1_000_000

    def _key_for(self, roi: Roi, tracks: Sequence[RoiTrack], used: set[int]) -> int:
        best_key: int | None = None
        best_iou = 0.0
        for t in tracks:
            if t.id in used:
                continue
            overlap = roi_iou(roi, t.roi)
            if overlap > best_iou:
                best_iou, best_key = overlap, int(t.id)
        if best_key is not None and best_iou >= self.config.match_iou:
            return best_key
        for key, entry in self._entries.items():
            if key in used:
                continue
            overlap = roi_iou(roi, entry.roi)
            if overlap > best_iou:
                best_iou, best_key = overlap, key
        if best_key is not None and best_iou >= self.config.match_iou:
            return best_key
        key = self._next_key
        self._next_key += 1
        return key

    def update(
        self,
        frame_rgb: np.ndarray,
        timestamp_s: float,
        rois: Sequence[Roi],
        tracks: Sequence[RoiTrack] = (),
    ) -> list[RoiEnhancement]:
        now = float(timestamp_s)
        out: list[RoiEnhancement] = []
        used: set[int] = set()
        live: set[int] = set()
        for roi in rois:
            key = self._key_for(roi, tracks, used)
            used.add(key); live.add(key)
            crop = _crop_roi(frame_rgb, roi)
            thumb = _thumbnail(crop, self.config.thumbnail_side)
            entry = self._entries.get(key)
            if entry is None:
                changed, score, age = True, 1.0, float("inf")
            else:
                score = float(np.mean(np.abs(thumb - entry.thumbnail)))
                age = max(0.0, now - entry.last_sent_s)
                changed = score >= self.config.change_threshold or age >= self.config.max_refresh_s
            if changed:
                self._entries[key] = _CachedRoi(key, roi.clipped(), thumb, now)
                image: np.ndarray | None = crop
            else:
                entry.roi = roi.clipped()
                image = None
            out.append(RoiEnhancement(key, roi.clipped(), changed, score, age, image))

        # Drop cache entries that are no longer represented by an active ROI.
        for key in list(self._entries):
            if key not in live:
                self._entries.pop(key, None)
        return out


@dataclass(slots=True)
class BackgroundTileCacheConfig:
    columns: int = 8
    rows: int = 6
    thumbnail_side: int = 12
    change_threshold: float = 0.045
    max_refresh_s: float = 2.0


@dataclass(slots=True)
class BackgroundTileUpdate:
    key: int
    roi: Roi
    change_score: float
    image: np.ndarray


@dataclass(slots=True)
class _BackgroundEntry:
    thumbnail: np.ndarray
    last_sent_s: float


class BackgroundTileCache:
    """Tile-based scene cache for mostly static cameras/backgrounds.

    This is intentionally opt-in. A moving camera naturally invalidates many tiles; callers should
    disable it or compensate camera motion before using it in that case.
    """

    def __init__(self, config: BackgroundTileCacheConfig | None = None):
        self.config = config or BackgroundTileCacheConfig()
        self._entries: dict[int, _BackgroundEntry] = {}

    def reset(self) -> None:
        self._entries.clear()

    def update(self, frame_rgb: np.ndarray, timestamp_s: float) -> list[BackgroundTileUpdate]:
        c, r = max(1, self.config.columns), max(1, self.config.rows)
        now = float(timestamp_s)
        updates: list[BackgroundTileUpdate] = []
        for gy in range(r):
            for gx in range(c):
                roi = Roi(gx / c, gy / r, 1 / c, 1 / r)
                crop = _crop_roi(frame_rgb, roi)
                thumb = _thumbnail(crop, self.config.thumbnail_side)
                key = gy * c + gx
                entry = self._entries.get(key)
                if entry is None:
                    score, changed = 1.0, True
                else:
                    score = float(np.mean(np.abs(thumb - entry.thumbnail)))
                    changed = score >= self.config.change_threshold or now - entry.last_sent_s >= self.config.max_refresh_s
                if changed:
                    self._entries[key] = _BackgroundEntry(thumb, now)
                    updates.append(BackgroundTileUpdate(key, roi, score, crop))
        return updates


@dataclass(slots=True)
class AtlasPlacement:
    kind: str
    key: int | str
    x: int
    y: int
    width: int
    height: int
    source_roi: Roi | None = None


@dataclass(slots=True)
class RoiAtlas:
    image: np.ndarray
    placements: list[AtlasPlacement]


def pack_roi_atlas(
    context: np.ndarray,
    enhancements: Sequence[RoiEnhancement],
    *,
    max_width: int = 1024,
    max_height: int = 1024,
    padding: int = 4,
) -> RoiAtlas:
    """Pack global context + only changed ROI enhancements into one ordinary image."""
    items: list[tuple[str, int | str, np.ndarray, Roi | None]] = [("context", "context", context, None)]
    for e in enhancements:
        if e.changed and e.image is not None and e.image.size:
            items.append(("roi", e.key, e.image, e.roi))
    max_width, max_height = max(32, int(max_width)), max(32, int(max_height))
    padding = max(0, int(padding))

    scale = min(1.0, max_width / max(1, max(i[2].shape[1] for i in items)), max_height / max(1, max(i[2].shape[0] for i in items)))
    packed: list[tuple[str, int | str, np.ndarray, Roi | None, int, int]] | None = None
    used_h = 0
    for _ in range(24):
        x = padding; y = padding; row_h = 0; trial = []
        ok = True
        for kind, key, image, source_roi in items:
            iw = max(1, round(image.shape[1] * scale)); ih = max(1, round(image.shape[0] * scale))
            if x + iw + padding > max_width:
                x = padding; y += row_h + padding; row_h = 0
            if y + ih + padding > max_height:
                ok = False; break
            trial.append((kind, key, _resize_image(image, iw, ih), source_roi, x, y))
            x += iw + padding; row_h = max(row_h, ih)
        if ok:
            packed = trial; used_h = min(max_height, y + row_h + padding); break
        scale *= 0.85
    if packed is None:
        raise ValueError("atlas cannot fit even after scaling")

    atlas = np.zeros((max(1, used_h), max_width, 3), np.uint8)
    placements: list[AtlasPlacement] = []
    for kind, key, image, source_roi, x, y in packed:
        if image.ndim == 2:
            image = np.repeat(image[..., None], 3, axis=2)
        h, w = image.shape[:2]
        atlas[y:y+h, x:x+w] = image[..., :3]
        placements.append(AtlasPlacement(kind, key, x, y, w, h, source_roi))
    return RoiAtlas(atlas, placements)


@dataclass(slots=True)
class EncoderSpatialHints:
    """Portable spatial-encoder contract; platform adapters translate this to codec-specific APIs."""

    block_size: int
    qp_delta_map: np.ndarray
    rois: list[Roi]
    fovea_qp_delta: int
    periphery_qp_delta: int

    @classmethod
    def from_result(cls, result: ProcessResult, config: StreamRuntimeConfig) -> "EncoderSpatialHints":
        qp = result.qp_map
        if qp is None:
            raise ValueError("runtime must have produce_qp_map=True")
        return cls(
            block_size=max(1, int(config.qp_block)),
            qp_delta_map=np.ascontiguousarray(qp, np.int8),
            rois=list(result.rois),
            fovea_qp_delta=int(config.fovea_qp_delta),
            periphery_qp_delta=int(config.periphery_qp_delta),
        )

    def to_bytes(self) -> bytes:
        """Row-major signed int8 delta-QP map for native encoder adapters."""
        return self.qp_delta_map.astype(np.int8, copy=False).tobytes(order="C")


@dataclass(slots=True)
class AdaptiveBudgetConfig:
    target_bitrate_bps: float | None = None
    target_bytes_per_frame: float | None = None
    target_pixel_fraction: float | None = None
    kp: float = 0.30
    ki: float = 0.035
    ewma_alpha: float = 0.20
    min_strength: float = 0.0
    max_strength: float = 1.0


@dataclass(slots=True)
class AdaptiveBudgetState:
    strength: float
    ewma_bitrate_bps: float = 0.0
    integral: float = 0.0
    last_error: float = 0.0


class AdaptiveBudgetController:
    """Closed-loop controller that adjusts spatial budget instead of using a fixed preset forever."""

    def __init__(self, config: AdaptiveBudgetConfig | None = None, *, initial_strength: float = 0.55):
        self.config = config or AdaptiveBudgetConfig()
        self.state = AdaptiveBudgetState(float(np.clip(initial_strength, 0.0, 1.0)))

    def reset(self, strength: float = 0.55) -> None:
        self.state = AdaptiveBudgetState(float(np.clip(strength, 0.0, 1.0)))

    def _step(self, error: float, dt_s: float) -> float:
        c = self.config
        dt = float(np.clip(dt_s, 1e-4, 2.0))
        self.state.integral = float(np.clip(self.state.integral + error * dt, -4.0, 4.0))
        self.state.last_error = float(error)
        self.state.strength = float(np.clip(
            self.state.strength + c.kp * error + c.ki * self.state.integral,
            c.min_strength,
            c.max_strength,
        ))
        return self.state.strength

    def observe_encoder(self, encoded_bytes: int, duration_s: float) -> float:
        duration = max(float(duration_s), 1e-6)
        instantaneous = max(0.0, float(encoded_bytes)) * 8.0 / duration
        a = float(np.clip(self.config.ewma_alpha, 0.001, 1.0))
        if self.state.ewma_bitrate_bps <= 0:
            self.state.ewma_bitrate_bps = instantaneous
        else:
            self.state.ewma_bitrate_bps = (1-a) * self.state.ewma_bitrate_bps + a * instantaneous
        target = self.config.target_bitrate_bps
        if target is None and self.config.target_bytes_per_frame is not None:
            target = self.config.target_bytes_per_frame * 8.0 / duration
        if target is None or target <= 0:
            return self.state.strength
        return self._step((self.state.ewma_bitrate_bps - target) / target, duration)

    def observe_pixel_fraction(self, fraction: float, dt_s: float = 1/30) -> float:
        target = self.config.target_pixel_fraction
        if target is None or target <= 0:
            return self.state.strength
        return self._step((float(fraction) - target) / target, dt_s)

    def apply(self, runtime: FoveaStreamRuntime) -> AdaptiveBudgetState:
        """Interpolate continuously between balanced and extreme policies."""
        t = float(np.clip(self.state.strength, 0.0, 1.0))
        lerp = lambda a, b: a + (b-a) * t
        cfg = runtime.config
        cfg.foveation.peripheral_scale = float(lerp(1/8, 1/24))
        cfg.foveation.falloff_x = cfg.foveation.falloff_y = float(lerp(.28, .12))
        cfg.tracker.pixel_budget_fraction = float(lerp(.30, .15))
        cfg.tracker.max_tracks = int(round(lerp(8, 4)))
        cfg.context_scale = float(lerp(.25, .10))
        cfg.quality_floor = float(lerp(.04, 0.0))
        cfg.uncertainty_floor = float(lerp(.08, .01))
        cfg.periphery_qp_delta = int(round(lerp(12, 22)))
        runtime.tracker.config.pixel_budget_fraction = cfg.tracker.pixel_budget_fraction
        runtime.tracker.config.max_tracks = cfg.tracker.max_tracks
        return self.state


@dataclass(slots=True)
class LayeredPayload:
    """Low-res base layer plus high-res changed regions and optional one-image atlas."""

    context: np.ndarray
    roi_enhancements: list[RoiEnhancement]
    background_updates: list[BackgroundTileUpdate]
    atlas: RoiAtlas | None

    @property
    def changed_rois(self) -> list[RoiEnhancement]:
        return [r for r in self.roi_enhancements if r.changed]

    @property
    def pixel_count(self) -> int:
        total = int(self.context.shape[0] * self.context.shape[1])
        total += sum(int(e.image.shape[0] * e.image.shape[1]) for e in self.changed_rois if e.image is not None)
        total += sum(int(t.image.shape[0] * t.image.shape[1]) for t in self.background_updates)
        return total


@dataclass(slots=True)
class AdaptiveTransportConfig:
    preset: str = "aggressive"
    auto_motion_proposals: bool = True
    relevance_map_threshold: float = 0.65
    temporal_cache: bool = True
    background_cache: bool = False
    build_atlas: bool = True
    atlas_max_width: int = 1024
    atlas_max_height: int = 1024
    latency: LatencyBudget = field(default_factory=LatencyBudget)
    budget: AdaptiveBudgetConfig | None = None


@dataclass(slots=True)
class TransportResult:
    timestamp_s: float
    process_result: ProcessResult
    layered: LayeredPayload
    encoder_hints: EncoderSpatialHints
    active_evidence_sources: tuple[str, ...]
    controller_state: AdaptiveBudgetState | None
    full_frame_pixels: int

    @property
    def frame_rgb(self) -> np.ndarray:
        if self.process_result.foveated_frame is None:
            raise RuntimeError("same-size foveated output is disabled")
        return self.process_result.foveated_frame

    @property
    def should_send(self) -> bool:
        return bool(self.process_result.decision.send)

    @property
    def payload_pixel_fraction(self) -> float:
        return self.layered.pixel_count / max(1, self.full_frame_pixels)


class AdaptiveTransportRuntime:
    """High-level relevance-aware transport optimizer.

    This is still provider-agnostic: it emits ordinary frames, a layered payload, an optional atlas,
    temporal/background deltas and encoder spatial hints. The application decides what to send.
    """

    def __init__(
        self,
        config: AdaptiveTransportConfig | None = None,
        *,
        runtime: FoveaStreamRuntime | None = None,
        evidence_bus: EvidenceBus | None = None,
    ):
        self.config = config or AdaptiveTransportConfig()
        self.runtime = runtime or FoveaStreamRuntime(StreamRuntimeConfig.preset(
            self.config.preset,
            auto_motion_proposals=self.config.auto_motion_proposals,
        ))
        self.evidence_bus = evidence_bus or EvidenceBus()
        self.temporal_cache = TemporalRoiCache() if self.config.temporal_cache else None
        self.background_cache = BackgroundTileCache() if self.config.background_cache else None
        initial = {"balanced": 0.0, "aggressive": 0.55, "extreme": 1.0}.get(self.config.preset.lower(), 0.55)
        self.controller = AdaptiveBudgetController(self.config.budget, initial_strength=initial) if self.config.budget else None
        self.config.latency.apply(self.runtime)

    def reset(self) -> None:
        self.runtime.reset()
        if self.temporal_cache: self.temporal_cache.reset()
        if self.background_cache: self.background_cache.reset()
        if self.controller: self.controller.reset()

    def feedback_encoded(self, encoded_bytes: int, duration_s: float) -> AdaptiveBudgetState | None:
        if self.controller is None:
            return None
        self.controller.observe_encoder(encoded_bytes, duration_s)
        return self.controller.apply(self.runtime)

    def process(
        self,
        frame_rgb: np.ndarray,
        timestamp_s: float,
        *,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float, float]] = (),
        signals: InnovationSignals | None = None,
        encoded_feedback: tuple[int, float] | None = None,
    ) -> TransportResult:
        frame = np.ascontiguousarray(frame_rgb, np.uint8)
        if encoded_feedback is not None:
            self.feedback_encoded(*encoded_feedback)
        if self.controller is not None:
            self.controller.apply(self.runtime)
        self.config.latency.apply(self.runtime)

        snapshot = self.evidence_bus.snapshot(timestamp_s, frame.shape)
        all_proposals = list(proposals) + snapshot.proposals
        all_points = list(points) + snapshot.points
        if snapshot.quality_map is not None:
            all_proposals.extend(relevance_map_to_proposals(
                snapshot.quality_map,
                threshold=self.config.relevance_map_threshold,
                source="evidence-fusion",
            ))

        result = self.runtime.process(
            frame,
            float(timestamp_s),
            proposals=all_proposals,
            points=all_points,
            signals=signals,
        )
        context = result.context
        if context is None:
            scale = float(np.clip(self.runtime.config.context_scale, .02, 1.0))
            context = _resize_image(frame, round(frame.shape[1]*scale), round(frame.shape[0]*scale))

        if self.temporal_cache is not None:
            roi_updates = self.temporal_cache.update(frame, timestamp_s, result.rois, result.tracks)
        else:
            roi_updates = [RoiEnhancement(i, r, True, 1.0, float("inf"), _crop_roi(frame, r)) for i, r in enumerate(result.rois)]
        bg_updates = self.background_cache.update(frame, timestamp_s) if self.background_cache is not None else []
        atlas = pack_roi_atlas(
            context,
            roi_updates,
            max_width=self.config.atlas_max_width,
            max_height=self.config.atlas_max_height,
        ) if self.config.build_atlas else None
        layered = LayeredPayload(context, roi_updates, bg_updates, atlas)
        hints = EncoderSpatialHints.from_result(result, self.runtime.config)

        state = None
        if self.controller is not None:
            fraction = layered.pixel_count / max(1, frame.shape[0] * frame.shape[1])
            self.controller.observe_pixel_fraction(fraction)
            state = self.controller.apply(self.runtime)

        return TransportResult(
            timestamp_s=float(timestamp_s),
            process_result=result,
            layered=layered,
            encoder_hints=hints,
            active_evidence_sources=snapshot.active_sources,
            controller_state=state,
            full_frame_pixels=int(frame.shape[0] * frame.shape[1]),
        )

    def transform(self, frame_rgb: np.ndarray, timestamp_s: float, **kwargs: Any) -> TransportResult:
        return self.process(frame_rgb, timestamp_s, **kwargs)
