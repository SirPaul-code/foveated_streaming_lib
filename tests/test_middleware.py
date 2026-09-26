import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))

from foveastream import (
    CallbackFrameSink,
    FramePacket,
    FoveaStreamTransform,
    InlinePipeline,
    RealtimeBridge,
    StreamRuntimeConfig,
    FoveaStreamRuntime,
)


def frame(value=0):
    return np.full((48, 64, 3), value, np.uint8)


def test_drop_in_transform_preserves_shape_timestamp_and_metadata():
    transform = FoveaStreamTransform(preset='aggressive', auto_motion_proposals=False)
    packet = FramePacket(frame(10), 12.5, metadata={'camera_id': 'front', 'seq': 7})
    out = transform.process(packet)
    assert out is not None
    assert out.frame_rgb.shape == packet.frame_rgb.shape
    assert out.frame_rgb.dtype == np.uint8
    assert out.timestamp_s == 12.5
    assert out.metadata == {'camera_id': 'front', 'seq': 7}
    assert out.result.timestamp_s == 12.5


def test_every_frame_is_default_for_continuous_video():
    transform = FoveaStreamTransform(preset='aggressive', auto_motion_proposals=False)
    assert transform.process(FramePacket(frame(), 0.0)) is not None
    assert transform.process(FramePacket(frame(), 0.01)) is not None


def test_when_send_can_suppress_redundant_frame():
    runtime = FoveaStreamRuntime(StreamRuntimeConfig.preset('balanced', auto_motion_proposals=False))
    transform = FoveaStreamTransform(runtime, emit_policy='when_send')
    assert transform.process(FramePacket(frame(), 0.0)) is not None
    assert transform.process(FramePacket(frame(), 0.01)) is None


def test_inline_pipeline_connects_source_transform_sink():
    received = []
    sink = CallbackFrameSink(lambda packet: received.append(packet.metadata['seq']))
    pipeline = InlinePipeline(
        FoveaStreamTransform(preset='aggressive', auto_motion_proposals=False),
        sink,
    )
    source = [
        FramePacket(frame(1), 0.0, metadata={'seq': 1}),
        FramePacket(frame(2), 0.04, metadata={'seq': 2}),
        FramePacket(frame(3), 0.08, metadata={'seq': 3}),
    ]
    stats = pipeline.run(source)
    assert received == [1, 2, 3]
    assert stats.submitted == 3
    assert stats.processed == 3
    assert stats.emitted == 3
    assert stats.dropped_input == 0


def test_realtime_latest_queue_discards_stale_pending_frames():
    received = []
    sink = CallbackFrameSink(lambda packet: received.append(packet.metadata['seq']))
    bridge = RealtimeBridge(
        FoveaStreamTransform(preset='aggressive', auto_motion_proposals=False),
        sink,
        queue_size=1,
        drop_policy='latest',
        autostart=False,
    )

    bridge.submit(FramePacket(frame(1), 0.0, metadata={'seq': 1}))
    bridge.submit(FramePacket(frame(2), 0.04, metadata={'seq': 2}))
    bridge.submit(FramePacket(frame(3), 0.08, metadata={'seq': 3}))
    assert bridge.stats.dropped_input == 2

    bridge.start()
    assert bridge.close(drain=True, timeout=5.0)
    assert received == [3]
    stats = bridge.stats
    assert stats.submitted == 3
    assert stats.processed == 1
    assert stats.emitted == 1
    assert stats.dropped_input == 2
    assert stats.max_queue_depth == 1


def test_realtime_bridge_propagates_processing_error_state():
    class BrokenTransform:
        def process(self, packet):
            raise RuntimeError('boom')

    bridge = RealtimeBridge(
        BrokenTransform(),
        CallbackFrameSink(lambda packet: None),
        autostart=True,
    )
    bridge.submit(FramePacket(frame(), 0.0))
    assert bridge.close(drain=True, timeout=5.0)
    assert bridge.stats.errors == 1
    assert isinstance(bridge.last_error, RuntimeError)
