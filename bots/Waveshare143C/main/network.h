#pragma once
#include <stdbool.h>
#include <stdint.h>
void network_init(void);
bool network_ready(void);
bool network_upload(const int16_t *stereo,int frames,uint32_t turn);
void network_cancel(uint32_t turn);
/* 0 waiting, 1 audio, 2 end, -1 error. PCM is mono 16kHz. */
int network_audio(uint32_t turn,int16_t *mono,int capacity,int *samples);
const char *network_mode(void);
int network_buffered(void);
bool network_finished(void);

bool network_wake_enabled(void);
bool network_wake_capturing(void);
uint32_t network_wake_turn(void);
bool network_stream(const int16_t *mono,int frames);
void network_wake_finish(uint32_t turn);
void network_playback_done(uint32_t turn);

uint32_t network_session_generation(void);

int network_wake_screen(void);
