#pragma once
#include <stdint.h>
void wake_stream_reset(void);
void wake_stream_process(const int16_t *stereo,int16_t *mono,int frames);
