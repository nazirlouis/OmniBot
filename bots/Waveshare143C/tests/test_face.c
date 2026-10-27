#include "face_ui.h"
#include <assert.h>
#include <stdio.h>
int main(void){
    pixel_anim_t animation;pixel_pose_t pose;
    uint16_t row[468];int blinks=0,winks=0;
    for(int state=0;state<10;state++){
        pixel_eyes_init(&animation,6); /* This deterministic sequence includes a wink. */
        for(unsigned t=0;t<30000;t+=50){
            pixel_eyes_update(&animation,state,t,&pose);
            if(state==9&&pose.left_ry<20&&pose.right_ry<20)blinks++;
            if(state==9&&((pose.left_ry<20)!=(pose.right_ry<20)))winks++;
            for(int y=0;y<466;y++){
                row[0]=row[467]=0x1234;
                face_render_row(state,&pose,y,0,466,row+1);
                assert(row[0]==0x1234&&row[467]==0x1234);
                for(int x=0;x<466;x++)if(row[x+1]){
                    float dx=x-232.5f,dy=y-232.5f;
                    if(dx*dx+dy*dy>233*233){fprintf(stderr,"Clipped state=%d t=%u x=%d y=%d\n",state,t,x,y);assert(0);}
                    assert(x>=8&&x<458&&y>=84&&y<382);
                }
            }
        }
    }
    assert(blinks&&winks);
    puts("PASS: ten states over 30s each; bounded row writes, round-screen clipping, animated dirty region, blink and wink");
}
