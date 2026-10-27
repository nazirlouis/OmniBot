#pragma once
#include "pixel_eyes.h"
#define FACE_SIZE 466
void face_render_row(int state,const pixel_pose_t *pose,int y,int x,int width,uint16_t *out);
