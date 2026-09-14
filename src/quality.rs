use crate::types::{Falloff, FoveationConfig, Point2, RoiRect};

#[inline]
fn apply_curve(t:f32,falloff:Falloff,gamma:f32)->f32{let x=t.clamp(0.0,1.0);let q=match falloff{Falloff::Linear=>1.0-x,Falloff::SmoothStep=>{let s=x*x*(3.0-2.0*x);1.0-s},Falloff::Gaussian=>(-4.5*x*x).exp()};q.clamp(0.0,1.0).powf(gamma.max(0.01))}
#[inline]
fn point_quality(nx:f32,ny:f32,p:Point2,cfg:&FoveationConfig)->f32{let rx=cfg.radius_x.max(1e-6);let ry=cfg.radius_y.max(1e-6);let fx=cfg.falloff_x.max(1e-6);let fy=cfg.falloff_y.max(1e-6);let dx=(nx-p.x).abs();let dy=(ny-p.y).abs();let inner=((dx/rx).powi(2)+(dy/ry).powi(2)).sqrt();if inner<=1.0{return 1.0;}let outer=((dx/(rx+fx)).powi(2)+(dy/(ry+fy)).powi(2)).sqrt();if outer>=1.0{return 0.0;}let t=((inner-1.0)/((inner/outer.max(1e-6))-1.0).abs().max(1e-6)).clamp(0.0,1.0);apply_curve(t,cfg.falloff,cfg.gamma)}
#[inline]
fn rect_quality(nx:f32,ny:f32,r:RoiRect,cfg:&FoveationConfig)->f32{let r=r.normalized();let x0=r.x;let x1=r.x+r.w;let y0=r.y;let y1=r.y+r.h;if nx>=x0&&nx<=x1&&ny>=y0&&ny<=y1{return r.confidence;}let dx=if nx<x0{x0-nx}else if nx>x1{nx-x1}else{0.0};let dy=if ny<y0{y0-ny}else if ny>y1{ny-y1}else{0.0};let d=((dx/cfg.falloff_x.max(1e-6)).powi(2)+(dy/cfg.falloff_y.max(1e-6)).powi(2)).sqrt();if d>=1.0{0.0}else{r.confidence*apply_curve(d,cfg.falloff,cfg.gamma)}}

pub fn quality_map(width:usize,height:usize,points:&[Point2],rois:&[RoiRect],cfg:&FoveationConfig)->Vec<f32>{if width==0||height==0{return Vec::new();}let mut out=vec![0.0;width*height];let w1=(width.saturating_sub(1)).max(1) as f32;let h1=(height.saturating_sub(1)).max(1) as f32;for y in 0..height{let ny=y as f32/h1;for x in 0..width{let nx=x as f32/w1;let mut q=0.0f32;for &p in points{q=q.max(point_quality(nx,ny,p.clamped(),cfg));}for &r in rois{q=q.max(rect_quality(nx,ny,r,cfg));}out[y*width+x]=q;}}out}

pub fn qp_delta_map(quality:&[f32],width:usize,height:usize,block:usize,fovea_delta:i8,periphery_delta:i8)->Vec<i8>{if width==0||height==0||quality.len()<width*height{return Vec::new();}let block=block.max(1);let bw=(width+block-1)/block;let bh=(height+block-1)/block;let mut out=vec![0i8;bw*bh];for by in 0..bh{for bx in 0..bw{let x0=bx*block;let y0=by*block;let x1=((bx+1)*block).min(width);let y1=((by+1)*block).min(height);let mut sum=0.0;let mut n=0usize;for y in y0..y1{for x in x0..x1{sum+=quality[y*width+x].clamp(0.0,1.0);n+=1;}}let q=if n>0{sum/n as f32}else{0.0};let v=periphery_delta as f32+q*(fovea_delta as f32-periphery_delta as f32);out[by*bw+bx]=v.round().clamp(i8::MIN as f32,i8::MAX as f32) as i8;}}out}

#[cfg(test)] mod tests{use super::*;#[test]fn center_is_high_quality(){let cfg=FoveationConfig::default();let q=quality_map(101,101,&[Point2::new(0.5,0.5)],&[],&cfg);assert!(q[50*101+50]>0.99);assert!(q[0]<0.05);}}
