use crate::{
    context_and_roi_views, foveate_rgb8_with_map, qp_delta_map, quality_map,
    AdaptiveScheduler, ImageView, InnovationSignals, MultiRoiTracker, Point2,
    RoiProposal, RoiRect, RoiTrackerConfig, SchedulerConfig, SendDecision,
};
use crate::types::{Falloff, FoveationConfig};

#[derive(Clone, Copy, Debug)]
pub struct StreamRuntimeConfig {
    pub foveation: FoveationConfig,
    pub tracker: RoiTrackerConfig,
    pub scheduler: SchedulerConfig,
    pub context_scale: f32,
    pub quality_floor: f32,
    pub uncertainty_floor: f32,
    pub roi_max_side: usize,
    pub qp_block: usize,
    pub fovea_qp_delta: i8,
    pub periphery_qp_delta: i8,
    pub produce_foveated_rgb: bool,
    pub produce_context_roi_views: bool,
    pub produce_qp_map: bool,
}

impl StreamRuntimeConfig {
    pub fn balanced() -> Self {
        Self {
            foveation: FoveationConfig { peripheral_scale: 1.0 / 8.0, ..FoveationConfig::default() },
            tracker: RoiTrackerConfig { max_tracks: 8, pixel_budget_fraction: 0.30, ..RoiTrackerConfig::default() },
            scheduler: SchedulerConfig::default(),
            context_scale: 0.25,
            quality_floor: 0.04,
            uncertainty_floor: 0.08,
            roi_max_side: 512,
            qp_block: 16,
            fovea_qp_delta: -4,
            periphery_qp_delta: 12,
            produce_foveated_rgb: true,
            produce_context_roi_views: true,
            produce_qp_map: true,
        }
    }

    pub fn aggressive() -> Self {
        let mut cfg = Self::balanced();
        cfg.foveation.peripheral_scale = 1.0 / 16.0;
        cfg.foveation.falloff_x = 0.18;
        cfg.foveation.falloff_y = 0.18;
        cfg.foveation.falloff = Falloff::Gaussian;
        cfg.tracker.max_tracks = 6;
        cfg.tracker.pixel_budget_fraction = 0.22;
        cfg.context_scale = 0.15;
        cfg.quality_floor = 0.01;
        cfg.uncertainty_floor = 0.02;
        cfg
    }

    pub fn extreme() -> Self {
        let mut cfg = Self::aggressive();
        cfg.foveation.peripheral_scale = 1.0 / 24.0;
        cfg.foveation.falloff_x = 0.12;
        cfg.foveation.falloff_y = 0.12;
        cfg.tracker.max_tracks = 4;
        cfg.tracker.pixel_budget_fraction = 0.15;
        cfg.context_scale = 0.10;
        cfg.quality_floor = 0.0;
        cfg.uncertainty_floor = 0.01;
        cfg
    }

    pub fn preset(name: &str) -> Option<Self> {
        match name.to_ascii_lowercase().as_str() {
            "balanced" => Some(Self::balanced()),
            "aggressive" => Some(Self::aggressive()),
            "extreme" => Some(Self::extreme()),
            _ => None,
        }
    }
}

impl Default for StreamRuntimeConfig {
    fn default() -> Self { Self::balanced() }
}

#[derive(Clone, Copy, Debug)]
pub struct FrameInput<'a> {
    pub rgb8: &'a [u8],
    pub width: usize,
    pub height: usize,
    pub timestamp_s: f64,
}

#[derive(Clone, Debug)]
pub struct ProcessResult {
    pub timestamp_s: f64,
    pub width: usize,
    pub height: usize,
    pub decision: SendDecision,
    pub rois: Vec<RoiRect>,
    pub quality_map: Vec<f32>,
    pub foveated_rgb8: Vec<u8>,
    pub views: Vec<ImageView>,
    pub qp_delta_map: Vec<i8>,
}

impl ProcessResult {
    pub fn should_send(&self) -> bool { self.decision.send }
    pub fn context_view(&self) -> Option<&ImageView> { self.views.first() }
    pub fn roi_views(&self) -> &[ImageView] {
        if self.views.len() <= 1 { &[] } else { &self.views[1..] }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamError {
    InvalidDimensions,
    InvalidRgbLength,
    NonFiniteTimestamp,
    TimestampMovedBackwards,
}

pub trait StreamSink {
    type Error;
    fn consume(&mut self, result: &ProcessResult) -> Result<(), Self::Error>;
}

#[derive(Clone, Debug)]
pub struct StreamRuntime {
    cfg: StreamRuntimeConfig,
    tracker: MultiRoiTracker,
    scheduler: AdaptiveScheduler,
    last_timestamp_s: Option<f64>,
}

impl StreamRuntime {
    pub fn new(cfg: StreamRuntimeConfig) -> Self {
        Self {
            tracker: MultiRoiTracker::new(cfg.tracker),
            scheduler: AdaptiveScheduler::new(cfg.scheduler),
            cfg,
            last_timestamp_s: None,
        }
    }

    pub fn config(&self) -> StreamRuntimeConfig { self.cfg }
    pub fn tracker(&self) -> &MultiRoiTracker { &self.tracker }
    pub fn reset(&mut self) {
        self.tracker.clear();
        self.scheduler = AdaptiveScheduler::new(self.cfg.scheduler);
        self.last_timestamp_s = None;
    }

    pub fn process_rgb8(
        &mut self,
        frame: FrameInput<'_>,
        proposals: &[RoiProposal],
        points: &[Point2],
        mut signals: InnovationSignals,
    ) -> Result<ProcessResult, StreamError> {
        if frame.width == 0 || frame.height == 0 { return Err(StreamError::InvalidDimensions); }
        let expected = frame.width.checked_mul(frame.height).and_then(|v| v.checked_mul(3)).ok_or(StreamError::InvalidDimensions)?;
        if frame.rgb8.len() < expected { return Err(StreamError::InvalidRgbLength); }
        if !frame.timestamp_s.is_finite() { return Err(StreamError::NonFiniteTimestamp); }

        let dt = match self.last_timestamp_s {
            None => 0.0,
            Some(last) if frame.timestamp_s + 1.0e-9 < last => return Err(StreamError::TimestampMovedBackwards),
            Some(last) => (frame.timestamp_s - last).max(0.0) as f32,
        };
        self.last_timestamp_s = Some(frame.timestamp_s);

        let rois = self.tracker.update(dt, proposals);
        let mean_conf = if !self.tracker.tracks().is_empty() {
            self.tracker.tracks().iter().map(|t| t.confidence).sum::<f32>() / self.tracker.tracks().len() as f32
        } else if !points.is_empty() { 1.0 } else { 0.0 };
        let mut quality = quality_map(frame.width, frame.height, points, &rois, &self.cfg.foveation);
        let floor = (self.cfg.quality_floor + self.cfg.uncertainty_floor * (1.0 - mean_conf)).clamp(0.0, 1.0);
        for q in &mut quality { *q = q.max(floor).min(1.0); }

        if signals.uncertainty_radius <= 0.0 {
            signals.uncertainty_radius = self.tracker.tracks().iter()
                .map(|t| t.stale_s * self.cfg.tracker.uncertainty_growth_per_s)
                .fold(0.0f32, f32::max);
        }
        if signals.semantic_uncertainty <= 0.0 && !self.tracker.tracks().is_empty() {
            signals.semantic_uncertainty = (1.0 - mean_conf).clamp(0.0, 1.0);
        }
        let decision = self.scheduler.step(dt, signals);

        let foveated_rgb8 = if self.cfg.produce_foveated_rgb {
            foveate_rgb8_with_map(frame.rgb8, frame.width, frame.height, &quality, &self.cfg.foveation)
        } else { Vec::new() };
        let views = if self.cfg.produce_context_roi_views {
            context_and_roi_views(
                frame.rgb8, frame.width, frame.height, &rois,
                self.cfg.context_scale, self.cfg.roi_max_side,
            )
        } else { Vec::new() };
        let qp_delta = if self.cfg.produce_qp_map {
            qp_delta_map(
                &quality, frame.width, frame.height, self.cfg.qp_block.max(1),
                self.cfg.fovea_qp_delta, self.cfg.periphery_qp_delta,
            )
        } else { Vec::new() };

        Ok(ProcessResult {
            timestamp_s: frame.timestamp_s,
            width: frame.width,
            height: frame.height,
            decision,
            rois,
            quality_map: quality,
            foveated_rgb8,
            views,
            qp_delta_map: qp_delta,
        })
    }

    pub fn process_into_sink<S: StreamSink>(
        &mut self,
        frame: FrameInput<'_>,
        proposals: &[RoiProposal],
        points: &[Point2],
        signals: InnovationSignals,
        sink: &mut S,
    ) -> Result<Result<(), S::Error>, StreamError> {
        let result = self.process_rgb8(frame, proposals, points, signals)?;
        Ok(sink.consume(&result))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct CountingSink { calls: usize, sent: usize }
    impl StreamSink for CountingSink {
        type Error = ();
        fn consume(&mut self, result: &ProcessResult) -> Result<(), Self::Error> {
            self.calls += 1;
            if result.should_send() { self.sent += 1; }
            Ok(())
        }
    }

    #[test]
    fn runtime_returns_multiple_roi_views() {
        let mut rt = StreamRuntime::new(StreamRuntimeConfig::aggressive());
        let frame = vec![127u8; 64 * 48 * 3];
        let proposals = [
            RoiProposal::new(RoiRect { x: .1, y: .1, w: .15, h: .2, confidence: 1.0 }, 1.0),
            RoiProposal::new(RoiRect { x: .65, y: .55, w: .15, h: .2, confidence: 1.0 }, 1.0),
        ];
        let out = rt.process_rgb8(
            FrameInput { rgb8: &frame, width: 64, height: 48, timestamp_s: 0.0 },
            &proposals, &[], InnovationSignals::default(),
        ).unwrap();
        assert_eq!(out.rois.len(), 2);
        assert_eq!(out.roi_views().len(), 2);
        assert_eq!(out.quality_map.len(), 64 * 48);
        assert!((rt.config().context_scale - 0.15).abs() < 1.0e-6);
    }

    #[test]
    fn sink_receives_frame_results() {
        let mut rt = StreamRuntime::new(StreamRuntimeConfig::default());
        let frame = vec![0u8; 32 * 24 * 3];
        let mut sink = CountingSink { calls: 0, sent: 0 };
        let r = rt.process_into_sink(
            FrameInput { rgb8: &frame, width: 32, height: 24, timestamp_s: 0.0 },
            &[], &[], InnovationSignals::default(), &mut sink,
        ).unwrap();
        assert!(r.is_ok());
        assert_eq!(sink.calls, 1);
    }
}
