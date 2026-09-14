import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from foveastream import (
    CallbackSink,
    FoveaStreamRuntime,
    Roi,
    RoiProposal,
    RoiTrackerConfig,
    StreamRuntimeConfig,
)


def test_multiple_rois_survive_runtime():
    cfg = StreamRuntimeConfig(tracker=RoiTrackerConfig(max_tracks=4, pixel_budget_fraction=.5))
    runtime = FoveaStreamRuntime(cfg)
    frame = np.zeros((120, 160, 3), np.uint8)
    result = runtime.process(
        frame, 0.0,
        proposals=[
            RoiProposal(Roi(.05, .10, .15, .20), 1.0, source='a'),
            RoiProposal(Roi(.70, .60, .15, .20), .9, source='b'),
        ],
    )
    assert len(result.rois) == 2
    assert len(result.roi_views) == 2
    assert result.quality_map.shape == (120, 160)
    assert result.foveated_frame.shape == frame.shape


def test_overlapping_proposals_are_deduplicated():
    runtime = FoveaStreamRuntime(StreamRuntimeConfig(tracker=RoiTrackerConfig(proposal_merge_iou=.5)))
    frame = np.zeros((80, 120, 3), np.uint8)
    result = runtime.process(
        frame, 0.0,
        proposals=[
            RoiProposal(Roi(.2, .2, .2, .2), .8, source='motion'),
            RoiProposal(Roi(.21, .21, .2, .2), .95, source='external'),
        ],
    )
    assert len(result.rois) == 1


def test_callback_sink_can_filter_on_send_decision():
    calls = []
    sink = CallbackSink(lambda result: calls.append(result.timestamp_s), only_when_send=True)
    runtime = FoveaStreamRuntime()
    frame = np.zeros((48, 64, 3), np.uint8)
    runtime.push(frame, 0.0, sink)
    assert calls == [0.0]  # initial scheduler state is stale -> first frame is sendable


def test_timestamps_must_be_monotonic():
    runtime = FoveaStreamRuntime()
    frame = np.zeros((32, 32, 3), np.uint8)
    runtime.process(frame, 2.0)
    try:
        runtime.process(frame, 1.0)
    except ValueError as exc:
        assert 'monotonic' in str(exc)
    else:
        raise AssertionError('expected monotonic timestamp validation')
