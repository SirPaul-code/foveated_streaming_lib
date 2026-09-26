#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TileCurve {
    Linear,
    SmoothStep,
    Gaussian,
    Exponential,
    Power,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TileAggregation {
    Mean,
    Max,
    P90,
}

#[derive(Clone, Debug)]
pub struct TilePlannerConfig {
    pub target_tiles: usize,
    pub curve: TileCurve,
    pub curve_strength: f32,
    pub aggregation: TileAggregation,
    pub min_quality: f32,
    pub min_resolution_scale: f32,
    pub fovea_qp_delta: i8,
    pub periphery_qp_delta: i8,
}

impl Default for TilePlannerConfig {
    fn default() -> Self {
        Self {
            target_tiles: 100,
            curve: TileCurve::Gaussian,
            curve_strength: 3.0,
            aggregation: TileAggregation::Max,
            min_quality: 0.04,
            min_resolution_scale: 0.125,
            fovea_qp_delta: -4,
            periphery_qp_delta: 18,
        }
    }
}

#[derive(Clone, Debug)]
pub struct TileDecision {
    pub index: usize,
    pub row: usize,
    pub column: usize,
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
    pub relevance: f32,
    pub distance: f32,
    pub quality: f32,
    pub resolution_scale: f32,
    pub qp_delta: i8,
}

impl TileDecision {
    pub fn area_fraction(&self) -> f32 {
        self.w * self.h
    }
}

#[derive(Clone, Debug)]
pub struct TilePlan {
    pub requested_tiles: usize,
    pub tiles: Vec<TileDecision>,
}

impl TilePlan {
    pub fn effective_pixel_fraction(&self) -> f32 {
        self.tiles
            .iter()
            .map(|t| t.area_fraction() * t.resolution_scale * t.resolution_scale)
            .sum()
    }

    pub fn mean_quality(&self) -> f32 {
        if self.tiles.is_empty() {
            0.0
        } else {
            self.tiles.iter().map(|t| t.quality).sum::<f32>() / self.tiles.len() as f32
        }
    }
}

fn row_counts(target_tiles: usize, aspect: f32) -> Vec<usize> {
    let n = target_tiles.max(1);
    let aspect = aspect.max(1e-6);
    let rows = ((n as f32 / aspect).sqrt().round() as usize).clamp(1, n);
    let base = n / rows;
    let rem = n % rows;
    (0..rows).map(|row| base + usize::from(row < rem)).collect()
}

fn curve_quality(distance: f32, curve: TileCurve, strength: f32) -> f32 {
    let d = distance.clamp(0.0, 1.0);
    let s = strength.max(1e-4);
    let q = match curve {
        TileCurve::Linear => 1.0 - d,
        TileCurve::SmoothStep => {
            let smooth = d * d * (3.0 - 2.0 * d);
            1.0 - smooth
        }
        TileCurve::Power => (1.0 - d).powf(s),
        TileCurve::Exponential => {
            let edge = (-s).exp();
            ((-s * d).exp() - edge) / (1.0 - edge).max(1e-9)
        }
        TileCurve::Gaussian => {
            let edge = (-s).exp();
            ((-s * d * d).exp() - edge) / (1.0 - edge).max(1e-9)
        }
    };
    q.clamp(0.0, 1.0)
}

fn aggregate(values: &[f32], aggregation: TileAggregation) -> f32 {
    if values.is_empty() {
        return 0.0;
    }
    match aggregation {
        TileAggregation::Mean => values.iter().map(|v| v.clamp(0.0, 1.0)).sum::<f32>() / values.len() as f32,
        TileAggregation::Max => values.iter().fold(0.0f32, |a, &b| a.max(b.clamp(0.0, 1.0))),
        TileAggregation::P90 => {
            let mut copy: Vec<f32> = values.iter().map(|v| v.clamp(0.0, 1.0)).collect();
            copy.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let idx = (((copy.len() - 1) as f32) * 0.90).round() as usize;
            copy[idx.min(copy.len() - 1)]
        }
    }
}

pub fn plan_tiles(
    quality_map: &[f32],
    width: usize,
    height: usize,
    config: &TilePlannerConfig,
) -> TilePlan {
    if width == 0 || height == 0 || quality_map.len() < width * height {
        return TilePlan { requested_tiles: config.target_tiles, tiles: Vec::new() };
    }
    let counts = row_counts(config.target_tiles.max(1), width as f32 / height as f32);
    let rows = counts.len();
    let mut tiles = Vec::with_capacity(config.target_tiles.max(1));
    let mut index = 0usize;

    for (row, &cols) in counts.iter().enumerate() {
        let y0 = row * height / rows;
        let y1 = (row + 1) * height / rows;
        for col in 0..cols {
            let x0 = col * width / cols;
            let x1 = (col + 1) * width / cols;
            let mut values = Vec::with_capacity((x1 - x0).max(1) * (y1 - y0).max(1));
            for y in y0..y1 {
                values.extend_from_slice(&quality_map[y * width + x0..y * width + x1]);
            }
            let relevance = aggregate(&values, config.aggregation).clamp(0.0, 1.0);
            let distance = 1.0 - relevance;
            let raw = curve_quality(distance, config.curve, config.curve_strength);
            let qmin = config.min_quality.clamp(0.0, 1.0);
            let quality = (qmin + (1.0 - qmin) * raw).clamp(0.0, 1.0);
            let smin = config.min_resolution_scale.clamp(1e-4, 1.0);
            let resolution_scale = (smin + (1.0 - smin) * quality).clamp(smin, 1.0);
            let qp = config.periphery_qp_delta as f32
                + quality * (config.fovea_qp_delta as f32 - config.periphery_qp_delta as f32);
            tiles.push(TileDecision {
                index,
                row,
                column: col,
                x: x0 as f32 / width as f32,
                y: y0 as f32 / height as f32,
                w: (x1 - x0) as f32 / width as f32,
                h: (y1 - y0) as f32 / height as f32,
                relevance,
                distance,
                quality,
                resolution_scale,
                qp_delta: qp.round().clamp(i8::MIN as f32, i8::MAX as f32) as i8,
            });
            index += 1;
        }
    }

    TilePlan { requested_tiles: config.target_tiles.max(1), tiles }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn returns_exact_requested_tile_count() {
        let mut q = vec![0.0f32; 160 * 90];
        for y in 30..60 {
            for x in 60..100 {
                q[y * 160 + x] = 1.0;
            }
        }
        for n in [1usize, 10, 25, 100, 137, 400] {
            let cfg = TilePlannerConfig { target_tiles: n, ..Default::default() };
            let plan = plan_tiles(&q, 160, 90, &cfg);
            assert_eq!(plan.tiles.len(), n);
        }
    }

    #[test]
    fn relevant_tiles_receive_more_quality() {
        let mut q = vec![0.0f32; 100 * 100];
        for y in 0..50 {
            for x in 0..50 {
                q[y * 100 + x] = 1.0;
            }
        }
        let cfg = TilePlannerConfig { target_tiles: 4, ..Default::default() };
        let plan = plan_tiles(&q, 100, 100, &cfg);
        assert!(plan.tiles[0].quality > plan.tiles[3].quality);
        assert!(plan.tiles[0].resolution_scale > plan.tiles[3].resolution_scale);
        assert!(plan.tiles[0].qp_delta < plan.tiles[3].qp_delta);
    }
}
