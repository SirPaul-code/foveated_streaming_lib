from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Sequence
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
class TilePlannerConfig:
    """Convert a dense relevance field into exactly `target_tiles` logical transport tiles.

    `curve` maps distance-from-relevance (0 = highly relevant, 1 = irrelevant) to fidelity.
    A custom `TileDegradationFn` may be supplied to `TilePlanner` instead.
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
    """Build a discrete multi-resolution/QP tile policy from a dense relevance map."""

    def __init__(
        self,
        config: TilePlannerConfig | None = None,
        *,
        degradation_fn: TileDegradationFn | None = None,
    ):
        self.config = config or TilePlannerConfig()
        self.config.validate()
        self.degradation_fn = degradation_fn

    def _aggregate(self, tile: np.ndarray) -> float:
        if tile.size == 0:
            return 0.0
        if self.config.aggregation == "mean":
            return float(np.mean(tile))
        if self.config.aggregation == "p90":
            return float(np.percentile(tile, 90))
        return float(np.max(tile))

    def _quality(self, distance: float) -> float:
        raw = (
            float(self.degradation_fn(float(np.clip(distance, 0.0, 1.0))))
            if self.degradation_fn is not None
            else _builtin_curve(distance, self.config.curve, self.config.curve_strength)
        )
        raw = float(np.clip(raw, 0.0, 1.0))
        qmin = float(self.config.min_quality)
        return float(np.clip(qmin + (1.0 - qmin) * raw, 0.0, 1.0))

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
                relevance = float(np.clip(self._aggregate(qmap[y0:y1, x0:x1]), 0.0, 1.0))
                distance = 1.0 - relevance
                quality = self._quality(distance)
                smin = float(self.config.min_resolution_scale)
                resolution_scale = float(np.clip(smin + (1.0 - smin) * quality, smin, 1.0))
                qp = round(
                    self.config.periphery_qp_delta
                    + quality * (self.config.fovea_qp_delta - self.config.periphery_qp_delta)
                )
                decisions.append(TileDecision(
                    index=idx,
                    row=row,
                    column=col,
                    x=x0 / w,
                    y=y0 / h,
                    w=(x1 - x0) / w,
                    h=(y1 - y0) / h,
                    relevance=relevance,
                    distance=distance,
                    quality=quality,
                    resolution_scale=resolution_scale,
                    qp_delta=int(np.clip(qp, -128, 127)),
                ))
                idx += 1

        return TilePlan(
            requested_tiles=self.config.target_tiles,
            tiles=decisions,
            curve="custom" if self.degradation_fn is not None else self.config.curve,
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
) -> TilePlan:
    return TilePlanner(config, degradation_fn=degradation_fn).plan(quality_map)


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
