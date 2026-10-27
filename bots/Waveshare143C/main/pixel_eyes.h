#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef struct {
    int state; uint32_t seed, entered, next_look, next_blink, blink, next_expression, expression_start, expression_duration;
    int expression, side; float look_x,look_y,target_x,target_y;
} pixel_anim_t;
typedef struct {float x,y,left_ry,right_ry;uint16_t color;bool brows;} pixel_pose_t;
void pixel_eyes_init(pixel_anim_t *a,uint32_t seed);
void pixel_eyes_update(pixel_anim_t *a,int state,uint32_t now,pixel_pose_t *p);
void pixel_render_row(const pixel_pose_t *p,int y,int x,int width,uint16_t *out);
