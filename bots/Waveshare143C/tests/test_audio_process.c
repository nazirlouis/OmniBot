#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include "audio_process.h"
static int16_t pcm[96000];
int main(void){
 audio_stats_t s=process_voice(pcm,48000);assert(s.gain==1 && s.limited==0);
 for(int i=0;i<48000;i++){pcm[i*2]=1000;pcm[i*2+1]=1000;}
 s=process_voice(pcm,48000);assert(abs(pcm[95998])<2); /* DC removed */
 for(int i=0;i<48000;i++){pcm[i*2]=0;pcm[i*2+1]=(int16_t)(600*sin(2*3.14159265*440*i/16000));}
 pcm[16000*2+1]=30000; /* isolated plosive must not suppress rest of speech */
 s=process_voice(pcm,48000);assert(s.channel==1 && s.gain>8 && s.limited>0);
 double sum=0;for(int i=1600;i<15000;i++){assert(pcm[i*2]==pcm[i*2+1]);sum+=(double)pcm[i*2]*pcm[i*2];}
 assert(sqrt(sum/13400)>3500);
 for(int i=0;i<96000;i++)pcm[i]=(i&1)?-32768:32767;
 s=process_voice(pcm,48000);for(int i=0;i<96000;i++)assert(abs((int)pcm[i])<=32000);
 puts("PASS: silence, DC rejection, weak channel, plosive headroom, speech level, full-scale bounds");
}
