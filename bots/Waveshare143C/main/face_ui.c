#include "face_ui.h"
void face_render_row(int state,const pixel_pose_t *pose,int y,int x,int width,uint16_t *out){
    (void)state;
    pixel_render_row(pose,y,x,width,out);
}
