#include "network.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdatomic.h>
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_websocket_client.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
static esp_websocket_client_handle_t ws;
static RingbufHandle_t audio_queue;
static SemaphoreHandle_t txlock;
static volatile bool ready,failed,ended,wake_enabled,wake_capturing,wake_gilligan;
static volatile uint32_t wake_turn,session_generation;
static volatile uint32_t active_turn;
static atomic_int buffered_bytes;
static char token[100],url[180],ssid[33],password[65],mode[12]="echo";
static bool configured;
static uint8_t message[8192];static int received,expected,opcode;
static bool send_text(const char *text){if(!ws)return false;xSemaphoreTake(txlock,portMAX_DELAY);int n=esp_websocket_client_send_text(ws,text,strlen(text),pdMS_TO_TICKS(5000));xSemaphoreGive(txlock);return n>=0;}
static bool load_str(nvs_handle_t h,const char *key,char *out,size_t max){return nvs_get_str(h,key,out,&max)==ESP_OK;}
static void provision_task(void *unused){
 char line[768];int used=0;
 while(1){
  int ch=getchar();
  if(ch==EOF){clearerr(stdin);vTaskDelay(pdMS_TO_TICKS(10));continue;}
  if(ch!='\n'&&ch!='\r'){if(used<(int)sizeof(line)-1)line[used++]=(char)ch;else used=0;continue;}
  if(used){line[used]=0;used=0;
   cJSON *j=cJSON_Parse(line);if(!j)continue;
   const char *keys[]={"ssid","password","url","token","mode"};bool valid=true;
   size_t limits[]={32,64,179,99,11};
   for(int i=0;i<5;i++){cJSON *v=cJSON_GetObjectItem(j,keys[i]);if(!cJSON_IsString(v)||strlen(v->valuestring)>limits[i])valid=false;}
   if(valid){nvs_handle_t h;ESP_ERROR_CHECK(nvs_open("omnibot",NVS_READWRITE,&h));
    for(int i=0;i<5;i++)ESP_ERROR_CHECK(nvs_set_str(h,keys[i],cJSON_GetObjectItem(j,keys[i])->valuestring));
    ESP_ERROR_CHECK(nvs_commit(h));nvs_close(h);puts("PROVISIONED: saved; restarting");fflush(stdout);vTaskDelay(pdMS_TO_TICKS(200));esp_restart();}
   cJSON_Delete(j);
  }
  vTaskDelay(pdMS_TO_TICKS(20));
 }
}
static void websocket_event(void *arg,esp_event_base_t base,int32_t id,void *event){
 esp_websocket_event_data_t *e=event;
 if(id==WEBSOCKET_EVENT_CONNECTED){
  char hello[240];snprintf(hello,sizeof(hello),"{\"wake_supported\":true,\"device_id\":\"waveshare-143c\",\"token\":\"%s\"}",token);send_text(hello);ESP_LOGI("net","Hub socket connected");
 }else if(id==WEBSOCKET_EVENT_DISCONNECTED||id==WEBSOCKET_EVENT_ERROR||id==WEBSOCKET_EVENT_CLOSED){ready=false;failed=true;wake_enabled=false;wake_capturing=false;ESP_LOGW("net","Hub offline");}
 else if(id==WEBSOCKET_EVENT_DATA){
  if(e->op_code==0x09||e->op_code==0x0a)return;
  if(e->payload_offset==0){received=0;expected=e->payload_len;opcode=e->op_code;}
  if(expected<0||expected>(int)sizeof(message)-1||e->payload_offset!=received||received+e->data_len>expected){failed=true;return;}
  memcpy(message+received,e->data_ptr,e->data_len);received+=e->data_len;if(received!=expected)return;
  if(opcode==0x01){message[received]=0;cJSON *j=cJSON_Parse((char*)message);if(!j){failed=true;return;}
   cJSON *kind=cJSON_GetObjectItem(j,"type"),*tid=cJSON_GetObjectItem(j,"turn_id");
   if(cJSON_IsString(kind)){
    if(!strcmp(kind->valuestring,"ready")){ready=true;failed=false;wake_turn=0;wake_capturing=false;active_turn=0;session_generation++;wake_enabled=cJSON_IsTrue(cJSON_GetObjectItem(j,"wake_enabled"));cJSON *phrase=cJSON_GetObjectItem(j,"wake_phrase");wake_gilligan=cJSON_IsString(phrase)&&!strcmp(phrase->valuestring,"Gilligan");ESP_LOGI("net","Hub authenticated READY");}
    else if(!strcmp(kind->valuestring,"wake_start")&&cJSON_IsNumber(tid)){
     active_turn=(uint32_t)tid->valuedouble;wake_turn=active_turn;wake_capturing=true;failed=false;ended=false;
     size_t n;void *old;while((old=xRingbufferReceive(audio_queue,&n,0)))vRingbufferReturnItem(audio_queue,old);atomic_store(&buffered_bytes,0);
    }else if(cJSON_IsNumber(tid)&&(uint32_t)tid->valuedouble==active_turn){
     if(!strcmp(kind->valuestring,"thinking"))wake_capturing=false;
     if(!strcmp(kind->valuestring,"audio_end")){ended=true;}
     else if(!strcmp(kind->valuestring,"error")){failed=true;ESP_LOGE("net","Hub turn failed");}
    }
   }cJSON_Delete(j);
  }else if(opcode==0x02&&received>=4){uint32_t tid;memcpy(&tid,message,4);if(tid!=active_turn)return;
   atomic_fetch_add(&buffered_bytes,received-4);
   if((received-4)%2||xRingbufferSend(audio_queue,message,received,0)!=pdTRUE){atomic_fetch_sub(&buffered_bytes,received-4);failed=true;ESP_LOGE("net","Playback queue overflow or invalid PCM");}
  }
 }
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data){
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_START)esp_wifi_connect();
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_DISCONNECTED){ready=false;failed=true;wifi_event_sta_disconnected_t *lost=data;ESP_LOGW("net","Wi-Fi disconnect reason=%d",lost->reason);esp_wifi_connect();}
 if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP){ESP_LOGI("net","Wi-Fi ready");if(!ws){
  esp_websocket_client_config_t cfg={.uri=url,.buffer_size=2048,.task_stack=8192,.reconnect_timeout_ms=3000,.enable_close_reconnect=true,.network_timeout_ms=5000,.ping_interval_sec=20};
  ws=esp_websocket_client_init(&cfg);ESP_ERROR_CHECK(esp_websocket_register_events(ws,WEBSOCKET_EVENT_ANY,websocket_event,NULL));ESP_ERROR_CHECK(esp_websocket_client_start(ws));
 }}
}
void network_init(void){
 ESP_ERROR_CHECK(nvs_flash_init());txlock=xSemaphoreCreateMutex();audio_queue=xRingbufferCreateWithCaps(128*1024,RINGBUF_TYPE_NOSPLIT,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);assert(txlock&&audio_queue);
 nvs_handle_t h;if(nvs_open("omnibot",NVS_READWRITE,&h)==ESP_OK){configured=load_str(h,"ssid",ssid,sizeof(ssid))&&load_str(h,"password",password,sizeof(password))&&load_str(h,"url",url,sizeof(url))&&load_str(h,"token",token,sizeof(token));load_str(h,"mode",mode,sizeof(mode));nvs_close(h);}
 xTaskCreate(provision_task,"provision",4096,NULL,3,NULL);
 if(!configured){ESP_LOGW("net","USB provisioning required");return;}
 ESP_ERROR_CHECK(esp_netif_init());ESP_ERROR_CHECK(esp_event_loop_create_default());esp_netif_create_default_wifi_sta();
 wifi_init_config_t init=WIFI_INIT_CONFIG_DEFAULT();ESP_ERROR_CHECK(esp_wifi_init(&init));
 ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
 wifi_config_t cfg={0};strcpy((char*)cfg.sta.ssid,ssid);memcpy(cfg.sta.password,password,strlen(password));cfg.sta.pmf_cfg.capable=true;cfg.sta.pmf_cfg.required=false;cfg.sta.threshold.authmode=WIFI_AUTH_WPA2_PSK;
 ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&cfg));ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));ESP_ERROR_CHECK(esp_wifi_start());
}
bool network_ready(void){return ready;}
const char *network_mode(void){return mode;}
void network_cancel(uint32_t turn){char msg[90];active_turn=0;snprintf(msg,sizeof(msg),"{\"type\":\"cancel\",\"turn_id\":%lu}",(unsigned long)turn);send_text(msg);}
bool network_upload(const int16_t *stereo,int frames,uint32_t turn){
 if(!ready){return false;}
 active_turn=turn;failed=false;ended=false;
 size_t n;void *old;while((old=xRingbufferReceive(audio_queue,&n,0)))vRingbufferReturnItem(audio_queue,old);
 atomic_store(&buffered_bytes,0);
 char msg[100];snprintf(msg,sizeof(msg),"{\"type\":\"start\",\"turn_id\":%lu}",(unsigned long)turn);if(!send_text(msg))return false;
 int16_t mono[1600];
 for(int off=0;off<frames;off+=1600){int count=frames-off<1600?frames-off:1600;for(int i=0;i<count;i++)mono[i]=stereo[(off+i)*2];
 xSemaphoreTake(txlock,portMAX_DELAY);int ret=esp_websocket_client_send_bin(ws,(char*)mono,count*2,pdMS_TO_TICKS(5000));xSemaphoreGive(txlock);if(ret<0)return false;}
 snprintf(msg,sizeof(msg),"{\"type\":\"end\",\"turn_id\":%lu,\"mode\":\"%s\"}",(unsigned long)turn,mode);return send_text(msg);
}
int network_audio(uint32_t turn,int16_t *mono,int capacity,int *samples){
 *samples=0;if(failed||!ready)return -1;
 size_t n;uint8_t *item=xRingbufferReceive(audio_queue,&n,0);
 if(item){atomic_fetch_sub(&buffered_bytes,n-4);uint32_t tid;memcpy(&tid,item,4);int count=(n-4)/2;
 if(tid==turn&&count<=capacity){memcpy(mono,item+4,count*2);*samples=count;}
 vRingbufferReturnItem(audio_queue,item);return *samples?1:0;}
 return ended?2:0;
}

int network_buffered(void){return failed?-1:atomic_load(&buffered_bytes);}
bool network_finished(void){return ended;}

bool network_wake_enabled(void){return ready&&wake_enabled;}
bool network_wake_capturing(void){return ready&&wake_capturing;}
uint32_t network_wake_turn(void){return wake_turn;}
bool network_stream(const int16_t *mono,int frames){
 if(!ready||!wake_enabled||frames!=1280)return false;
 uint8_t packet[2561];packet[0]=0x10;memcpy(packet+1,mono,2560);
 xSemaphoreTake(txlock,portMAX_DELAY);int ret=esp_websocket_client_send_bin(ws,(char*)packet,sizeof(packet),pdMS_TO_TICKS(2000));xSemaphoreGive(txlock);return ret>=0;
}
void network_wake_finish(uint32_t turn){char msg[100];snprintf(msg,sizeof(msg),"{\"type\":\"wake_finish\",\"turn_id\":%lu}",(unsigned long)turn);send_text(msg);}
void network_playback_done(uint32_t turn){char msg[100];snprintf(msg,sizeof(msg),"{\"type\":\"playback_done\",\"turn_id\":%lu}",(unsigned long)turn);send_text(msg);}

uint32_t network_session_generation(void){return session_generation;}

int network_wake_screen(void){return wake_gilligan?9:8;}
