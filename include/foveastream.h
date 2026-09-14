#ifndef FOVEASTREAM_H
#define FOVEASTREAM_H
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#ifdef FOVEASTREAM_BUILD
#define FS_API __declspec(dllexport)
#else
#define FS_API __declspec(dllimport)
#endif
#else
#define FS_API __attribute__((visibility("default")))
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef enum fs_falloff { FS_FALLOFF_LINEAR=0, FS_FALLOFF_SMOOTHSTEP=1, FS_FALLOFF_GAUSSIAN=2 } fs_falloff;
typedef struct fs_foveation_config { float radius_x; float radius_y; float falloff_x; float falloff_y; float peripheral_scale; float peripheral_desaturate; float gamma; fs_falloff falloff; } fs_foveation_config;
FS_API int32_t fs_foveate_rgb8(const uint8_t *src,size_t width,size_t height,const float *quality,const fs_foveation_config *cfg,uint8_t *dst);
FS_API intptr_t fs_qp_delta_map(const float *quality,size_t width,size_t height,size_t block,int8_t fovea_delta,int8_t periphery_delta,int8_t *dst,size_t dst_len);
#ifdef __cplusplus
}
#endif
#endif
