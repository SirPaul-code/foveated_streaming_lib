"""Copyable examples for custom FoveaStream logical-tile policies.

These are ordinary Python callbacks. Import them into your application or copy the functions into
your own project. See docs/TILE_POLICIES.md for the complete callback contract.
"""
from __future__ import annotations

import math

from foveastream import TilePolicyContext


def gentle_distance(distance: float) -> float:
    """Simple distance-only policy: keep mid-distance tiles relatively detailed."""
    d = max(0.0, min(1.0, float(distance)))
    return 1.0 - d ** 1.7


def focus_cliff(distance: float) -> float:
    """Simple distance-only policy: full quality near relevance, then a sharp drop."""
    d = max(0.0, min(1.0, float(distance)))
    if d <= 0.18:
        return 1.0
    return max(0.0, 1.0 - ((d - 0.18) / 0.82) ** 0.55)


def context_aware_quality(ctx: TilePolicyContext) -> float:
    """Use multiple relevance statistics and a mild center prior.

    This demonstrates why ``quality_fn`` is more powerful than ``degradation_fn``: the callback can
    see tile geometry plus mean/max/p90 relevance instead of receiving only one distance scalar.
    """
    center_distance = math.hypot(ctx.center_x - 0.5, ctx.center_y - 0.5) / math.sqrt(0.5)
    center_prior = max(0.0, 1.0 - center_distance)
    semantic = max(ctx.max_relevance, 0.8 * ctx.p90_relevance, 0.55 * ctx.mean_relevance)
    return min(1.0, 0.90 * semantic + 0.10 * center_prior)


def stepped_resolution(ctx: TilePolicyContext, quality: float) -> float:
    """Quantize spatial scale to transport-friendly levels."""
    del ctx
    if quality >= 0.82:
        return 1.0
    if quality >= 0.55:
        return 0.5
    if quality >= 0.25:
        return 0.25
    return 0.125


def tiered_qp(ctx: TilePolicyContext, quality: float) -> int:
    """Map quality to a few explicit delta-QP tiers."""
    del ctx
    if quality >= 0.85:
        return -6
    if quality >= 0.60:
        return 0
    if quality >= 0.30:
        return 8
    return 18
