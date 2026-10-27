#include "wake_stream.h"
#include <math.h>
static float previous,filtered;
void wake_stream_reset(void){previous=0;filtered=0;}
void wake_stream_process(const int16_t *stereo,int16_t *mono,int frames){
 for(int i=0;i<frames;i++){
  float x=stereo[i*2];filtered=.96907f*(filtered+x-previous);previous=x;
  float v=filtered*4,a=fabsf(v);
  if(a>24000){float excess=a-24000;v=copysignf(24000+8000*excess/(8000+excess),v);}
  mono[i]=(int16_t)v;
 }
}
