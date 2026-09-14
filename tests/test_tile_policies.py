import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from foveastream.tiles import (
    TilePlanner,
    TilePlannerConfig,
    TilePolicyContext,
    apply_tile_plan,
)


def test_context_quality_resolution_and_qp_hooks():
    qmap = np.zeros((80, 120), np.float32)
    qmap[20:45, 30:65] = 1.0
    seen = []

    def quality(ctx: TilePolicyContext) -> float:
        seen.append(ctx)
        return ctx.max_relevance

    def resolution(ctx: TilePolicyContext, q: float) -> float:
        del ctx
        return 1.0 if q > .8 else .25

    def qp(ctx: TilePolicyContext, q: float) -> int:
        del ctx
        return -6 if q > .8 else 18

    planner = TilePlanner(
        TilePlannerConfig(target_tiles=25, aggregation='mean', min_quality=0.0, min_resolution_scale=.125),
        quality_fn=quality,
        resolution_fn=resolution,
        qp_fn=qp,
    )
    plan = planner.plan(qmap)

    assert plan.tile_count == 25
    assert plan.curve == 'custom'
    assert len(seen) == 25
    assert any(t.quality > .8 and t.resolution_scale == 1.0 and t.qp_delta == -6 for t in plan.tiles)
    assert any(t.quality == 0.0 and t.resolution_scale == .25 and t.qp_delta == 18 for t in plan.tiles)
    assert all(0.0 <= c.mean_relevance <= c.p90_relevance <= c.max_relevance <= 1.0 for c in seen)


def test_cannot_supply_both_quality_and_degradation_hooks():
    try:
        TilePlanner(
            TilePlannerConfig(),
            degradation_fn=lambda d: 1-d,
            quality_fn=lambda ctx: ctx.relevance,
        )
    except ValueError as exc:
        assert 'degradation_fn or quality_fn' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_custom_hooks_are_safely_clamped():
    qmap = np.ones((32, 32), np.float32)
    planner = TilePlanner(
        TilePlannerConfig(target_tiles=4, min_quality=.1, min_resolution_scale=.2),
        quality_fn=lambda ctx: 99.0,
        resolution_fn=lambda ctx, q: -10.0,
        qp_fn=lambda ctx, q: 999,
    )
    plan = planner.plan(qmap)
    assert all(t.quality == 1.0 for t in plan.tiles)
    assert all(t.resolution_scale == .2 for t in plan.tiles)
    assert all(t.qp_delta == 127 for t in plan.tiles)


def test_apply_tile_plan_preserves_shape_and_degrades_low_scale_tiles():
    yy, xx = np.mgrid[0:64, 0:96]
    frame = np.stack([
        (xx * 17 + yy * 3) % 256,
        (xx * 5 + yy * 19) % 256,
        (xx * 13 + yy * 11) % 256,
    ], axis=2).astype(np.uint8)
    qmap = np.zeros((64, 96), np.float32)
    qmap[:, :48] = 1.0

    planner = TilePlanner(
        TilePlannerConfig(target_tiles=2, aggregation='max', min_quality=0.0, min_resolution_scale=.125),
        quality_fn=lambda ctx: ctx.relevance,
        resolution_fn=lambda ctx, q: 1.0 if q > .5 else .125,
    )
    plan = planner.plan(qmap)
    output = apply_tile_plan(frame, plan)

    assert output.shape == frame.shape
    # High-quality half should be untouched while the low-scale half loses detail.
    assert np.array_equal(output[:, :48], frame[:, :48])
    assert not np.array_equal(output[:, 48:], frame[:, 48:])
