#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "codec_board.h"
#include "codec_init.h"
#include "esp_codec_dev.h"
#include "audio_process.h"
#include "network.h"
#include "wake_stream.h"
#define MAX_FRAMES (16000*15)
static const char *TAG="board";
void display_init(void);void show_screen(int state);
#define CHECK(call) do{if((call)!=ESP_OK){show_screen(7);ESP_LOGE(TAG,"Failed: %s",#call);return;}}while(0)
static bool press(void){static bool held;bool now=!gpio_get_level(GPIO_NUM_0);if(now&&!held){held=true;return true;}if(!now)held=false;return false;}
static void play_reply(esp_codec_dev_handle_t speaker,uint32_t turn,int16_t *mono,int16_t *out){
   bool playing=false;int underrun_ticks=0;int64_t deadline=esp_timer_get_time()+100000000;
   while(1){
    if(press()){network_cancel(turn);ESP_LOGI(TAG,"Cancelled turn=%lu",(unsigned long)turn);break;}
    if(!playing && network_ready() && !network_finished() && network_buffered()>=0 && network_buffered()<11200 && esp_timer_get_time()<deadline){vTaskDelay(pdMS_TO_TICKS(10));continue;}
    int count=0,status=network_audio(turn,mono,4096,&count);
    if(status<0||esp_timer_get_time()>deadline){network_cancel(turn);show_screen(7);ESP_LOGE(TAG,"Turn failed or timed out");vTaskDelay(pdMS_TO_TICKS(1500));break;}
    if(status==2){ESP_LOGI(TAG,"Playback complete turn=%lu; empty queue wait=%d ms",(unsigned long)turn,underrun_ticks*10);break;}
    if(status==1){if(!playing){show_screen(5);playing=true;ESP_LOGI(TAG,"Playback started turn=%lu",(unsigned long)turn);}
     for(int i=0;i<count;i++){out[i*2]=out[i*2+1]=mono[i];}
     if(esp_codec_dev_write(speaker,out,count*4)!=ESP_OK){network_cancel(turn);break;}
    }else {if(playing)underrun_ticks++;vTaskDelay(pdMS_TO_TICKS(10));}
   }
 network_playback_done(turn);wake_stream_reset();
}
void app_main(void){
 display_init();
 gpio_config_t button={.pin_bit_mask=1ULL<<0,.mode=GPIO_MODE_INPUT,.pull_up_en=true};CHECK(gpio_config(&button));
 gpio_config_t led={.pin_bit_mask=1ULL<<5,.mode=GPIO_MODE_OUTPUT};CHECK(gpio_config(&led));
 set_codec_board_type("S3_AMOLED_1_43C");codec_init_cfg_t cfg={.in_mode=CODEC_I2S_MODE_TDM,.out_mode=CODEC_I2S_MODE_TDM};CHECK(init_codec(&cfg));
 esp_codec_dev_handle_t speaker=get_playback_handle(),mic=get_record_handle();
 int16_t *audio=heap_caps_malloc(MAX_FRAMES*4,MALLOC_CAP_SPIRAM),*out=heap_caps_malloc(4096*4,MALLOC_CAP_SPIRAM),*mono=heap_caps_malloc(4096*2,MALLOC_CAP_SPIRAM);
 if(!speaker||!mic||!audio||!out||!mono){show_screen(7);return;}
 esp_codec_dev_sample_info_t format={.sample_rate=16000,.channel=2,.bits_per_sample=16};CHECK(esp_codec_dev_open(speaker,&format));CHECK(esp_codec_dev_open(mic,&format));CHECK(esp_codec_dev_set_out_vol(speaker,100));CHECK(esp_codec_dev_set_in_gain(mic,37.5));
 network_init();show_screen(1);int shown=1;uint32_t turn=0,seen_session=0;
 while(1){
  if(network_session_generation()!=seen_session){seen_session=network_session_generation();turn=0;wake_stream_reset();}
  bool local=!strcmp(network_mode(),"local");bool online=network_ready()||local;
  int desired=online?(network_wake_enabled()?network_wake_screen():2):6;if(shown!=desired){show_screen(desired);shown=desired;}
  if(online&&network_wake_enabled()&&network_wake_turn()>turn){
   turn=network_wake_turn();show_screen(3);bool finish_sent=false;
   while(network_wake_capturing()){
    if(press()&&!finish_sent){network_wake_finish(turn);finish_sent=true;}
    if(esp_codec_dev_read(mic,audio,1280*4)!=ESP_OK)break;
    wake_stream_process(audio,mono,1280);if(!network_stream(mono,1280))break;
   }
   show_screen(4);play_reply(speaker,turn,mono,out);shown=-1;continue;
  }
  if(press()&&online){
   int frames=0;show_screen(3);gpio_set_level(5,1);ESP_LOGI(TAG,"Recording max15s; BOOT finishes");
   vTaskDelay(pdMS_TO_TICKS(40));
   do{if(esp_codec_dev_read(mic,audio+frames*2,320*4)!=ESP_OK)break;frames+=320;if(press())break;}while(frames<MAX_FRAMES);
   gpio_set_level(5,0);audio_stats_t s=process_voice(audio,frames);ESP_LOGI(TAG,"Captured frames=%d speech_RMS=%d gain=%.2f",frames,s.speech_rms,s.gain);
   if(local){show_screen(5);CHECK(esp_codec_dev_write(speaker,audio,frames*4));shown=-1;continue;}
   show_screen(4);ESP_LOGI(TAG,"Uploading turn=%lu mode=%s",(unsigned long)++turn,network_mode());
   if(!network_upload(audio,frames,turn)){network_cancel(turn);show_screen(7);vTaskDelay(pdMS_TO_TICKS(1500));shown=-1;continue;}
   play_reply(speaker,turn,mono,out);
   shown=-1;
  }
  if(network_wake_enabled()){
   if(esp_codec_dev_read(mic,audio,1280*4)==ESP_OK){wake_stream_process(audio,mono,1280);network_stream(mono,1280);}
  }else vTaskDelay(pdMS_TO_TICKS(15));
 }
}
