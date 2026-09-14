mod atlas;
mod ffi;
mod focus;
mod foveate;
mod middleware;
mod predictive;
mod quality;
mod resample;
mod roi;
mod scheduler;
mod streaming;
mod types;

pub use atlas::{context_and_roi_views, ImageView};
pub use focus::{FocusTracker, FocusTrackerConfig};
pub use foveate::foveate_rgb8_with_map;
pub use middleware::{EmitPolicy, StreamMiddleware};
pub use predictive::{
    AttentionMeasurement, AttentionState, CameraIntrinsics, MotionMode, PredictionConfig,
    PredictiveAttentionFilter,
};
pub use quality::{qp_delta_map, quality_map};
pub use resample::{crop_rgb8, resize_rgb8};
pub use roi::{iou as roi_iou, MultiRoiTracker, RoiProposal, RoiTrack, RoiTrackerConfig};
pub use scheduler::{
    AdaptiveScheduler, InnovationSignals, SchedulerConfig, SendDecision, SendReason,
};
pub use streaming::{FrameInput, ProcessResult, StreamError, StreamRuntime, StreamRuntimeConfig, StreamSink};
pub use types::{Falloff, FocusCandidate, FoveationConfig, Point2, RoiRect};
