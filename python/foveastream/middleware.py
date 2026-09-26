from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping, Protocol, Sequence

import numpy as np

from .streaming import (
    FoveaStreamRuntime,
    InnovationSignals,
    ProcessResult,
    RoiProposal,
    StreamRuntimeConfig,
)

EmitPolicy = Literal["every_frame", "when_send"]
DropPolicy = Literal["latest", "block"]


@dataclass(slots=True)
class FramePacket:
    """Provider-agnostic frame envelope entering the middleware.

    FoveaStream intentionally keeps transport/provider metadata opaque. Applications may attach
    camera IDs, RTP timestamps, frame IDs, intrinsics handles, trace IDs, etc. in ``metadata`` and
    recover them unchanged on the optimized output.
    """

    frame_rgb: np.ndarray
    timestamp_s: float
    metadata: dict[str, Any] = field(default_factory=dict)
    proposals: tuple[RoiProposal, ...] = ()
    points: tuple[tuple[float, float], ...] = ()
    signals: InnovationSignals | None = None

    @classmethod
    def now(
        cls,
        frame_rgb: np.ndarray,
        *,
        metadata: Mapping[str, Any] | None = None,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float, float]] = (),
        signals: InnovationSignals | None = None,
    ) -> "FramePacket":
        return cls(
            frame_rgb=frame_rgb,
            timestamp_s=time.monotonic(),
            metadata=dict(metadata or {}),
            proposals=tuple(proposals),
            points=tuple(points),
            signals=signals,
        )


@dataclass(slots=True)
class OptimizedFrame:
    """Drop-in same-size frame plus the complete FoveaStream decision metadata."""

    frame_rgb: np.ndarray
    timestamp_s: float
    metadata: dict[str, Any]
    result: ProcessResult

    @property
    def should_send(self) -> bool:
        return bool(self.result.decision.send)


class FrameTransform(Protocol):
    def process(self, packet: FramePacket) -> OptimizedFrame | None: ...


class FrameSink(Protocol):
    def consume(self, packet: OptimizedFrame) -> None: ...


class CallbackFrameSink:
    """Adapts any callback/function into a middleware output sink."""

    def __init__(self, callback: Callable[[OptimizedFrame], None]):
        self.callback = callback

    def consume(self, packet: OptimizedFrame) -> None:
        self.callback(packet)


class FoveaStreamTransform:
    """Drop-in frame transform for ``source -> FoveaStream -> sink`` pipelines.

    ``every_frame`` is deliberately the default emit policy. Continuous video paths such as
    camera -> encoder/WebRTC usually need one output for every frame they submit. Model/request
    pipelines that may suppress redundant frames can opt into ``when_send``.
    """

    def __init__(
        self,
        runtime: FoveaStreamRuntime | None = None,
        *,
        preset: str = "aggressive",
        auto_motion_proposals: bool = True,
        emit_policy: EmitPolicy = "every_frame",
    ):
        if emit_policy not in ("every_frame", "when_send"):
            raise ValueError("emit_policy must be 'every_frame' or 'when_send'")
        self.runtime = runtime or FoveaStreamRuntime(
            StreamRuntimeConfig.preset(preset, auto_motion_proposals=auto_motion_proposals)
        )
        self.emit_policy: EmitPolicy = emit_policy

    def reset(self) -> None:
        self.runtime.reset()

    def process(self, packet: FramePacket) -> OptimizedFrame | None:
        result = self.runtime.process(
            packet.frame_rgb,
            packet.timestamp_s,
            proposals=packet.proposals,
            points=packet.points,
            signals=packet.signals,
        )
        if self.emit_policy == "when_send" and not result.decision.send:
            return None
        if result.foveated_frame is None:
            raise RuntimeError(
                "FoveaStreamTransform requires produce_foveated_frame=True because a drop-in "
                "frame transform must return a same-size frame"
            )
        return OptimizedFrame(
            frame_rgb=result.foveated_frame,
            timestamp_s=packet.timestamp_s,
            metadata=dict(packet.metadata),
            result=result,
        )

    def transform(
        self,
        frame_rgb: np.ndarray,
        timestamp_s: float | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float, float]] = (),
        signals: InnovationSignals | None = None,
    ) -> OptimizedFrame | None:
        """Convenience API when the caller already owns a camera/callback loop."""
        packet = FramePacket(
            frame_rgb=frame_rgb,
            timestamp_s=time.monotonic() if timestamp_s is None else float(timestamp_s),
            metadata=dict(metadata or {}),
            proposals=tuple(proposals),
            points=tuple(points),
            signals=signals,
        )
        return self.process(packet)


def _coerce_packet(item: FramePacket | tuple[float, np.ndarray]) -> FramePacket:
    if isinstance(item, FramePacket):
        return item
    if isinstance(item, tuple) and len(item) == 2:
        timestamp_s, frame_rgb = item
        return FramePacket(frame_rgb=frame_rgb, timestamp_s=float(timestamp_s))
    raise TypeError("source items must be FramePacket or (timestamp_s, frame_rgb)")


def transform_source(
    source: Iterable[FramePacket | tuple[float, np.ndarray]],
    transform: FrameTransform,
) -> Iterator[OptimizedFrame]:
    """Pull-style adapter: wrap any iterable source and yield optimized frames."""
    for item in source:
        output = transform.process(_coerce_packet(item))
        if output is not None:
            yield output


@dataclass(slots=True)
class PipelineStats:
    submitted: int = 0
    processed: int = 0
    emitted: int = 0
    scheduler_skipped: int = 0
    dropped_input: int = 0
    errors: int = 0
    max_queue_depth: int = 0


class InlinePipeline:
    """Synchronous source -> transform -> sink pipeline with no hidden buffering."""

    def __init__(self, transform: FrameTransform, sink: FrameSink):
        self.transform = transform
        self.sink = sink

    def run(self, source: Iterable[FramePacket | tuple[float, np.ndarray]]) -> PipelineStats:
        stats = PipelineStats()
        for item in source:
            stats.submitted += 1
            output = self.transform.process(_coerce_packet(item))
            stats.processed += 1
            if output is None:
                stats.scheduler_skipped += 1
                continue
            self.sink.consume(output)
            stats.emitted += 1
        return stats


class RealtimeBridge:
    """Bounded asynchronous bridge for callback-driven camera sources.

    The default ``drop_policy='latest'`` prevents latency growth when capture temporarily outruns
    processing: the oldest *queued* frame is discarded and the newest frame is retained. The frame
    currently being processed is never interrupted. Use ``drop_policy='block'`` only when every
    input frame must be processed and backpressure into the producer is acceptable.
    """

    def __init__(
        self,
        transform: FrameTransform,
        sink: FrameSink,
        *,
        queue_size: int = 1,
        drop_policy: DropPolicy = "latest",
        autostart: bool = True,
        stop_on_error: bool = True,
        on_error: Callable[[BaseException], None] | None = None,
        thread_name: str = "foveastream-bridge",
    ):
        if queue_size < 1:
            raise ValueError("queue_size must be >= 1")
        if drop_policy not in ("latest", "block"):
            raise ValueError("drop_policy must be 'latest' or 'block'")
        self.transform = transform
        self.sink = sink
        self.queue_size = int(queue_size)
        self.drop_policy: DropPolicy = drop_policy
        self.stop_on_error = bool(stop_on_error)
        self.on_error = on_error
        self.thread_name = thread_name

        self._queue: deque[FramePacket] = deque()
        self._cv = threading.Condition()
        self._closed = False
        self._drain_on_close = True
        self._thread: threading.Thread | None = None
        self._stats = PipelineStats()
        self._last_error: BaseException | None = None

        if autostart:
            self.start()

    @property
    def stats(self) -> PipelineStats:
        with self._cv:
            return replace(self._stats)

    @property
    def last_error(self) -> BaseException | None:
        with self._cv:
            return self._last_error

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._cv:
            if self._closed:
                raise RuntimeError("bridge is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker, name=self.thread_name, daemon=True)
            self._thread.start()

    def submit(
        self,
        frame_rgb: np.ndarray | FramePacket,
        timestamp_s: float | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        proposals: Sequence[RoiProposal] = (),
        points: Sequence[tuple[float, float]] = (),
        signals: InnovationSignals | None = None,
        timeout: float | None = None,
    ) -> bool:
        packet = frame_rgb if isinstance(frame_rgb, FramePacket) else FramePacket(
            frame_rgb=frame_rgb,
            timestamp_s=time.monotonic() if timestamp_s is None else float(timestamp_s),
            metadata=dict(metadata or {}),
            proposals=tuple(proposals),
            points=tuple(points),
            signals=signals,
        )

        with self._cv:
            if self._closed:
                raise RuntimeError("bridge is closed")
            self._stats.submitted += 1

            if self.drop_policy == "latest":
                if len(self._queue) >= self.queue_size:
                    self._queue.popleft()
                    self._stats.dropped_input += 1
                self._queue.append(packet)
            else:
                deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
                while len(self._queue) >= self.queue_size and not self._closed:
                    if deadline is None:
                        self._cv.wait()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._stats.dropped_input += 1
                            return False
                        self._cv.wait(remaining)
                if self._closed:
                    raise RuntimeError("bridge is closed")
                self._queue.append(packet)

            self._stats.max_queue_depth = max(self._stats.max_queue_depth, len(self._queue))
            self._cv.notify_all()
            return True

    def close(self, *, drain: bool = True, timeout: float | None = None) -> bool:
        with self._cv:
            if not self._closed:
                self._closed = True
                self._drain_on_close = bool(drain)
                if not drain:
                    self._stats.dropped_input += len(self._queue)
                    self._queue.clear()
                self._cv.notify_all()
            thread = self._thread

        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _worker(self) -> None:
        while True:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if self._closed and (not self._drain_on_close or not self._queue):
                    return
                packet = self._queue.popleft()
                self._cv.notify_all()

            try:
                output = self.transform.process(packet)
                with self._cv:
                    self._stats.processed += 1
                if output is None:
                    with self._cv:
                        self._stats.scheduler_skipped += 1
                    continue
                self.sink.consume(output)
                with self._cv:
                    self._stats.emitted += 1
            except BaseException as exc:
                callback = self.on_error
                with self._cv:
                    self._stats.errors += 1
                    self._last_error = exc
                    if self.stop_on_error:
                        self._closed = True
                        self._drain_on_close = False
                        self._stats.dropped_input += len(self._queue)
                        self._queue.clear()
                        self._cv.notify_all()
                if callback is not None:
                    callback(exc)
                if self.stop_on_error:
                    return
