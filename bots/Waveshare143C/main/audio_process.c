#include "audio_process.h"
#include <math.h>
#include <stdlib.h>
/* 16 kHz stereo input. Both microphones are inspected before choosing one.
 * DC/rumble rejection: one-pole 80 Hz high-pass. Constant gain per message
 * avoids pumping; 20 ms block RMS percentile avoids silence diluting speech.
 * Soft peak limiting prevents boosted plosives from wrapping or hard clipping. */
audio_stats_t process_voice(int16_t *pcm, int frames) {
    audio_stats_t stats={0};
    if(frames<=0)return stats;
    double energy[2]={0};float previous[2]={0},filtered[2]={0};
    for(int i=0;i<frames;i++)for(int c=0;c<2;c++){
        int v=pcm[i*2+c];int a=abs(v);if(a>stats.raw_peak[c])stats.raw_peak[c]=a;
        float y=.96907f*(filtered[c]+v-previous[c]);previous[c]=v;filtered[c]=y;
        pcm[i*2+c]=(int16_t)fmaxf(-32768,fminf(32767,y));
        energy[c]+=(double)pcm[i*2+c]*pcm[i*2+c];
    }
    for(int c=0;c<2;c++)stats.raw_rms[c]=(int)sqrt(energy[c]/frames);
    stats.channel=energy[1]>energy[0]?1:0;
    /* Histogram of block RMS: 75th percentile, rounded down to 64 units. */
    int histogram[513]={0},blocks=0;
    for(int start=0;start<frames;start+=320){
        int n=frames-start<320?frames-start:320;double sum=0;
        for(int i=0;i<n;i++){int v=pcm[(start+i)*2+stats.channel];sum+=(double)v*v;}
        int bin=(int)sqrt(sum/n)/64;if(bin>512)bin=512;histogram[bin]++;blocks++;
    }
    int cumulative=0;
    for(int i=0;i<513;i++){cumulative+=histogram[i];if(cumulative>=(blocks*3+3)/4){stats.speech_rms=i*64;break;}}
    stats.gain=1;
    if(stats.speech_rms>=128){stats.gain=4500.f/stats.speech_rms;if(stats.gain>16)stats.gain=16;if(stats.gain<.25f)stats.gain=.25f;}
    for(int i=0;i<frames;i++){
        float v=pcm[i*2+stats.channel]*stats.gain,a=fabsf(v);
        if(a>24000){stats.limited++;float excess=a-24000;v=copysignf(24000+8000*excess/(8000+excess),v);}
        int16_t sample=(int16_t)v;pcm[i*2]=sample;pcm[i*2+1]=sample;
    }
    return stats;
}
