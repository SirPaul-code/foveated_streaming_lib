import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from foveastream import (
    AdaptiveBudgetConfig,
    AdaptiveBudgetController,
    AdaptiveTransportConfig,
    AdaptiveTransportRuntime,
    BackgroundTileCache,
    BackgroundTileCacheConfig,
    EvidenceBus,
    FoveaStreamRuntime,
    LatencyBudget,
    LowResProposalAdapter,
    Roi,
    RoiProposal,
    StreamRuntimeConfig,
    TemporalRoiCache,
    TemporalRoiCacheConfig,
    pack_roi_atlas,
)


def test_evidence_bus_fuses_and_expires_sources():
    bus = EvidenceBus(fusion='max')
    q = np.zeros((20, 30), np.float32)
    q[5:10, 7:14] = .9
    bus.publish(
        'task', 1.0, ttl_s=.5, weight=2.0,
        proposals=[RoiProposal(Roi(.1, .1, .2, .2), confidence=.8, priority=1.5, source='box')],
        points=[(.5, .5)],
        quality_map=q,
    )
    snap = bus.snapshot(1.1, (20, 30, 3))
    assert snap.active_sources == ('task',)
    assert len(snap.proposals) == 1
    assert snap.proposals[0].priority == 3.0
    assert snap.points == [(.5, .5)]
    assert snap.quality_map.shape == (20, 30)
    assert float(snap.quality_map.max()) == 1.0

    expired = bus.snapshot(1.7, (20, 30, 3))
    assert expired.active_sources == ()
    assert expired.quality_map is None


def test_temporal_roi_cache_suppresses_unchanged_crop_and_refreshes_change():
    cache = TemporalRoiCache(TemporalRoiCacheConfig(change_threshold=.02, max_refresh_s=10.0))
    roi = Roi(.25, .25, .5, .5)
    frame = np.zeros((80, 100, 3), np.uint8)

    first = cache.update(frame, 0.0, [roi])
    assert first[0].changed
    assert first[0].image is not None

    second = cache.update(frame.copy(), .1, [roi])
    assert not second[0].changed
    assert second[0].image is None

    changed = frame.copy()
    changed[20:60, 25:75] = 255
    third = cache.update(changed, .2, [roi])
    assert third[0].changed
    assert third[0].change_score > .02


def test_background_cache_only_emits_changed_tiles_before_forced_refresh():
    cache = BackgroundTileCache(BackgroundTileCacheConfig(columns=2, rows=2, change_threshold=.02, max_refresh_s=10.0))
    frame = np.zeros((40, 60, 3), np.uint8)
    assert len(cache.update(frame, 0.0)) == 4
    assert cache.update(frame.copy(), .1) == []
    changed = frame.copy(); changed[:20, :30] = 255
    updates = cache.update(changed, .2)
    assert len(updates) == 1
    assert updates[0].key == 0


def test_low_res_proposal_adapter_keeps_normalized_coordinates():
    seen = []
    def detector(low, ts):
        seen.append(low.shape)
        return [RoiProposal(Roi(.2, .3, .1, .1), 1.0, source='test')]
    adapter = LowResProposalAdapter(detector, analysis_width=100)
    frame = np.zeros((480, 640, 3), np.uint8)
    proposals = adapter.propose(frame, 1.0)
    assert seen[0][1] == 100
    assert proposals[0].roi.x == .2
    assert proposals[0].roi.y == .3


def test_latency_budget_sets_predictive_horizon():
    runtime = FoveaStreamRuntime(StreamRuntimeConfig.preset('aggressive', auto_motion_proposals=False))
    budget = LatencyBudget(encode_s=.03, network_s=.07, consumer_s=.02, safety_s=.01)
    horizon = budget.apply(runtime)
    assert abs(horizon - .13) < 1e-6
    assert abs(runtime.config.tracker.prediction_horizon_s - .13) < 1e-6


def test_budget_controller_gets_more_aggressive_above_target():
    runtime = FoveaStreamRuntime(StreamRuntimeConfig.preset('balanced', auto_motion_proposals=False))
    controller = AdaptiveBudgetController(
        AdaptiveBudgetConfig(target_bitrate_bps=1_000_000, kp=.25, ki=0.0),
        initial_strength=.2,
    )
    before = controller.state.strength
    controller.observe_encoder(encoded_bytes=250_000, duration_s=1.0)  # 2 Mbps
    controller.apply(runtime)
    assert controller.state.strength > before
    assert runtime.config.context_scale < .25
    assert runtime.config.tracker.pixel_budget_fraction < .30
    assert runtime.config.periphery_qp_delta > 12


def test_adaptive_transport_builds_layers_atlas_and_encoder_hints():
    cfg = AdaptiveTransportConfig(
        preset='aggressive',
        auto_motion_proposals=False,
        temporal_cache=True,
        background_cache=False,
        build_atlas=True,
    )
    transport = AdaptiveTransportRuntime(cfg)
    frame = np.zeros((96, 128, 3), np.uint8)
    frame[20:50, 30:70] = 220
    proposal = RoiProposal(Roi(.2, .15, .35, .4), 1.0, source='task')

    first = transport.process(frame, 0.0, proposals=[proposal])
    assert first.frame_rgb.shape == frame.shape
    assert first.layered.context.size > 0
    assert len(first.layered.changed_rois) == 1
    assert first.layered.atlas is not None
    assert first.encoder_hints.qp_delta_map.ndim == 2
    assert len(first.encoder_hints.to_bytes()) == first.encoder_hints.qp_delta_map.size
    assert first.payload_pixel_fraction < 1.0

    second = transport.process(frame.copy(), .05, proposals=[proposal])
    assert len(second.layered.changed_rois) == 0
    assert len(second.layered.atlas.placements) == 1  # context only


def test_roi_atlas_contains_context_and_only_changed_enhancements():
    cache = TemporalRoiCache(TemporalRoiCacheConfig(max_refresh_s=10.0))
    frame = np.zeros((80, 120, 3), np.uint8)
    roi = Roi(.1, .1, .25, .25)
    updates = cache.update(frame, 0.0, [roi])
    context = np.zeros((20, 30, 3), np.uint8)
    atlas = pack_roi_atlas(context, updates, max_width=256, max_height=256)
    assert len(atlas.placements) == 2
    assert atlas.placements[0].kind == 'context'
    assert atlas.placements[1].kind == 'roi'
