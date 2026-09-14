#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point2 { pub x: f32, pub y: f32 }
impl Point2 { pub const fn new(x:f32,y:f32)->Self{Self{x,y}} pub fn clamped(self)->Self{Self{x:self.x.clamp(0.0,1.0),y:self.y.clamp(0.0,1.0)}} }

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RoiRect { pub x:f32,pub y:f32,pub w:f32,pub h:f32,pub confidence:f32 }
impl RoiRect { pub fn normalized(self)->Self{ let x0=self.x.clamp(0.0,1.0); let y0=self.y.clamp(0.0,1.0); let x1=(self.x+self.w).clamp(x0,1.0); let y1=(self.y+self.h).clamp(y0,1.0); Self{x:x0,y:y0,w:x1-x0,h:y1-y0,confidence:self.confidence.clamp(0.0,1.0)} } }

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Falloff { Linear=0, SmoothStep=1, Gaussian=2 }
impl Default for Falloff { fn default()->Self{Self::SmoothStep} }

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct FoveationConfig { pub radius_x:f32,pub radius_y:f32,pub falloff_x:f32,pub falloff_y:f32,pub peripheral_scale:f32,pub peripheral_desaturate:f32,pub gamma:f32,pub falloff:Falloff }
impl Default for FoveationConfig { fn default()->Self{Self{radius_x:0.12,radius_y:0.12,falloff_x:0.28,falloff_y:0.28,peripheral_scale:0.25,peripheral_desaturate:0.0,gamma:1.0,falloff:Falloff::SmoothStep}} }

#[derive(Clone, Debug)]
pub struct FocusCandidate { pub point:Point2,pub confidence:f32,pub weight:f32 }
impl FocusCandidate { pub fn effective_weight(&self)->f32{self.confidence.max(0.0)*self.weight.max(0.0)} }
