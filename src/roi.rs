use crate::types::{Point2, RoiRect};

#[derive(Clone, Copy, Debug)]
pub struct RoiTrackerConfig {
    pub max_tracks: usize,
    pub association_iou: f32,
    pub association_center_distance: f32,
    pub smoothing: f32,
    pub velocity_smoothing: f32,
    pub max_stale_s: f32,
    pub confidence_decay_per_s: f32,
    pub uncertainty_growth_per_s: f32,
    pub prediction_horizon_s: f32,
    pub min_confidence: f32,
    pub proposal_merge_iou: f32,
    pub pixel_budget_fraction: f32,
}

impl Default for RoiTrackerConfig {
    fn default() -> Self {
        Self {
            max_tracks: 8,
            association_iou: 0.12,
            association_center_distance: 0.18,
            smoothing: 0.55,
            velocity_smoothing: 0.35,
            max_stale_s: 0.75,
            confidence_decay_per_s: 0.45,
            uncertainty_growth_per_s: 0.08,
            prediction_horizon_s: 0.12,
            min_confidence: 0.05,
            proposal_merge_iou: 0.55,
            pixel_budget_fraction: 0.30,
        }
    }
}

#[derive(Clone, Debug)]
pub struct RoiProposal {
    pub roi: RoiRect,
    pub confidence: f32,
    pub priority: f32,
    pub source_id: u32,
    pub track_hint: Option<u64>,
}

impl RoiProposal {
    pub fn new(roi: RoiRect, confidence: f32) -> Self {
        Self {
            roi,
            confidence: confidence.clamp(0.0, 1.0),
            priority: 1.0,
            source_id: 0,
            track_hint: None,
        }
    }

    pub fn score(&self) -> f32 {
        self.confidence.clamp(0.0, 1.0) * self.priority.max(0.0)
    }
}

#[derive(Clone, Debug)]
pub struct RoiTrack {
    pub id: u64,
    pub roi: RoiRect,
    pub velocity: [f32; 4],
    pub confidence: f32,
    pub priority: f32,
    pub source_id: u32,
    pub age_s: f32,
    pub stale_s: f32,
}

impl RoiTrack {
    pub fn center(&self) -> Point2 {
        let r = self.roi.normalized();
        Point2::new(r.x + r.w * 0.5, r.y + r.h * 0.5)
    }

    fn score(&self) -> f32 {
        self.confidence.clamp(0.0, 1.0) * self.priority.max(0.0)
    }
}

#[derive(Clone, Debug)]
pub struct MultiRoiTracker {
    cfg: RoiTrackerConfig,
    tracks: Vec<RoiTrack>,
    next_id: u64,
}

impl MultiRoiTracker {
    pub fn new(cfg: RoiTrackerConfig) -> Self {
        Self { cfg, tracks: Vec::new(), next_id: 1 }
    }

    pub fn config(&self) -> RoiTrackerConfig { self.cfg }
    pub fn tracks(&self) -> &[RoiTrack] { &self.tracks }
    pub fn clear(&mut self) { self.tracks.clear(); }

    pub fn update(&mut self, dt_s: f32, proposals: &[RoiProposal]) -> Vec<RoiRect> {
        let dt = dt_s.max(0.0).min(1.0);
        self.predict_existing(dt);
        let proposals = dedupe_proposals(proposals, self.cfg.proposal_merge_iou);
        let mut used = vec![false; self.tracks.len()];

        for p in proposals {
            let normalized = p.roi.normalized();
            if normalized.w <= 0.0 || normalized.h <= 0.0 || p.confidence <= 0.0 { continue; }

            let mut best: Option<(usize, f32)> = None;
            for (i, track) in self.tracks.iter().enumerate() {
                if used[i] { continue; }
                if let Some(hint) = p.track_hint {
                    if hint == track.id { best = Some((i, 10.0)); break; }
                }
                let overlap = iou(track.roi, normalized);
                let dist = center_distance(track.roi, normalized);
                if overlap < self.cfg.association_iou && dist > self.cfg.association_center_distance { continue; }
                let compatibility = overlap * 2.0 + (1.0 - dist.min(1.0)) + 0.25 * p.score();
                if best.map(|(_, s)| compatibility > s).unwrap_or(true) {
                    best = Some((i, compatibility));
                }
            }

            if let Some((idx, _)) = best {
                self.correct_track(idx, dt.max(1.0e-4), &p);
                used[idx] = true;
            } else if self.tracks.len() < self.cfg.max_tracks.max(1) {
                self.tracks.push(RoiTrack {
                    id: self.next_id,
                    roi: RoiRect { confidence: p.confidence.clamp(0.0, 1.0), ..normalized },
                    velocity: [0.0; 4],
                    confidence: p.confidence.clamp(0.0, 1.0),
                    priority: p.priority.max(0.0),
                    source_id: p.source_id,
                    age_s: 0.0,
                    stale_s: 0.0,
                });
                self.next_id = self.next_id.saturating_add(1);
                used.push(true);
            }
        }

        self.tracks.retain(|t| t.stale_s <= self.cfg.max_stale_s && t.confidence >= self.cfg.min_confidence);
        self.tracks.sort_by(|a, b| b.score().partial_cmp(&a.score()).unwrap_or(std::cmp::Ordering::Equal));
        self.tracks.truncate(self.cfg.max_tracks.max(1));
        self.active_rois()
    }

    pub fn active_rois(&self) -> Vec<RoiRect> {
        let mut ranked: Vec<&RoiTrack> = self.tracks.iter().collect();
        ranked.sort_by(|a, b| b.score().partial_cmp(&a.score()).unwrap_or(std::cmp::Ordering::Equal));
        let budget = self.cfg.pixel_budget_fraction.clamp(0.0, 1.0);
        let mut area = 0.0f32;
        let mut out = Vec::new();
        for track in ranked.into_iter().take(self.cfg.max_tracks.max(1)) {
            let r = self.expanded_predicted_roi(track);
            let next_area = (r.w * r.h).clamp(0.0, 1.0);
            if !out.is_empty() && budget > 0.0 && area + next_area > budget { continue; }
            area += next_area;
            out.push(r);
        }
        out
    }

    fn predict_existing(&mut self, dt: f32) {
        for t in &mut self.tracks {
            t.age_s += dt;
            t.stale_s += dt;
            let r = t.roi.normalized();
            t.roi = RoiRect {
                x: r.x + t.velocity[0] * dt,
                y: r.y + t.velocity[1] * dt,
                w: (r.w + t.velocity[2] * dt).max(0.005),
                h: (r.h + t.velocity[3] * dt).max(0.005),
                confidence: t.confidence,
            }.normalized();
            t.confidence = (t.confidence - self.cfg.confidence_decay_per_s.max(0.0) * dt).clamp(0.0, 1.0);
            t.roi.confidence = t.confidence;
        }
    }

    fn correct_track(&mut self, idx: usize, dt: f32, p: &RoiProposal) {
        let alpha = self.cfg.smoothing.clamp(0.0, 1.0);
        let beta = self.cfg.velocity_smoothing.clamp(0.0, 1.0);
        let old = self.tracks[idx].roi.normalized();
        let z = p.roi.normalized();
        let observed = [
            (z.x - old.x) / dt,
            (z.y - old.y) / dt,
            (z.w - old.w) / dt,
            (z.h - old.h) / dt,
        ];
        for (v, obs) in self.tracks[idx].velocity.iter_mut().zip(observed) {
            *v = *v * (1.0 - beta) + obs * beta;
        }
        let blend = |a: f32, b: f32| a * (1.0 - alpha) + b * alpha;
        self.tracks[idx].roi = RoiRect {
            x: blend(old.x, z.x), y: blend(old.y, z.y),
            w: blend(old.w, z.w), h: blend(old.h, z.h),
            confidence: p.confidence.clamp(0.0, 1.0),
        }.normalized();
        self.tracks[idx].confidence = (0.35 * self.tracks[idx].confidence + 0.65 * p.confidence).clamp(0.0, 1.0);
        self.tracks[idx].roi.confidence = self.tracks[idx].confidence;
        self.tracks[idx].priority = self.tracks[idx].priority.max(p.priority.max(0.0));
        self.tracks[idx].source_id = p.source_id;
        self.tracks[idx].stale_s = 0.0;
    }

    fn expanded_predicted_roi(&self, track: &RoiTrack) -> RoiRect {
        let h = self.cfg.prediction_horizon_s.max(0.0);
        let r = track.roi.normalized();
        let px = r.x + track.velocity[0] * h;
        let py = r.y + track.velocity[1] * h;
        let pw = (r.w + track.velocity[2] * h).max(0.005);
        let ph = (r.h + track.velocity[3] * h).max(0.005);
        let speed = (track.velocity[0] * track.velocity[0] + track.velocity[1] * track.velocity[1]).sqrt();
        let margin = self.cfg.uncertainty_growth_per_s.max(0.0) * track.stale_s + speed * h * 0.35;
        RoiRect {
            x: px - margin,
            y: py - margin,
            w: pw + 2.0 * margin,
            h: ph + 2.0 * margin,
            confidence: track.confidence,
        }.normalized()
    }
}

pub fn iou(a: RoiRect, b: RoiRect) -> f32 {
    let a = a.normalized(); let b = b.normalized();
    let x0 = a.x.max(b.x); let y0 = a.y.max(b.y);
    let x1 = (a.x + a.w).min(b.x + b.w); let y1 = (a.y + a.h).min(b.y + b.h);
    let inter = (x1 - x0).max(0.0) * (y1 - y0).max(0.0);
    let union = a.w * a.h + b.w * b.h - inter;
    if union <= 1.0e-9 { 0.0 } else { inter / union }
}

fn center_distance(a: RoiRect, b: RoiRect) -> f32 {
    let a = a.normalized(); let b = b.normalized();
    let ax = a.x + a.w * 0.5; let ay = a.y + a.h * 0.5;
    let bx = b.x + b.w * 0.5; let by = b.y + b.h * 0.5;
    ((ax - bx).powi(2) + (ay - by).powi(2)).sqrt()
}

fn dedupe_proposals(proposals: &[RoiProposal], threshold: f32) -> Vec<RoiProposal> {
    let mut sorted = proposals.to_vec();
    sorted.sort_by(|a, b| b.score().partial_cmp(&a.score()).unwrap_or(std::cmp::Ordering::Equal));
    let mut out: Vec<RoiProposal> = Vec::new();
    'candidate: for p in sorted {
        for kept in &mut out {
            if iou(kept.roi, p.roi) >= threshold.clamp(0.0, 1.0) {
                if p.score() > kept.score() { *kept = p; }
                continue 'candidate;
            }
        }
        out.push(p);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn proposal(x: f32, y: f32) -> RoiProposal {
        RoiProposal::new(RoiRect { x, y, w: 0.12, h: 0.12, confidence: 1.0 }, 1.0)
    }

    #[test]
    fn keeps_multiple_independent_regions() {
        let mut t = MultiRoiTracker::new(RoiTrackerConfig::default());
        let out = t.update(1.0 / 30.0, &[proposal(0.1, 0.2), proposal(0.7, 0.6)]);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn associates_moving_region_without_new_track() {
        let mut t = MultiRoiTracker::new(RoiTrackerConfig::default());
        let _ = t.update(1.0 / 30.0, &[proposal(0.2, 0.2)]);
        let id = t.tracks()[0].id;
        let _ = t.update(1.0 / 30.0, &[proposal(0.22, 0.2)]);
        assert_eq!(t.tracks().len(), 1);
        assert_eq!(t.tracks()[0].id, id);
    }

    #[test]
    fn dedupes_overlapping_sources() {
        let mut b = proposal(0.105, 0.205); b.source_id = 2;
        let mut t = MultiRoiTracker::new(RoiTrackerConfig::default());
        let out = t.update(1.0 / 30.0, &[proposal(0.1, 0.2), b]);
        assert_eq!(out.len(), 1);
    }
}
