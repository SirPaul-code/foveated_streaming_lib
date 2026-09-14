use crate::types::{Point2, RoiRect};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum MotionMode {
    Fixation,
    Pursuit,
    Saccade,
    Lost,
}

#[derive(Clone, Copy, Debug)]
pub struct CameraIntrinsics {
    pub fx: f32,
    pub fy: f32,
    pub cx: f32,
    pub cy: f32,
    pub width: u32,
    pub height: u32,
}

impl CameraIntrinsics {
    pub fn from_fov(width: u32, height: u32, hfov_deg: f32, vfov_deg: f32) -> Self {
        let hfov = hfov_deg.to_radians().max(1.0e-3);
        let vfov = vfov_deg.to_radians().max(1.0e-3);
        Self {
            fx: 0.5 * width as f32 / (0.5 * hfov).tan(),
            fy: 0.5 * height as f32 / (0.5 * vfov).tan(),
            cx: 0.5 * (width.saturating_sub(1)) as f32,
            cy: 0.5 * (height.saturating_sub(1)) as f32,
            width,
            height,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct AttentionState {
    pub center: Point2,
    pub velocity: Point2,
    pub log_scale: f32,
    pub log_scale_velocity: f32,
    /// Variance in normalized image coordinates.
    pub var_x: f32,
    pub var_y: f32,
    pub cov_xy: f32,
    pub confidence: f32,
    pub mode: MotionMode,
    pub age_s: f32,
}

impl Default for AttentionState {
    fn default() -> Self {
        Self {
            center: Point2::new(0.5, 0.5),
            velocity: Point2::new(0.0, 0.0),
            log_scale: 0.0,
            log_scale_velocity: 0.0,
            var_x: 0.01,
            var_y: 0.01,
            cov_xy: 0.0,
            confidence: 0.0,
            mode: MotionMode::Lost,
            age_s: 0.0,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PredictionConfig {
    pub process_noise_fixation: f32,
    pub process_noise_pursuit: f32,
    pub process_noise_saccade: f32,
    pub process_noise_lost: f32,
    pub measurement_noise: f32,
    pub confidence_decay_per_s: f32,
    pub saccade_speed_threshold: f32,
    pub fixation_speed_threshold: f32,
    pub lost_confidence_threshold: f32,
    pub max_horizon_s: f32,
}

impl Default for PredictionConfig {
    fn default() -> Self {
        Self {
            process_noise_fixation: 2.0e-5,
            process_noise_pursuit: 2.5e-4,
            process_noise_saccade: 4.0e-3,
            process_noise_lost: 1.0e-2,
            measurement_noise: 6.0e-4,
            confidence_decay_per_s: 0.30,
            saccade_speed_threshold: 1.4,
            fixation_speed_threshold: 0.05,
            lost_confidence_threshold: 0.12,
            max_horizon_s: 1.5,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub struct AttentionMeasurement {
    pub center: Point2,
    pub confidence: f32,
    pub variance: f32,
    pub timestamp_s: f64,
}

#[derive(Clone, Debug)]
pub struct PredictiveAttentionFilter {
    pub state: AttentionState,
    cfg: PredictionConfig,
    last_timestamp_s: Option<f64>,
}

impl PredictiveAttentionFilter {
    pub fn new(cfg: PredictionConfig) -> Self {
        Self { state: AttentionState::default(), cfg, last_timestamp_s: None }
    }

    fn process_noise(&self) -> f32 {
        match self.state.mode {
            MotionMode::Fixation => self.cfg.process_noise_fixation,
            MotionMode::Pursuit => self.cfg.process_noise_pursuit,
            MotionMode::Saccade => self.cfg.process_noise_saccade,
            MotionMode::Lost => self.cfg.process_noise_lost,
        }
    }

    pub fn predict_by(&mut self, dt_s: f32) -> AttentionState {
        let dt = dt_s.clamp(0.0, self.cfg.max_horizon_s);
        self.state.center = Point2::new(
            self.state.center.x + self.state.velocity.x * dt,
            self.state.center.y + self.state.velocity.y * dt,
        ).clamped();
        self.state.log_scale += self.state.log_scale_velocity * dt;

        let q = self.process_noise() * (1.0 + dt * dt);
        self.state.var_x = (self.state.var_x + q).min(0.25);
        self.state.var_y = (self.state.var_y + q).min(0.25);
        self.state.confidence = (self.state.confidence - self.cfg.confidence_decay_per_s * dt).clamp(0.0, 1.0);
        self.state.age_s += dt;
        self.update_mode();
        self.state
    }

    pub fn predict_to(&mut self, timestamp_s: f64) -> AttentionState {
        if let Some(last) = self.last_timestamp_s {
            self.predict_by((timestamp_s - last).max(0.0) as f32);
        }
        self.last_timestamp_s = Some(timestamp_s);
        self.state
    }

    pub fn correct(&mut self, m: AttentionMeasurement) -> AttentionState {
        self.predict_to(m.timestamp_s);
        let z = m.center.clamped();
        let r = (self.cfg.measurement_noise + m.variance.max(1.0e-8)) / m.confidence.clamp(0.05, 1.0);

        let kx = self.state.var_x / (self.state.var_x + r);
        let ky = self.state.var_y / (self.state.var_y + r);
        let ex = z.x - self.state.center.x;
        let ey = z.y - self.state.center.y;

        let dt = self.state.age_s.max(1.0 / 240.0);
        self.state.center = Point2::new(
            self.state.center.x + kx * ex,
            self.state.center.y + ky * ey,
        ).clamped();

        let beta = 0.22 * m.confidence.clamp(0.0, 1.0);
        self.state.velocity = Point2::new(
            self.state.velocity.x + beta * ex / dt,
            self.state.velocity.y + beta * ey / dt,
        );

        self.state.var_x = ((1.0 - kx) * self.state.var_x).max(1.0e-8);
        self.state.var_y = ((1.0 - ky) * self.state.var_y).max(1.0e-8);
        self.state.confidence = (0.65 * self.state.confidence + 0.35 * m.confidence).clamp(0.0, 1.0);
        self.state.age_s = 0.0;
        self.update_mode();
        self.state
    }

    pub fn apply_flow_observation(&mut self, flow_dx_norm: f32, flow_dy_norm: f32, dt_s: f32, confidence: f32) {
        let dt = dt_s.max(1.0e-4);
        let c = confidence.clamp(0.0, 1.0);
        let vx = flow_dx_norm / dt;
        let vy = flow_dy_norm / dt;
        self.state.velocity.x = (1.0 - c) * self.state.velocity.x + c * vx;
        self.state.velocity.y = (1.0 - c) * self.state.velocity.y + c * vy;
        let reduce = 1.0 - 0.35 * c;
        self.state.var_x *= reduce;
        self.state.var_y *= reduce;
        self.update_mode();
    }

    /// Propagate the current image ray using a small camera rotation measured by the gyro.
    /// Positive yaw rotates camera to the right; positive pitch rotates camera upward.
    pub fn apply_gyro_rotation(
        &mut self,
        gyro_yaw_rad_s: f32,
        gyro_pitch_rad_s: f32,
        dt_s: f32,
        intr: CameraIntrinsics,
    ) {
        if intr.width == 0 || intr.height == 0 || intr.fx <= 0.0 || intr.fy <= 0.0 { return; }
        let px = self.state.center.x * (intr.width.saturating_sub(1) as f32);
        let py = self.state.center.y * (intr.height.saturating_sub(1) as f32);

        let mut x = (px - intr.cx) / intr.fx;
        let mut y = (py - intr.cy) / intr.fy;
        let mut z = 1.0_f32;

        let yaw = gyro_yaw_rad_s * dt_s;
        let pitch = gyro_pitch_rad_s * dt_s;
        let (sy, cy) = yaw.sin_cos();
        let (sp, cp) = pitch.sin_cos();

        let x1 = cy * x - sy * z;
        let z1 = sy * x + cy * z;
        x = x1;
        z = z1;
        let y1 = cp * y + sp * z;
        let z2 = -sp * y + cp * z;
        y = y1;
        z = z2.max(1.0e-4);

        let nx = (intr.fx * (x / z) + intr.cx) / (intr.width.saturating_sub(1).max(1) as f32);
        let ny = (intr.fy * (y / z) + intr.cy) / (intr.height.saturating_sub(1).max(1) as f32);
        self.state.center = Point2::new(nx, ny).clamped();
    }

    pub fn future_state(&self, horizon_s: f32) -> AttentionState {
        let mut out = self.state;
        let h = horizon_s.clamp(0.0, self.cfg.max_horizon_s);
        out.center = Point2::new(
            out.center.x + out.velocity.x * h,
            out.center.y + out.velocity.y * h,
        ).clamped();
        out.log_scale += out.log_scale_velocity * h;
        let q = self.process_noise() * (1.0 + h * h);
        out.var_x = (out.var_x + q).min(0.25);
        out.var_y = (out.var_y + q).min(0.25);
        out
    }

    pub fn uncertainty_roi(&self, base_width: f32, base_height: f32, sigma: f32, horizon_s: f32) -> RoiRect {
        let s = self.future_state(horizon_s);
        let half_w = 0.5 * base_width * s.log_scale.exp() + sigma.max(0.0) * s.var_x.sqrt();
        let half_h = 0.5 * base_height * s.log_scale.exp() + sigma.max(0.0) * s.var_y.sqrt();
        let x0 = (s.center.x - half_w).clamp(0.0, 1.0);
        let y0 = (s.center.y - half_h).clamp(0.0, 1.0);
        let x1 = (s.center.x + half_w).clamp(x0, 1.0);
        let y1 = (s.center.y + half_h).clamp(y0, 1.0);
        RoiRect {
            x: x0,
            y: y0,
            w: x1 - x0,
            h: y1 - y0,
            confidence: s.confidence.max(0.05),
        }
    }

    fn update_mode(&mut self) {
        if self.state.confidence < self.cfg.lost_confidence_threshold {
            self.state.mode = MotionMode::Lost;
            return;
        }
        let speed = (self.state.velocity.x * self.state.velocity.x + self.state.velocity.y * self.state.velocity.y).sqrt();
        self.state.mode = if speed >= self.cfg.saccade_speed_threshold {
            MotionMode::Saccade
        } else if speed <= self.cfg.fixation_speed_threshold {
            MotionMode::Fixation
        } else {
            MotionMode::Pursuit
        };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uncertainty_roi_expands_with_horizon() {
        let mut f = PredictiveAttentionFilter::new(PredictionConfig::default());
        f.state.confidence = 1.0;
        f.state.mode = MotionMode::Pursuit;
        let a = f.uncertainty_roi(0.1, 0.1, 2.0, 0.0);
        let b = f.uncertainty_roi(0.1, 0.1, 2.0, 0.8);
        assert!(b.w >= a.w);
        assert!(b.h >= a.h);
    }

    #[test]
    fn correction_moves_toward_measurement() {
        let mut f = PredictiveAttentionFilter::new(PredictionConfig::default());
        f.state.center = Point2::new(0.2, 0.2);
        f.state.confidence = 0.7;
        f.state.mode = MotionMode::Fixation;
        f.correct(AttentionMeasurement { center: Point2::new(0.8, 0.6), confidence: 1.0, variance: 1e-5, timestamp_s: 1.0 });
        assert!(f.state.center.x > 0.2);
        assert!(f.state.center.y > 0.2);
    }
}
