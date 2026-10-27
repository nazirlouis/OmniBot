/* Adapted from OmniBot Pixel's eye geometry, idle expressions and speech cadence.
 * Copyright (c) 2026 Omnibot contributors; MIT license in Pixel-LICENSE. */
#include "pixel_eyes.h"
#include <math.h>
#include <string.h>
static uint32_t random_range(pixel_anim_t *a,uint32_t lo,uint32_t hi){
    uint32_t x=a->seed;x^=x<<13;x^=x>>17;x^=x<<5;a->seed=x;return lo+x%(hi-lo);
}
static bool due(uint32_t now,uint32_t deadline){return (int32_t)(now-deadline)>=0;}
static float pulse(uint32_t t,uint32_t duration,float depth){
    if(t>=duration)return 0;
    float phase=(float)t/duration;
    return depth*(phase<.5f?phase*2:(1-phase)*2);
}
static float at_least(float x,float floor){return x<floor?floor:x;}
void pixel_eyes_init(pixel_anim_t *a,uint32_t seed){memset(a,0,sizeof(*a));a->state=-1;a->seed=seed?seed:1;}
void pixel_eyes_update(pixel_anim_t *a,int state,uint32_t now,pixel_pose_t *p){
    if(state!=a->state){a->state=state;a->entered=now;a->look_x=a->look_y=a->target_x=a->target_y=0;
        a->next_look=now+900;a->next_blink=now+2500;a->blink=0;a->next_expression=now+5000;a->expression=0;}
    uint32_t elapsed=now-a->entered;
    *p=(pixel_pose_t){.left_ry=66,.right_ry=66,.color=0x07ff};
    if(state==4||state==1){
        int step=(elapsed/220)%8;p->x=step<2?-8:step<4?-4:step<6?4:8;
        p->left_ry=26;p->right_ry=24;p->brows=true;
    }else if(state==3){
        p->left_ry=38+pulse(elapsed%700,700,8);p->right_ry=p->left_ry;p->color=0xffe0;
    }else if(state==5){
        float cadence=pulse(elapsed%340,340,22)+pulse(elapsed%210,210,12);
        p->left_ry=p->right_ry=66-cadence;p->x=sinf(elapsed*.012f)*4;
    }else if(state==6||state==7){
        p->left_ry=state==6?18:30;p->right_ry=state==6?18:54;p->color=0xffff;
    }else{
        if(due(now,a->next_look)){
            a->target_x=(int)random_range(a,0,21)-10;a->target_y=(int)random_range(a,0,11)-5;
            a->next_look=now+random_range(a,900,2400);
        }
        a->look_x+=(a->target_x-a->look_x)/3;a->look_y+=(a->target_y-a->look_y)/3;
        p->x=a->look_x;p->y=a->look_y;
        if(!a->blink&&due(now,a->next_blink)){a->blink=now;a->next_blink=now+random_range(a,2500,7000);}
        if(a->blink){float squint=pulse(now-a->blink,200,54);p->left_ry-=squint;p->right_ry-=squint;if(now-a->blink>=200)a->blink=0;}
        if(!a->expression&&due(now,a->next_expression)){
            unsigned roll=random_range(a,0,1000);a->expression=roll<420?1:roll<760?3:roll<900?4:5;
            a->side=random_range(a,0,2)?1:-1;a->expression_start=now;
            a->expression_duration=a->expression==1?320:a->expression==3?900:a->expression==4?160:500;
            if(a->expression==3){a->target_x=a->side*14;a->target_y=0;a->next_look=now+1000;}
        }
        if(a->expression){uint32_t t=now-a->expression_start;float squint=0;
            if(t>=a->expression_duration){a->expression=0;a->next_expression=now+random_range(a,2200,7000);}
            else if(a->expression==1)squint=t<120?pulse(t,120,56):t>=200?pulse(t-200,120,56):0;
            else if(a->expression==4){float wink=pulse(t,160,62);if(a->side<0)p->left_ry-=wink;else p->right_ry-=wink;}
            else if(a->expression==5)squint=t<190?pulse(t,190,58):t>=310?pulse(t-310,190,58):0;
            p->left_ry-=squint;p->right_ry-=squint;
        }
    }
    p->left_ry=at_least(p->left_ry,8);p->right_ry=at_least(p->right_ry,8);
}
void pixel_render_row(const pixel_pose_t *p,int y,int x,int width,uint16_t *out){
    memset(out,0,width*sizeof(*out));
    const float scale=2.0f,cy=233+p->y*scale,rx=46*scale;
    for(int eye=0;eye<2;eye++){
        float cx=(eye?337:129)+p->x*scale,ry=(eye?p->right_ry:p->left_ry)*scale,dy=y-cy;
        if(fabsf(dy)<=ry){
            float half=rx*sqrtf(fmaxf(0,1-dy*dy/(ry*ry)));
            int left=(int)ceilf(cx-half),right=(int)floorf(cx+half);
            if(left<x)left=x;
            if(right>=x+width)right=x+width-1;
            for(int col=left;col<=right;col++)out[col-x]=p->color;
        }
    }
    if(p->brows&&y>=125&&y<=150){
        for(int col=x;col<x+width;col++){
            float local=(col+7)/scale-p->x*.5f;
            float line=local<120?68-(local-30)*8/58:60+(local-152)*8/58;
            if(((local>=30&&local<=88)||(local>=152&&local<=210))&&fabsf(y-(9+line*scale))<2.5f)out[col-x]=p->color;
        }
    }
}
