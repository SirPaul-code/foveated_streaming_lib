#[derive(Clone, Copy, Debug)]
pub struct SchedulerConfig {
    pub threshold: f32,
    pub max_refresh_interval_s: f32,
    pub min_interval_s: f32,
    pub uncertainty_hard_limit: f32,
    pub pose_weight: f32,
    pub roi_weight: f32,
    pub flow_weight: f32,
    pub scene_weight: f32,
    pub semantic_weight: f32,
    pub age_weight: f32,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            threshold: 0.45,
            max_refresh_interval_s: 1.0,
            min_interval_s: 0.08,
            uncertainty_hard_limit: 0.18,
            pose_weight: 0.14,
            roi_weight: 0.24,
            flow_weight: 0.14,
            scene_weight: 0.18,
            semantic_weight: 0.20,
            age_weight: 0.10,
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct InnovationSignals {
    pub pose_novelty: f32,
    pub roi_prediction_error: f32,
    pub residual_motion: f32,
    pub scene_change: f32,
    pub semantic_uncertainty: f32,
    pub uncertainty_radius: f32,
    pub hard_trigger: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum SendReason {
    HardTrigger,
    MaxStaleness,
    UncertaintyLimit,
    Innovation,
    SuppressedMinInterval,
    BelowThreshold,
}

#[derive(Clone, Copy, Debug)]
pub struct SendDecision {
    pub send: bool,
    pub score: f32,
    pub reason: SendReason,
}

#[derive(Clone, Debug)]
pub struct AdaptiveScheduler {
    cfg: SchedulerConfig,
    time_since_send_s: f32,
}

impl AdaptiveScheduler {
    pub fn new(cfg: SchedulerConfig) -> Self {
        Self { cfg, time_since_send_s: f32::INFINITY }
    }

    pub fn time_since_send_s(&self) -> f32 { self.time_since_send_s }

    pub fn step(&mut self, dt_s: f32, s: InnovationSignals) -> SendDecision {
        self.time_since_send_s = (self.time_since_send_s + dt_s.max(0.0)).min(1.0e6);
        let age = if self.cfg.max_refresh_interval_s > 0.0 {
            (self.time_since_send_s / self.cfg.max_refresh_interval_s).clamp(0.0, 1.0)
        } else { 1.0 };

        let score = self.cfg.pose_weight * s.pose_novelty.clamp(0.0, 1.0)
            + self.cfg.roi_weight * s.roi_prediction_error.clamp(0.0, 1.0)
            + self.cfg.flow_weight * s.residual_motion.clamp(0.0, 1.0)
            + self.cfg.scene_weight * s.scene_change.clamp(0.0, 1.0)
            + self.cfg.semantic_weight * s.semantic_uncertainty.clamp(0.0, 1.0)
            + self.cfg.age_weight * age;

        let (mut send, mut reason) = if s.hard_trigger {
            (true, SendReason::HardTrigger)
        } else if self.time_since_send_s >= self.cfg.max_refresh_interval_s {
            (true, SendReason::MaxStaleness)
        } else if s.uncertainty_radius >= self.cfg.uncertainty_hard_limit {
            (true, SendReason::UncertaintyLimit)
        } else if score >= self.cfg.threshold {
            (true, SendReason::Innovation)
        } else {
            (false, SendReason::BelowThreshold)
        };

        if send && !s.hard_trigger && self.time_since_send_s < self.cfg.min_interval_s {
            send = false;
            reason = SendReason::SuppressedMinInterval;
        }

        if send { self.time_since_send_s = 0.0; }
        SendDecision { send, score, reason }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hard_trigger_sends() {
        let mut s = AdaptiveScheduler::new(SchedulerConfig::default());
        let d = s.step(0.01, InnovationSignals { hard_trigger: true, ..Default::default() });
        assert!(d.send);
        assert_eq!(d.reason, SendReason::HardTrigger);
    }

    #[test]
    fn max_staleness_forces_send() {
        let mut s = AdaptiveScheduler::new(SchedulerConfig { max_refresh_interval_s: 1.0, ..Default::default() });
        let _ = s.step(0.0, InnovationSignals::default());
        let d = s.step(1.01, InnovationSignals::default());
        assert!(d.send);
    }
}
