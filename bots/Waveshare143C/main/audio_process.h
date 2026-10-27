#pragma once
#include <stdint.h>
typedef struct { int channel; int raw_peak[2]; int raw_rms[2]; int speech_rms; float gain; int limited; } audio_stats_t;
audio_stats_t process_voice(int16_t *pcm, int frames);
