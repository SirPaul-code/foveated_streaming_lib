use crate::{
    FrameInput, InnovationSignals, Point2, ProcessResult, RoiProposal, StreamError, StreamRuntime,
    StreamRuntimeConfig, StreamSink,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EmitPolicy {
    /// Emit one optimized frame/result for every submitted input frame.
    /// This is the correct default for continuous camera -> encoder/video pipelines.
    EveryFrame,
    /// Emit only when the adaptive scheduler decides the frame is worth sending.
    /// This is useful for model/API request pipelines and event-driven consumers.
    WhenSend,
}

#[derive(Clone, Debug)]
pub struct StreamMiddleware {
    runtime: StreamRuntime,
    emit_policy: EmitPolicy,
}

impl StreamMiddleware {
    pub fn new(runtime: StreamRuntime, emit_policy: EmitPolicy) -> Self {
        Self { runtime, emit_policy }
    }

    pub fn from_config(cfg: StreamRuntimeConfig) -> Self {
        Self::new(StreamRuntime::new(cfg), EmitPolicy::EveryFrame)
    }

    pub fn aggressive() -> Self {
        Self::from_config(StreamRuntimeConfig::aggressive())
    }

    pub fn runtime(&self) -> &StreamRuntime { &self.runtime }
    pub fn runtime_mut(&mut self) -> &mut StreamRuntime { &mut self.runtime }
    pub fn emit_policy(&self) -> EmitPolicy { self.emit_policy }
    pub fn set_emit_policy(&mut self, policy: EmitPolicy) { self.emit_policy = policy; }
    pub fn reset(&mut self) { self.runtime.reset(); }

    pub fn process_rgb8(
        &mut self,
        frame: FrameInput<'_>,
        proposals: &[RoiProposal],
        points: &[Point2],
        signals: InnovationSignals,
    ) -> Result<Option<ProcessResult>, StreamError> {
        let result = self.runtime.process_rgb8(frame, proposals, points, signals)?;
        if self.emit_policy == EmitPolicy::WhenSend && !result.should_send() {
            Ok(None)
        } else {
            Ok(Some(result))
        }
    }

    pub fn process_into_sink<S: StreamSink>(
        &mut self,
        frame: FrameInput<'_>,
        proposals: &[RoiProposal],
        points: &[Point2],
        signals: InnovationSignals,
        sink: &mut S,
    ) -> Result<Option<Result<(), S::Error>>, StreamError> {
        match self.process_rgb8(frame, proposals, points, signals)? {
            Some(result) => Ok(Some(sink.consume(&result))),
            None => Ok(None),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct CountSink(usize);
    impl StreamSink for CountSink {
        type Error = ();
        fn consume(&mut self, _result: &ProcessResult) -> Result<(), Self::Error> {
            self.0 += 1;
            Ok(())
        }
    }

    #[test]
    fn every_frame_is_drop_in_safe() {
        let mut mw = StreamMiddleware::aggressive();
        let frame = vec![0u8; 32 * 24 * 3];
        for i in 0..3 {
            let out = mw.process_rgb8(
                FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: i as f64 / 60.0 },
                &[], &[], InnovationSignals::default(),
            ).unwrap();
            assert!(out.is_some());
        }
    }

    #[test]
    fn when_send_may_suppress_redundant_frames() {
        let mut mw = StreamMiddleware::new(
            StreamRuntime::new(StreamRuntimeConfig::default()),
            EmitPolicy::WhenSend,
        );
        let frame = vec![0u8; 32 * 24 * 3];
        let first = mw.process_rgb8(
            FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: 0.0 },
            &[], &[], InnovationSignals::default(),
        ).unwrap();
        let second = mw.process_rgb8(
            FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: 0.01 },
            &[], &[], InnovationSignals::default(),
        ).unwrap();
        assert!(first.is_some());
        assert!(second.is_none());
    }

    #[test]
    fn sink_only_receives_emitted_results() {
        let mut mw = StreamMiddleware::new(
            StreamRuntime::new(StreamRuntimeConfig::default()),
            EmitPolicy::WhenSend,
        );
        let frame = vec![0u8; 32 * 24 * 3];
        let mut sink = CountSink(0);
        let _ = mw.process_into_sink(
            FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: 0.0 },
            &[], &[], InnovationSignals::default(), &mut sink,
        ).unwrap();
        let second = mw.process_into_sink(
            FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: 0.01 },
            &[], &[], InnovationSignals::default(), &mut sink,
        ).unwrap();
        assert!(second.is_none());
        assert_eq!(sink.0, 1);
    }
}
