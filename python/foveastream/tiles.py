from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal
import math

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

TileCurve = Literal["linear", "smoothstep", "gaussian", "exponential", "power"]
TileAggregation = Literal["mean", "max", "p90"]
TileDegradationFn = Callable[[float], float]


@dataclass(slots=True)
class TilePolicyContext:
    """All spatial/relevance information available to advanced custom tile policies.

    Coordinates are normalized to [0,1]. ``relevance`` is the configured aggregate used by the
    planner while mean/max/p90 are always provided so a custom policy can make a different choice.
    ``distance`` is ``1 - relevance``.
    """

    index: int
    row: int
    column: int
    x: float
    y: float
    w: float
    h: float
    relevance: float
    distance: float
    mean_relevance: float
    max_relevance: float
    p90_relevance: float
    map_width: int
    map_height: int

    @property
    def center_x(self) -> float:
        return self.x + self.w * 0.5

    @property
    def center_y(self) -> float:
        return self.y + self.h * 0.5

    @property
    def area_fraction(self) -> float:
        return self.w * self.h


TileQualityFn = Callable[[TilePolicyContext], float]
TileResolutionFn = Callable[[TilePolicyContext, float], float]
TileQpFn = Callable[[TilePolicyContext, float], int]


@dataclass(slots=True)
class TilePlannerConfig:
    """Convert a dense relevance field into exactly ``target_tiles`` logical transport tiles.

    ``curve`` maps distance-from-relevance (0 = highly relevant, 1 = irrelevant) to fidelity.
    A simple custom ``distance -> quality`` function can be supplied as ``degradation_fn``.
    Advanced callers may instead provide context-aware quality/resolution/QP hooks to TilePlanner.
    """

    target_tiles: int = 100
    curve: TileCurve = "gaussian"
    curve_strength: float = 3.0
    aggregation: TileAggregation = "max"
    min_quality: float = 0.04
    min_resolution_scale: float = 0.125
    fovea_qp_delta: int = -4
    periphery_qp_delta: int = 18

    def validate(self) -> None:
        if self.target_tiles < 1:
            raise ValueError("target_tiles must be >= 1")
        if self.curve not in ("linear", "smoothstep", "gaussian", "exponential", "power"):
            raise ValueError("unsupported tile curve")
        if self.aggregation not in ("mean", "max", "p90"):
            raise ValueError("aggregation must be mean, max or p90")
        if not 0.0 <= self.min_quality <= 1.0:
            raise ValueError("min_quality must be in [0,1]")
        if not 0.0 < self.min_resolution_scale <= 1.0:
            raise ValueError("min_resolution_scale must be in (0,1]")


@dataclass(slots=True)
class TileDecision:
    index: int
    row: int
    column: int
    x: float
    y: float
    w: float
    h: float
    relevance: float
    distance: float
    quality: float
    resolution_scale: float
    qp_delta: int

    @property
    def area_fraction(self) -> float:
        return self.w * self.h


@dataclass(slots=True)
class TilePlan:
    requested_tiles: int
    tiles: list[TileDecision]
    curve: str
    curve_strength: float
    aggregation: str
    min_quality: float
    min_resolution_scale: float

    @property
    def tile_count(self) -> int:
        return len(self.tiles)

    @property
    def mean_quality(self) -> float:
        return float(np.mean([t.quality for t in self.tiles])) if self.tiles else 0.0

    @property
    def mean_qp_delta(self) -> float:
        return float(np.mean([t.qp_delta for t in self.tiles])) if self.tiles else 0.0

    @property
    def effective_pixel_fraction(self) -> float:
        """Estimated pixels vs full-res if each tile is rasterized at its suggested scale."""
        return float(sum(t.area_fraction * t.resolution_scale * t.resolution_scale for t in self.tiles))


def _row_counts(target_tiles: int, aspect: float) -> list[int]:
    """Return row column-counts whose sum is exactly target_tiles and shape follows aspect ratio."""
    n = max(1, int(target_tiles))
    aspect = max(1e-6, float(aspect))
    rows = max(1, min(n, round(math.sqrt(n / aspect))))
    base, rem = divmod(n, rows)
    return [base + (1 if row < rem else 0) for row in range(rows)]


def _builtin_curve(distance: float, curve: TileCurve, strength: float) -> float:
    d = float(np.clip(distance, 0.0, 1.0))
    s = max(1e-4, float(strength))
    if curve == "linear":
        q = 1.0 - d
    elif curve == "smoothstep":
        smooth = d * d * (3.0 - 2.0 * d)
        q = 1.0 - smooth
    elif curve == "power":
        q = (1.0 - d) ** s
    elif curve == "exponential":
        edge = math.exp(-s)
        q = (math.exp(-s * d) - edge) / max(1e-9, 1.0 - edge)
    else:  # gaussian
        edge = math.exp(-s)
        q = (math.exp(-s * d * d) - edge) / max(1e-9, 1.0 - edge)
    return float(np.clip(q, 0.0, 1.0))


class TilePlanner:
    """Build a discrete multi-resolution/QP tile policy from a dense relevance map.

    Customization levels, from simplest to most powerful:

    1. ``curve`` / ``curve_strength`` in TilePlannerConfig.
    2. ``degradation_fn(distance) -> raw_quality``.
    3. ``quality_fn(context) -> raw_quality`` for geometry/relevance-aware policy.
    4. ``resolution_fn(context, final_quality) -> scale``.
    5. ``qp_fn(context, final_quality) -> signed_delta_qp``.

    Configured quality/resolution floors are still enforced after custom callbacks. QP values are
    clamped to signed int8 range so the result remains compatible with the portable tile contract.
    """

    def __init__(
        self,
        config: TilePlannerConfig | None = None,
        *,
        degradation_fn: TileDegradationFn | None = None,
        quality_fn: TileQualityFn | None = None,
        resolution_fn: TileResolutionFn | None = None,
        qp_fn: TileQpFn | None = None,
    ):
        if degradation_fn is not None and quality_fn is not None:
            raise ValueError("use degradation_fn or quality_fn, not both")
        self.config = config or TilePlannerConfig()
        self.config.validate()
        self.degradation_fn = degradation_fn
        self.quality_fn = quality_fn
        self.resolution_fn = resolution_fn
        self.qp_fn = qp_fn

    def _aggregate(self, tile: np.ndarray) -> float:
        if tile.size == 0:
            return 0.0
        if self.config.aggregation == "mean":
            return float(np.mean(tile))
        if self.config.aggregation == "p90":
            return float(np.percentile(tile, 90))
        return float(np.max(tile))

    def _raw_quality(self, context: TilePolicyContext) -> float:
        if self.quality_fn is not None:
            raw = float(self.quality_fn(context))
        elif self.degradation_fn is not None:
            raw = float(self.degradation_fn(context.distance))
        else:
            raw = _builtin_curve(context.distance, self.config.curve, self.config.curve_strength)
        return float(np.clip(raw, 0.0, 1.0))

    def _quality(self, context: TilePolicyContext) -> float:
        raw = self._raw_quality(context)
        qmin = float(self.config.min_quality)
        return float(np.clip(qmin + (1.0 - qmin) * raw, 0.0, 1.0))

    def _resolution_scale(self, context: TilePolicyContext, quality: float) -> float:
        smin = float(self.config.min_resolution_scale)
        if self.resolution_fn is not None:
            raw = float(self.resolution_fn(context, quality))
        else:
            raw = smin + (1.0 - smin) * quality
        return float(np.clip(raw, smin, 1.0))

    def _qp_delta(self, context: TilePolicyContext, quality: float) -> int:
        if self.qp_fn is not None:
            qp = int(round(self.qp_fn(context, quality)))
        else:
            qp = round(
                self.config.periphery_qp_delta
                + quality * (self.config.fovea_qp_delta - self.config.periphery_qp_delta)
            )
        return int(np.clip(qp, -128, 127))

    def plan(self, quality_map: np.ndarray) -> TilePlan:
        qmap = np.clip(np.asarray(quality_map, np.float32), 0.0, 1.0)
        if qmap.ndim != 2:
            raise ValueError("quality_map must be HxW")
        h, w = qmap.shape
        if h < 1 or w < 1:
            raise ValueError("quality_map must be non-empty")

        row_counts = _row_counts(self.config.target_tiles, w / h)
        rows = len(row_counts)
        decisions: list[TileDecision] = []
        idx = 0
        for row, cols in enumerate(row_counts):
            y0 = round(row * h / rows)
            y1 = round((row + 1) * h / rows)
            for col in range(cols):
                x0 = round(col * w / cols)
                x1 = round((col + 1) * w / cols)
                tile_values = qmap[y0:y1, x0:x1]
                mean_rel = float(np.mean(tile_values)) if tile_values.size else 0.0
                max_rel = float(np.max(tile_values)) if tile_values.size else 0.0
                p90_rel = float(np.percentile(tile_values, 90)) if tile_values.size else 0.0
                relevance = float(np.clip(self._aggregate(tile_values), 0.0, 1.0))
                context = TilePolicyContext(
                    index=idx,
                    row=row,
                    column=col,
                    x=x0 / w,
                    y=y0 / h,
                    w=(x1 - x0) / w,
                    h=(y1 - y0) / h,
                    relevance=relevance,
                    distance=1.0 - relevance,
                    mean_relevance=float(np.clip(mean_rel, 0.0, 1.0)),
                    max_relevance=float(np.clip(max_rel, 0.0, 1.0)),
                    p90_relevance=float(np.clip(p90_rel, 0.0, 1.0)),
                    map_width=w,
                    map_height=h,
                )
                quality = self._quality(context)
                resolution_scale = self._resolution_scale(context, quality)
                qp_delta = self._qp_delta(context, quality)
                decisions.append(TileDecision(
                    index=idx,
                    row=row,
                    column=col,
                    x=context.x,
                    y=context.y,
                    w=context.w,
                    h=context.h,
                    relevance=relevance,
                    distance=context.distance,
                    quality=quality,
                    resolution_scale=resolution_scale,
                    qp_delta=qp_delta,
                ))
                idx += 1

        custom = self.degradation_fn is not None or self.quality_fn is not None
        return TilePlan(
            requested_tiles=self.config.target_tiles,
            tiles=decisions,
            curve="custom" if custom else self.config.curve,
            curve_strength=self.config.curve_strength,
            aggregation=self.config.aggregation,
            min_quality=self.config.min_quality,
            min_resolution_scale=self.config.min_resolution_scale,
        )


def plan_tiles(
    quality_map: np.ndarray,
    config: TilePlannerConfig | None = None,
    *,
    degradation_fn: TileDegradationFn | None = None,
    quality_fn: TileQualityFn | None = None,
    resolution_fn: TileResolutionFn | None = None,
    qp_fn: TileQpFn | None = None,
) -> TilePlan:
    return TilePlanner(
        config,
        degradation_fn=degradation_fn,
        quality_fn=quality_fn,
        resolution_fn=resolution_fn,
        qp_fn=qp_fn,
    ).plan(quality_map)


def rasterize_tile_plan(plan: TilePlan, width: int, height: int, *, field: str = "quality") -> np.ndarray:
    """Rasterize a discrete tile field back to HxW for preview or a tile-driven foveation pass."""
    width, height = max(1, int(width)), max(1, int(height))
    out = np.zeros((height, width), np.float32)
    for tile in plan.tiles:
        x0 = int(round(tile.x * width)); x1 = int(round((tile.x + tile.w) * width))
        y0 = int(round(tile.y * height)); y1 = int(round((tile.y + tile.h) * height))
        value = getattr(tile, field)
        out[max(0, y0):min(height, y1), max(0, x0):min(width, x1)] = float(value)
    return out


def apply_tile_plan(frame_rgb: np.ndarray, plan: TilePlan) -> np.ndarray:
    """Reference realization of ``resolution_scale`` for visual testing and generic pipelines.

    Every logical tile is downsampled to its suggested resolution and resized back into its original
    rectangle. The output keeps the original frame dimensions, making the spatial loss directly
    visible. Production tiled transports should normally send the low-resolution tile itself rather
    than upscaling it again; this function is a CPU/reference preview, not a zero-copy hot path.
    """
    if cv2 is None:
        raise RuntimeError("apply_tile_plan requires opencv-python")
    frame = np.ascontiguousarray(frame_rgb, np.uint8)
    h, w = frame.shape[:2]
    out = frame.copy()
    for tile in plan.tiles:
        x0 = int(round(tile.x * w)); x1 = int(round((tile.x + tile.w) * w))
        y0 = int(round(tile.y * h)); y1 = int(round((tile.y + tile.h) * h))
        x0, x1 = max(0, x0), min(w, x1)
        y0, y1 = max(0, y0), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = frame[y0:y1, x0:x1]
        ch, cw = crop.shape[:2]
        scale = float(np.clip(tile.resolution_scale, 1.0 / max(cw, ch, 1), 1.0))
        low_w = max(1, int(round(cw * scale)))
        low_h = max(1, int(round(ch * scale)))
        if low_w == cw and low_h == ch:
            out[y0:y1, x0:x1] = crop
            continue
        low = cv2.resize(crop, (low_w, low_h), interpolation=cv2.INTER_AREA)
        out[y0:y1, x0:x1] = cv2.resize(low, (cw, ch), interpolation=cv2.INTER_LINEAR)
    return out


def render_tile_plan(
    frame_rgb: np.ndarray,
    plan: TilePlan,
    *,
    alpha: float = 0.42,
    show_labels: bool | None = None,
) -> np.ndarray:
    """Visualize tile boundaries and per-tile quality over an RGB frame."""
    if cv2 is None:
        raise RuntimeError("render_tile_plan requires opencv-python")
    frame = np.ascontiguousarray(frame_rgb, np.uint8)
    h, w = frame.shape[:2]
    field = rasterize_tile_plan(plan, w, h, field="quality")
    heat_bgr = cv2.applyColorMap(np.uint8(np.clip(field, 0, 1) * 255), cv2.COLORMAP_TURBO)
    heat_rgb = cv2.cvtColor(heat_bgr, cv2.COLOR_BGR2RGB)
    out = cv2.addWeighted(frame, 1.0 - float(np.clip(alpha, 0, 1)), heat_rgb, float(np.clip(alpha, 0, 1)), 0)
    labels = plan.tile_count <= 25 if show_labels is None else bool(show_labels)
    for tile in plan.tiles:
        x0 = int(round(tile.x * w)); x1 = int(round((tile.x + tile.w) * w))
        y0 = int(round(tile.y * h)); y1 = int(round((tile.y + tile.h) * h))
        cv2.rectangle(out, (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), (255, 255, 255), 1)
        if labels and x1 - x0 >= 44 and y1 - y0 >= 24:
            cv2.putText(out, f"{tile.quality:.2f}", (x0 + 3, min(y1 - 4, y0 + 16)),
                        cv2.FONT_HERSHEY_SIMPLEX, .38, (255, 255, 255), 1, cv2.LINE_AA)
    return out
