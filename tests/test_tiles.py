import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from foveastream import (
    TilePlanner,
    TilePlannerConfig,
    plan_tiles,
    rasterize_tile_plan,
)


def test_tile_planner_returns_exact_requested_count():
    q = np.zeros((90, 160), np.float32)
    q[25:60, 55:105] = 1.0
    for count in (1, 10, 25, 100, 137, 400):
        plan = plan_tiles(q, TilePlannerConfig(target_tiles=count))
        assert plan.tile_count == count


def test_relevant_tile_gets_more_quality_scale_and_better_qp():
    q = np.zeros((100, 100), np.float32)
    q[:50, :50] = 1.0
    plan = plan_tiles(q, TilePlannerConfig(target_tiles=4, aggregation='max'))
    best = max(plan.tiles, key=lambda t: t.relevance)
    worst = min(plan.tiles, key=lambda t: t.relevance)
    assert best.quality > worst.quality
    assert best.resolution_scale > worst.resolution_scale
    assert best.qp_delta < worst.qp_delta


def test_custom_degradation_function_is_used():
    q = np.zeros((20, 20), np.float32)
    q[:10, :10] = 1.0
    planner = TilePlanner(
        TilePlannerConfig(target_tiles=4, min_quality=0.0),
        degradation_fn=lambda distance: 1.0 if distance < 0.25 else 0.1,
    )
    plan = planner.plan(q)
    assert max(t.quality for t in plan.tiles) == 1.0
    assert min(t.quality for t in plan.tiles) == 0.1


def test_effective_pixel_fraction_and_rasterization_are_bounded():
    q = np.linspace(0, 1, 64 * 64, dtype=np.float32).reshape(64, 64)
    plan = plan_tiles(q, TilePlannerConfig(target_tiles=64, min_resolution_scale=.125))
    assert 0.0 < plan.effective_pixel_fraction <= 1.0
    raster = rasterize_tile_plan(plan, 64, 64)
    assert raster.shape == (64, 64)
    assert float(raster.min()) >= 0.0
    assert float(raster.max()) <= 1.0
