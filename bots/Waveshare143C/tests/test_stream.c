#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include "wake_stream.h"
static int16_t stereo[32000],full[16000],split[16000];
int main(void){
 for(int i=0;i<16000;i++){stereo[i*2]=(int16_t)(1000+2000*sin(2*3.14159265*440*i/16000));stereo[i*2+1]=0;}
 wake_stream_reset();wake_stream_process(stereo,full,16000);
 wake_stream_reset();for(int i=0;i<16000;i+=320)wake_stream_process(stereo+i*2,split+i,320);
 for(int i=0;i<16000;i++){assert(full[i]==split[i]);assert(abs((int)full[i])<32000);}
 for(int i=0;i<32000;i++)stereo[i]=32767;
 wake_stream_reset();wake_stream_process(stereo,full,16000);
 assert(abs((int)full[15999])<10);
 for(int i=0;i<16000;i++)assert(abs((int)full[i])<=32000);
 puts("PASS: streaming output independent of packet boundaries; DC removed; peaks bounded");
}
