use std::collections::HashMap;

use crate::{RoiProposal, RoiRect, StreamRuntimeConfig};

#[derive(Clone, Copy, Debug, Default)]
pub struct LatencyBudget {
    pub capture_s: f32,
    pub analysis_s: f32,
    pub encode_s: f32,
    pub network_s: f32,
    pub decode_s: f32,
    pub consumer_s: f32,
    pub safety_s: f32,
    pub max_horizon_s: f32,
}

impl LatencyBudget {
    pub fn realtime_default() -> Self {
        Self { safety_s: 0.02, max_horizon_s: 0.50, ..Self::default() }
    }

    pub fn total_s(&self) -> f32 {
        let total = self.capture_s + self.analysis_s + self.encode_s + self.network_s
            + self.decode_s + self.consumer_s + self.safety_s;
        total.max(0.0).min(self.max_horizon_s.max(0.0))
    }

    pub fn apply_to_config(&self, cfg: &mut StreamRuntimeConfig) -> f32 {
        let h = self.total_s();
        cfg.tracker.prediction_horizon_s = h;
        h
    }
}

#[derive(Clone, Copy, Debug)]
pub struct AdaptiveBudgetConfig {
    pub target_bitrate_bps: Option<f64>,
    pub target_bytes_per_frame: Option<f64>,
    pub target_pixel_fraction: Option<f32>,
    pub kp: f32,
    pub ki: f32,
    pub ewma_alpha: f32,
    pub min_strength: f32,
    pub max_strength: f32,
}

impl Default for AdaptiveBudgetConfig {
    fn default() -> Self {
        Self {
            target_bitrate_bps: None,
            target_bytes_per_frame: None,
            target_pixel_fraction: None,
            kp: 0.30,
            ki: 0.035,
            ewma_alpha: 0.20,
            min_strength: 0.0,
            max_strength: 1.0,
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct AdaptiveBudgetState {
    pub strength: f32,
    pub ewma_bitrate_bps: f64,
    pub integral: f32,
    pub last_error: f32,
}

#[derive(Clone, Copy, Debug)]
pub struct AdaptiveBudgetController {
    pub config: AdaptiveBudgetConfig,
    pub state: AdaptiveBudgetState,
}

impl AdaptiveBudgetController {
    pub fn new(config: AdaptiveBudgetConfig, initial_strength: f32) -> Self {
        Self {
            config,
            state: AdaptiveBudgetState { strength: initial_strength.clamp(0.0, 1.0), ..Default::default() },
        }
    }

    fn step(&mut self, error: f32, dt_s: f32) -> f32 {
        let dt = dt_s.clamp(1.0e-4, 2.0);
        self.state.integral = (self.state.integral + error * dt).clamp(-4.0, 4.0);
        self.state.last_error = error;
        self.state.strength = (self.state.strength
            + self.config.kp * error
            + self.config.ki * self.state.integral)
            .clamp(self.config.min_strength, self.config.max_strength);
        self.state.strength
    }

    pub fn observe_encoder(&mut self, encoded_bytes: usize, duration_s: f32) -> f32 {
        let duration = duration_s.max(1.0e-6);
        let instant = encoded_bytes as f64 * 8.0 / duration as f64;
        let a = self.config.ewma_alpha.clamp(0.001, 1.0) as f64;
        self.state.ewma_bitrate_bps = if self.state.ewma_bitrate_bps <= 0.0 {
            instant
        } else {
            (1.0 - a) * self.state.ewma_bitrate_bps + a * instant
        };
        let target = self.config.target_bitrate_bps.or_else(|| {
            self.config.target_bytes_per_frame.map(|v| v * 8.0 / duration as f64)
        });
        match target {
            Some(t) if t > 0.0 => self.step(((self.state.ewma_bitrate_bps - t) / t) as f32, duration),
            _ => self.state.strength,
        }
    }

    pub fn observe_pixel_fraction(&mut self, fraction: f32, dt_s: f32) -> f32 {
        match self.config.target_pixel_fraction {
            Some(t) if t > 0.0 => self.step((fraction - t) / t, dt_s),
            _ => self.state.strength,
        }
    }

    pub fn apply_to_config(&self, cfg: &mut StreamRuntimeConfig) {
        let t = self.state.strength.clamp(0.0, 1.0);
        let lerp = |a: f32, b: f32| a + (b - a) * t;
        cfg.foveation.peripheral_scale = lerp(1.0 / 8.0, 1.0 / 24.0);
        cfg.foveation.falloff_x = lerp(0.28, 0.12);
        cfg.foveation.falloff_y = cfg.foveation.falloff_x;
        cfg.tracker.pixel_budget_fraction = lerp(0.30, 0.15);
        cfg.tracker.max_tracks = lerp(8.0, 4.0).round() as usize;
        cfg.context_scale = lerp(0.25, 0.10);
        cfg.quality_floor = lerp(0.04, 0.0);
        cfg.uncertainty_floor = lerp(0.08, 0.01);
        cfg.periphery_qp_delta = lerp(12.0, 22.0).round() as i8;
    }
}

#[derive(Clone, Debug)]
pub struct EvidenceRecord {
    pub source_id: u32,
    pub timestamp_s: f64,
    pub ttl_s: f32,
    pub weight: f32,
    pub proposals: Vec<RoiProposal>,
}

#[derive(Clone, Debug, Default)]
pub struct EvidenceBus {
    records: HashMap<u32, EvidenceRecord>,
}

impl EvidenceBus {
    pub fn publish(&mut self, record: EvidenceRecord) {
        self.records.insert(record.source_id, record);
    }

    pub fn clear(&mut self, source_id: Option<u32>) {
        if let Some(id) = source_id { self.records.remove(&id); } else { self.records.clear(); }
    }

    pub fn proposals_at(&mut self, timestamp_s: f64) -> Vec<RoiProposal> {
        self.records.retain(|_, r| {
            let age = timestamp_s - r.timestamp_s;
            age >= -1.0e-6 && age <= r.ttl_s.max(0.0) as f64
        });
        let mut out = Vec::new();
        for record in self.records.values() {
            for p in &record.proposals {
                let mut p = p.clone();
                p.priority *= record.weight.max(0.0);
                p.source_id = record.source_id;
                out.push(p);
            }
        }
        out
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct RegionSignature {
    pub mean_rgb: [f32; 3],
    pub samples: [f32; 16],
}

impl RegionSignature {
    pub fn difference(&self, other: &Self) -> f32 {
        let mut total = 0.0f32;
        for i in 0..3 { total += (self.mean_rgb[i] - other.mean_rgb[i]).abs(); }
        for i in 0..16 { total += (self.samples[i] - other.samples[i]).abs(); }
        total / 19.0
    }
}

pub fn signature_rgb8(rgb: &[u8], width: usize, height: usize, roi: RoiRect) -> RegionSignature {
    if width == 0 || height == 0 || rgb.len() < width.saturating_mul(height).saturating_mul(3) {
        return RegionSignature::default();
    }
    let r = roi.normalized();
    let x0 = ((r.x * width as f32).floor() as usize).min(width - 1);
    let y0 = ((r.y * height as f32).floor() as usize).min(height - 1);
    let x1 = (((r.x + r.w) * width as f32).ceil() as usize).clamp(x0 + 1, width);
    let y1 = (((r.y + r.h) * height as f32).ceil() as usize).clamp(y0 + 1, height);
    let mut mean = [0.0f32; 3];
    let mut count = 0usize;
    for y in y0..y1 {
        for x in x0..x1 {
            let o = (y * width + x) * 3;
            mean[0] += rgb[o] as f32 / 255.0;
            mean[1] += rgb[o + 1] as f32 / 255.0;
            mean[2] += rgb[o + 2] as f32 / 255.0;
            count += 1;
        }
    }
    if count > 0 {
        for c in &mut mean { *c /= count as f32; }
    }
    let mut samples = [0.0f32; 16];
    for sy in 0..4 {
        for sx in 0..4 {
            let fx = (sx as f32 + 0.5) / 4.0;
            let fy = (sy as f32 + 0.5) / 4.0;
            let x = (x0 as f32 + fx * (x1 - x0) as f32).floor() as usize;
            let y = (y0 as f32 + fy * (y1 - y0) as f32).floor() as usize;
            let x = x.min(width - 1); let y = y.min(height - 1);
            let o = (y * width + x) * 3;
            samples[sy * 4 + sx] = (0.2126 * rgb[o] as f32 + 0.7152 * rgb[o + 1] as f32 + 0.0722 * rgb[o + 2] as f32) / 255.0;
        }
    }
    RegionSignature { mean_rgb: mean, samples }
}

#[derive(Clone, Copy, Debug)]
pub struct TemporalCacheConfig {
    pub change_threshold: f32,
    pub max_refresh_s: f32,
}

impl Default for TemporalCacheConfig {
    fn default() -> Self { Self { change_threshold: 0.055, max_refresh_s: 1.5 } }
}

#[derive(Clone, Copy, Debug)]
pub struct CacheDecision {
    pub changed: bool,
    pub change_score: f32,
    pub age_s: f32,
}

#[derive(Clone, Copy, Debug)]
struct CachedRegion {
    signature: RegionSignature,
    last_sent_s: f64,
}

#[derive(Clone, Debug, Default)]
pub struct TemporalRegionCache {
    pub config: TemporalCacheConfig,
    entries: HashMap<u64, CachedRegion>,
}

impl TemporalRegionCache {
    pub fn new(config: TemporalCacheConfig) -> Self { Self { config, entries: HashMap::new() } }
    pub fn clear(&mut self) { self.entries.clear(); }

    pub fn observe(&mut self, key: u64, signature: RegionSignature, timestamp_s: f64) -> CacheDecision {
        let (score, age, changed) = match self.entries.get(&key) {
            None => (1.0, f32::INFINITY, true),
            Some(old) => {
                let score = signature.difference(&old.signature);
                let age = (timestamp_s - old.last_sent_s).max(0.0) as f32;
                (score, age, score >= self.config.change_threshold || age >= self.config.max_refresh_s)
            }
        };
        if changed {
            self.entries.insert(key, CachedRegion { signature, last_sent_s: timestamp_s });
        }
        CacheDecision { changed, change_score: score, age_s: age }
    }
}

#[derive(Clone, Debug)]
pub struct EncoderSpatialHints {
    pub block_size: usize,
    pub qp_delta_map: Vec<i8>,
    pub rois: Vec<RoiRect>,
    pub fovea_qp_delta: i8,
    pub periphery_qp_delta: i8,
}

impl EncoderSpatialHints {
    pub fn to_u8_bytes(&self) -> Vec<u8> {
        self.qp_delta_map.iter().map(|v| *v as u8).collect()
    }
}

pub fn analysis_dimensions(width: usize, height: usize, analysis_width: usize) -> (usize, usize) {
    if width == 0 || height == 0 { return (0, 0); }
    let aw = analysis_width.max(32).min(width);
    let ah = ((height as f64 * aw as f64 / width as f64).round() as usize).max(1);
    (aw, ah)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{RoiProposal, RoiRect};

    #[test]
    fn latency_budget_sets_prediction_horizon() {
        let mut cfg = StreamRuntimeConfig::aggressive();
        let b = LatencyBudget { encode_s: 0.03, network_s: 0.07, consumer_s: 0.02, safety_s: 0.01, max_horizon_s: 0.5, ..Default::default() };
        let h = b.apply_to_config(&mut cfg);
        assert!((h - 0.13).abs() < 1.0e-6);
        assert!((cfg.tracker.prediction_horizon_s - 0.13).abs() < 1.0e-6);
    }

    #[test]
    fn controller_increases_strength_when_over_target() {
        let mut c = AdaptiveBudgetController::new(
            AdaptiveBudgetConfig { target_bitrate_bps: Some(1_000_000.0), kp: 0.25, ki: 0.0, ..Default::default() },
            0.2,
        );
        c.observe_encoder(250_000, 1.0);
        assert!(c.state.strength > 0.2);
        let mut cfg = StreamRuntimeConfig::balanced();
        c.apply_to_config(&mut cfg);
        assert!(cfg.context_scale < 0.25);
        assert!(cfg.tracker.pixel_budget_fraction < 0.30);
    }

    #[test]
    fn evidence_bus_expires_old_sources() {
        let mut bus = EvidenceBus::default();
        bus.publish(EvidenceRecord {
            source_id: 7, timestamp_s: 1.0, ttl_s: 0.5, weight: 2.0,
            proposals: vec![RoiProposal::new(RoiRect { x: 0.1, y: 0.1, w: 0.2, h: 0.2, confidence: 1.0 }, 1.0)],
        });
        let active = bus.proposals_at(1.1);
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].source_id, 7);
        assert_eq!(active[0].priority, 2.0);
        assert!(bus.proposals_at(1.7).is_empty());
    }

    #[test]
    fn temporal_cache_skips_unchanged_signature() {
        let mut cache = TemporalRegionCache::new(TemporalCacheConfig { change_threshold: 0.02, max_refresh_s: 10.0 });
        let sig = RegionSignature { mean_rgb: [0.1, 0.2, 0.3], samples: [0.2; 16] };
        assert!(cache.observe(1, sig, 0.0).changed);
        assert!(!cache.observe(1, sig, 0.1).changed);
        let changed = RegionSignature { mean_rgb: [0.9, 0.2, 0.3], samples: [0.8; 16] };
        assert!(cache.observe(1, changed, 0.2).changed);
    }

    #[test]
    fn signature_detects_region_change() {
        let mut a = vec![0u8; 16 * 16 * 3];
        let mut b = a.clone();
        for px in b.chunks_mut(3) { px.copy_from_slice(&[255, 255, 255]); }
        let roi = RoiRect { x: 0.0, y: 0.0, w: 1.0, h: 1.0, confidence: 1.0 };
        let sa = signature_rgb8(&a, 16, 16, roi);
        let sb = signature_rgb8(&b, 16, 16, roi);
        assert!(sa.difference(&sb) > 0.5);
        a[0] = 1;
    }

    #[test]
    fn low_res_analysis_preserves_aspect_ratio() {
        assert_eq!(analysis_dimensions(1920, 1080, 256), (256, 144));
    }
}
