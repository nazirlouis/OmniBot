#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_sh8601.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "face_ui.h"
#include <stdatomic.h>
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t done;
static uint16_t *strip;
static atomic_int requested_state;
static bool complete(esp_lcd_panel_io_handle_t h,esp_lcd_panel_io_event_data_t* e,void* p){BaseType_t wake=pdFALSE;xSemaphoreGiveFromISR(done,&wake);return wake==pdTRUE;}
void show_screen(int state){
 atomic_store_explicit(&requested_state,state>=0&&state<=9?state:7,memory_order_relaxed);
}
static void display_task(void *unused){
 pixel_anim_t anim;pixel_eyes_init(&anim,esp_random());int previous=-1;
 TickType_t next=xTaskGetTickCount();
 while(1){
  int state=atomic_load_explicit(&requested_state,memory_order_relaxed);
  pixel_pose_t pose;pixel_eyes_update(&anim,state,(uint32_t)(esp_timer_get_time()/1000),&pose);
  bool changed=state!=previous;
  /* Eyes only. DMA owns strip until transfer completion. */
  int x=changed?0:8,width=changed?466:450,start=changed?0:84,end=changed?466:382;
  for(int y=start;y<end;y+=32){
   int rows=end-y<32?end-y:32;
   for(int row=0;row<rows;row++){
    uint16_t *line=strip+row*width;face_render_row(state,&pose,y+row,x,width,line);
    for(int col=0;col<width;col++)line[col]=__builtin_bswap16(line[col]);
   }
   ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel,x,y,x+width,y+rows,strip));
   assert(xSemaphoreTake(done,pdMS_TO_TICKS(1000))==pdTRUE);
  }
  previous=state;vTaskDelayUntil(&next,pdMS_TO_TICKS(50));
 }
}
void display_init(void){
 /* GPIO42 is microphone data, not a display power enable on 1.43C. */
 done=xSemaphoreCreateBinary();strip=heap_caps_malloc(466*32*2,MALLOC_CAP_DMA);assert(done&&strip);
 spi_bus_config_t bus=SH8601_PANEL_BUS_QSPI_CONFIG(14,9,10,11,12,466*32*2);
 ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST,&bus,SPI_DMA_CH_AUTO));
 esp_lcd_panel_io_spi_config_t cfg=SH8601_PANEL_IO_QSPI_CONFIG(15,complete,NULL);cfg.trans_queue_depth=1;cfg.pclk_hz=40000000;
 esp_lcd_panel_io_handle_t io;ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,&cfg,&io));
 static const uint8_t zero=0,level=0x20,full=0xff,c4=0x80,rgb565=0x55,orientation=0xc0;
 static const uint8_t cols[]={0,6,1,0xd7},rows[]={0,0,1,0xd1};
 static const sh8601_lcd_init_cmd_t cmds[]={
 {0xfe,&zero,1,0},{0xc4,&c4,1,0},{0x3a,&rgb565,1,0},{0x35,&zero,1,0},
 {0x53,&level,1,0},{0x51,&full,1,0},{0x36,&orientation,1,0},{0x63,&full,1,0},
 {0x2a,cols,4,0},{0x2b,rows,4,0},{0x11,&zero,0,100},{0x29,&zero,0,0}};
 sh8601_vendor_config_t vendor={.init_cmds=cmds,.init_cmds_size=sizeof(cmds)/sizeof(cmds[0]),.flags={.use_qspi_interface=1}};
 esp_lcd_panel_dev_config_t dev={.reset_gpio_num=13,.rgb_ele_order=LCD_RGB_ELEMENT_ORDER_RGB,.bits_per_pixel=16,.vendor_config=&vendor};
 ESP_ERROR_CHECK(esp_lcd_new_panel_sh8601(io,&dev,&panel));ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));ESP_ERROR_CHECK(esp_lcd_panel_init(panel));ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel,8,0));
 show_screen(0);uint8_t brightness=200;ESP_ERROR_CHECK(esp_lcd_panel_io_tx_param(io,0x02005100,&brightness,1));
 assert(xTaskCreatePinnedToCore(display_task,"pixel_eyes",4096,NULL,1,NULL,1)==pdPASS);
}
