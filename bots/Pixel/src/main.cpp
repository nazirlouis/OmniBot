#include <Arduino.h>
#include <WiFi.h>
#include <esp_wifi.h> 
#include <Arduino_GFX_Library.h>
#include "esp_camera.h"
#include <driver/i2s.h>
#include <Preferences.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>
#include <JPEGDEC.h>
#include <esp_heap_caps.h>
#include <Wire.h> 
#include <RTClib.h>
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include <time.h>

// ==========================================
//        USER CONFIGURATION
// ==========================================
const char* backend_ip = "10.0.0.180"; 
const int backend_port = 8000;

#define BLE_SERVICE_NAME "Pixel"
#define SERVICE_UUID           "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define WIFI_CREDS_CHAR_UUID   "12345678-1234-5678-1234-56789abcdef0"

// ==========================================
//            PIN DEFINITIONS
// ==========================================
#define TFT_SCK    D8
#define TFT_MOSI   D10
#define TFT_MISO   D9
#define TFT_CS     D1
#define TFT_DC     D3
#define TFT_RST    -1  
#define TFT_BL     D6

// Seeed Round Display Touch & RTC Pins
#define TOUCH_INT  D7 
#define TOUCH_SDA  D4
#define TOUCH_SCL  D5
#define CHSC6X_I2C_ID 0x2e

#define I2S_WS     42
#define I2S_SD     41

// Camera Pins
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13

// ==========================================
//            GLOBALS
// ==========================================
Arduino_DataBus *bus = new Arduino_ESP32SPI(TFT_DC, TFT_CS, TFT_SCK, TFT_MOSI, TFT_MISO);
Arduino_GFX *gfx = new Arduino_GC9A01(bus, TFT_RST, 0, true);
Preferences preferences;
RTC_PCF8563 rtc;

#define SAMPLE_RATE 16000
#define MAX_RECORD_SECONDS 15 
#define FLASH_RECORD_SIZE (SAMPLE_RATE * MAX_RECORD_SECONDS * 2) 

uint8_t *rec_buffer = NULL; 
size_t recordedAudioLen = 0;

#define MAX_FRAMES 150
uint8_t* video_frames[MAX_FRAMES];
size_t video_frame_sizes[MAX_FRAMES];
int video_frame_count = 0;

WebSocketsClient webSocket;
bool isWsConnected = false;

#define FRAME_INTERVAL_MS 100 
unsigned long lastFrameTime = 0;

enum RobotState { STATE_IDLE, STATE_RECORDING, STATE_SETUP, STATE_UPLOADING, STATE_SETTINGS };
volatile RobotState currentState = STATE_SETUP;

// Touch & Gesture Tracking Globals
int startX = -1, startY = -1;
int endX = -1, endY = -1;
unsigned long touchStartTime = 0;
unsigned long lastActiveTouchTime = 0;
bool isTouching = false;

// Timers & Debounce Globals
unsigned long lastTapTime = 0; 
const int tapCooldown = 500; 
unsigned long lastRtcPrintTime = 0;
bool timeScreenActive = false;
unsigned long lastTimeScreenUpdate = 0;
String lastRenderedDay = "";
String lastRenderedDate = "";
String lastRenderedTime = "";

unsigned long lastRecordingEyesUpdate = 0;
unsigned long lastIdleEyesUpdate = 0;
unsigned long lastUploadingEyesUpdate = 0;

bool needsRestart = false;
volatile bool triggerUpload = false;
volatile bool geminiFirstTokenReceived = false;

// When false, skip JPEG capture during record (matches app "Vision" / server use_vision).
bool deviceVisionCaptureEnabled = true;

// Settings screen
bool settingsScreenNeedsRedraw = true;

// Weather overlay (from backend show_weather tool)
#define WEATHER_KIND_SUNNY 0
#define WEATHER_KIND_CLOUDY 1
#define WEATHER_KIND_PARTIALLY_CLOUDY 2
#define WEATHER_KIND_RAINING 3
#define WEATHER_KIND_SNOWING 4

bool weatherOverlayActive = false;
uint32_t weatherOverlayUntilMs = 0;
uint8_t weatherKind = WEATHER_KIND_CLOUDY;
int weatherTempDisplay = 0;
bool weatherNeedsRedraw = true;
// After show_weather during upload, do not resume thinking when the overlay ends.
bool suppressUploadThinkingAfterWeather = false;

// Map snapshot from hub (face_animation map + binary 0x04 JPEG)
#define MAP_DISPLAY_PX 240
#define MAP_OVERLAY_EXTEND_MS 9000
bool mapOverlayActive = false;
bool mapHasImage = false;
uint32_t mapOverlayUntilMs = 0;
bool mapNeedsRedraw = false;
bool suppressUploadThinkingAfterMap = false;
static uint16_t *s_mapRgb565 = nullptr;
static JPEGDEC s_jpegDec;

static int mapJpegDrawCallback(JPEGDRAW *pDraw) {
    if (!s_mapRgb565) {
        return 0;
    }
    uint16_t *src = pDraw->pPixels;
    int16_t x = pDraw->x;
    int16_t y = pDraw->y;
    int16_t w = pDraw->iWidth;
    int16_t h = pDraw->iHeight;
    for (int16_t row = 0; row < h; row++) {
        uint16_t *dst = s_mapRgb565 + (y + row) * MAP_DISPLAY_PX + x;
        memcpy(dst, src + row * w, w * sizeof(uint16_t));
    }
    return 1;
}

bool faceAnimActive = false;
uint32_t faceAnimStartMs = 0;
uint32_t faceAnimUntilMs = 0;
uint8_t faceAnimMode = 0; // 0=speaking, 1=happy, 2=mad
// Snapshot idle gaze when happy/mad animation starts (no drift during animation).
int16_t happyFrozenLookX = 0;
int16_t happyFrozenLookY = 0;
int16_t madFrozenLookX = 0;
int16_t madFrozenLookY = 0;
// Up to two words from face_animation tool (shown under eyes).
char faceAnimWords[64] = "";

#define FACE_ANIM_DURATION_MS 2500

static void setFaceAnimWordsFromPayload(const char* src) {
    faceAnimWords[0] = '\0';
    if (!src || !src[0]) {
        return;
    }
    char buf[80];
    memset(buf, 0, sizeof(buf));
    strncpy(buf, src, sizeof(buf) - 1);
    char* p1 = strtok(buf, " \t\r\n");
    if (!p1) {
        return;
    }
    char* p2 = strtok(NULL, " \t\r\n");
    if (p2) {
        snprintf(faceAnimWords, sizeof(faceAnimWords), "%s %s", p1, p2);
    } else {
        strncpy(faceAnimWords, p1, sizeof(faceAnimWords) - 1);
        faceAnimWords[sizeof(faceAnimWords) - 1] = '\0';
    }
}

static bool parseWeatherWords(const char* src, uint8_t* outKind, int* outTempF) {
    if (!src || !outKind || !outTempF) return false;
    char buf[80];
    memset(buf, 0, sizeof(buf));
    strncpy(buf, src, sizeof(buf) - 1);
    char* p1 = strtok(buf, " \t\r\n");
    char* p2 = strtok(NULL, " \t\r\n");
    if (!p1 || !p2) return false;

    if (strcmp(p1, "sunny") == 0) *outKind = WEATHER_KIND_SUNNY;
    else if (strcmp(p1, "cloudy") == 0) *outKind = WEATHER_KIND_CLOUDY;
    else if (strcmp(p1, "partially_cloudy") == 0) *outKind = WEATHER_KIND_PARTIALLY_CLOUDY;
    else if (strcmp(p1, "raining") == 0) *outKind = WEATHER_KIND_RAINING;
    else if (strcmp(p1, "snowing") == 0) *outKind = WEATHER_KIND_SNOWING;
    else return false;

    *outTempF = atoi(p2);
    return true;
}

// Caption is static for the whole speaking animation: draw once, skip until words change or end.
static char s_faceCaptionLastText[64] = "";
static int16_t s_faceCaptionBoxX = 0;
static int16_t s_faceCaptionBoxY = 0;
static int16_t s_faceCaptionBoxW = 0;
static int16_t s_faceCaptionBoxH = 0;
static bool s_faceCaptionVisible = false;

static void clearFaceAnimCaptionBox() {
    if (s_faceCaptionVisible) {
        gfx->fillRect(s_faceCaptionBoxX, s_faceCaptionBoxY, s_faceCaptionBoxW, s_faceCaptionBoxH, BLACK);
        s_faceCaptionVisible = false;
    }
    s_faceCaptionLastText[0] = '\0';
}

static void drawFaceAnimWordsLine() {
    if (!faceAnimWords[0]) {
        clearFaceAnimCaptionBox();
        return;
    }
    // Same string as last draw: do nothing (avoids flicker from per-frame clear+redraw).
    if (s_faceCaptionVisible && strcmp(faceAnimWords, s_faceCaptionLastText) == 0) {
        return;
    }

    clearFaceAnimCaptionBox();

    const uint8_t textSize = 2;
    const int16_t charW = 6 * textSize;
    const int16_t charH = 8 * textSize;
    const int16_t y = 182;
    int len = (int)strlen(faceAnimWords);
    if (len < 1) {
        return;
    }
    int16_t x = 120 - (len * charW) / 2;
    if (x < 4) {
        x = 4;
    }
    int16_t w = (int16_t)(len * charW);
    gfx->fillRect(x - 2, y - 2, w + 4, charH + 4, BLACK);
    gfx->setTextSize(textSize);
    gfx->setTextColor(WHITE);
    gfx->setCursor(x, y);
    gfx->print(faceAnimWords);

    strncpy(s_faceCaptionLastText, faceAnimWords, sizeof(s_faceCaptionLastText));
    s_faceCaptionLastText[sizeof(s_faceCaptionLastText) - 1] = '\0';
    s_faceCaptionBoxX = x - 2;
    s_faceCaptionBoxY = y - 2;
    s_faceCaptionBoxW = w + 4;
    s_faceCaptionBoxH = charH + 4;
    s_faceCaptionVisible = true;
}

// Weather overlay motion (delta-friendly: avoid full-screen clears on each tick).
struct WeatherAnimState {
    uint32_t lastTickMs = 0;
    bool sunnyHasPrevSeg = false;
    int16_t su_ix[8], su_iy[8], su_ox[8], su_oy[8];
    int16_t cloudDx = 0;
    int16_t cloudDy = 0;
    float cloudPhase = 0.0f;
    float sunnyPhase = 0.0f;
    struct RainDrop {
        int16_t x0;
        int16_t y0;
    } rain[9];
};
static WeatherAnimState wAnim;
const uint8_t WIFI_CONNECT_ATTEMPTS = 3;
const uint16_t WIFI_CONNECT_TIMEOUT_MS = 15000;
const uint8_t WIFI_FAILS_BEFORE_BLE_FALLBACK = 3;
const uint8_t WIFI_FAILS_BEFORE_CREDS_CLEAR = 8;
const uint32_t RTC_WIFI_SYNC_INTERVAL_MS = 3600000UL; // 1 hour
const char* DEFAULT_TIMEZONE_RULE = "EST5EDT,M3.2.0/2,M11.1.0/2";
char activeTimezoneRule[64] = {0};
unsigned long lastRtcWifiSyncAttemptMs = 0;

struct IdleAnimState {
    int16_t lookX = 0;
    int16_t lookY = 0;
    int16_t targetLookX = 0;
    int16_t targetLookY = 0;
    uint32_t nextLookChangeMs = 0;
    uint32_t lookHoldUntilMs = 0;

    uint32_t blinkStartMs = 0;
    uint16_t blinkDurationMs = 0;
    uint32_t nextBlinkMs = 0;

    uint8_t expressionType = 0;
    bool expressionActive = false;
    uint32_t expressionStartMs = 0;
    uint16_t expressionDurationMs = 0;
    uint32_t nextExpressionMs = 0;
    int8_t expressionSide = 0; // -1 left, +1 right (used by wink)

    bool focusLockActive = false;
    uint32_t focusLockUntilMs = 0;

    int16_t prevEyeXOffset = 0;
    int16_t prevEyeYOffset = 0;
    int16_t prevEyeRyL = 66;
    int16_t prevEyeRyR = 66;
    bool hasPrevFrame = false;
};

IdleAnimState idleAnim;

struct UploadAnimState {
    int16_t prevEyeXOffset = 0;
    int16_t prevEyeYOffset = 0;
    int16_t prevEyeRyL = 26;
    int16_t prevEyeRyR = 24;
    int16_t prevBrowOffset = 0;
    uint8_t prevQSize = 0;
    bool hasPrevFrame = false;
};

UploadAnimState uploadAnim;

struct RecordingAnimState {
    int16_t prevEyeXOffset = 0;
    int16_t prevEyeYOffset = 0;
    int16_t prevEyeRyL = 52;
    int16_t prevEyeRyR = 52;
    bool hasPrevFrame = false;
};

RecordingAnimState recordingAnim;

// ==========================================
//          UI HELPER
// ==========================================
void updateScreen(String text, uint16_t color) {
    gfx->fillScreen(BLACK);
    gfx->setTextColor(color);
    gfx->setTextSize(3);
    
    int cursorX = 120 - (text.length() * 9); 
    if (cursorX < 10) cursorX = 10;
    
    gfx->setCursor(cursorX, 110);
    gfx->print(text);
}

static void fillOval(int16_t xc, int16_t yc, int16_t rx, int16_t ry, uint16_t color) {
    if (rx <= 0 || ry <= 0) return;

    int32_t rx2 = (int32_t)rx * (int32_t)rx;
    int32_t ry2 = (int32_t)ry * (int32_t)ry;

    // Scanline ellipse fill.
    for (int16_t dy = -ry; dy <= ry; dy++) {
        int32_t dy2 = (int32_t)dy * (int32_t)dy;
        int32_t t = ry2 - dy2;
        if (t < 0) continue;

        // x = rx * sqrt(1 - dy^2 / ry^2) = sqrt(rx^2 * (1 - dy^2/ry^2))
        int32_t x = (int32_t)(sqrtf((float)(rx2 * t) / (float)ry2) + 0.5f);
        if (x < 0) continue;

        gfx->drawFastHLine(xc - (int16_t)x, yc + dy, (int16_t)(2 * x + 1), color);
    }
}

// Scale s roughly 1.0 = previous look; >1 enlarges clouds (round display).
static void drawWeatherCloud(int16_t x, int16_t y, uint16_t cloudGray, uint16_t cloudHi, float s) {
    int16_t a = (int16_t)(28 * s), b = (int16_t)(18 * s);
    int16_t ox = (int16_t)(22 * s), oy = (int16_t)(6 * s);
    fillOval(x, y, a, b, cloudGray);
    fillOval(x - ox, y + oy, (int16_t)(24 * s), (int16_t)(16 * s), cloudGray);
    fillOval(x + (int16_t)(20 * s), y + oy, (int16_t)(26 * s), (int16_t)(18 * s), cloudGray);
    // Soft highlight on main puff
    fillOval(x - (int16_t)(8 * s), y - (int16_t)(5 * s), (int16_t)(10 * s), (int16_t)(7 * s), cloudHi);
}

// ---- Weather layout (shared: full paint + delta animation ticks) ----
static const uint16_t kWxBg = 0x10A2;
static const uint16_t kWxRing = 0x2124;
static const uint16_t kWxCloud = 0xB5B6;
static const uint16_t kWxCloudHi = 0xDEFB;
static const uint16_t kWxTemp = 0xEF9D;
static const uint16_t kWxAccent = 0x5D9B;
static const int16_t kWxCx = 120;
static const int16_t kWxIconY = 92;
static const float kWxIconScale = 1.65f;
static const int16_t kWxTempCyTop = 142;
static const uint16_t kWxAnimIntervalMs = 72;
// Large decorative snowflake (no cloud) for WEATHER_KIND_SNOWING.
static const int16_t kWxFlakeCx = 120;
static const int16_t kWxFlakeCy = 86;
static const int16_t kWxFlakeArm = 54;

static void drawGiantSnowflake(int16_t cx, int16_t cy, int16_t armLen, uint16_t col) {
    const float pi = 3.1415926f;
    const int16_t hubR = 5;
    fillOval(cx, cy, hubR, hubR, col);

    for (int arm = 0; arm < 6; arm++) {
        float a = (float)arm * pi / 3.0f;
        int16_t xe = cx + (int16_t)(cosf(a) * (float)armLen);
        int16_t ye = cy + (int16_t)(sinf(a) * (float)armLen);
        gfx->drawLine(cx, cy, xe, ye, col);

        float mx = cosf(a) * (float)armLen * 0.52f;
        float my = sinf(a) * (float)armLen * 0.52f;
        int16_t mx_i = cx + (int16_t)mx;
        int16_t my_i = cy + (int16_t)my;
        const int16_t br = (int16_t)(armLen / 4);
        gfx->drawLine(mx_i, my_i, mx_i + (int16_t)(cosf(a + 0.65f) * (float)br), my_i + (int16_t)(sinf(a + 0.65f) * (float)br), col);
        gfx->drawLine(mx_i, my_i, mx_i + (int16_t)(cosf(a - 0.65f) * (float)br), my_i + (int16_t)(sinf(a - 0.65f) * (float)br), col);

        float tx = cosf(a) * (float)armLen * 0.78f;
        float ty = sinf(a) * (float)armLen * 0.78f;
        int16_t tx_i = cx + (int16_t)tx;
        int16_t ty_i = cy + (int16_t)ty;
        const int16_t tip = (int16_t)(armLen / 5);
        gfx->drawLine(tx_i, ty_i, tx_i + (int16_t)(cosf(a + 0.5f) * (float)tip), ty_i + (int16_t)(sinf(a + 0.5f) * (float)tip), col);
        gfx->drawLine(tx_i, ty_i, tx_i + (int16_t)(cosf(a - 0.5f) * (float)tip), ty_i + (int16_t)(sinf(a - 0.5f) * (float)tip), col);
    }
}

static void drawTemperatureLineF(int16_t cyTop, uint8_t textSize, uint16_t textColor) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%dF", weatherTempDisplay);

    gfx->setTextSize(textSize);
    // Match weather panel bg so glyphs are not outlined in default black.
    gfx->setTextColor(textColor, kWxBg);

    const int16_t charW = (int16_t)(6 * textSize);
    const int16_t lineH = (int16_t)(8 * textSize);
    const int16_t totalW = (int16_t)strlen(buf) * charW;
    int16_t startX = (240 - totalW) / 2;
    if (startX < 4) startX = 4;

    const int16_t baselineY = cyTop + lineH - (int16_t)(textSize > 3 ? 3 : 2);

    gfx->setCursor(startX, baselineY);
    gfx->print(buf);
}

// Cloud / sun art only — icon band stays below temperature.
static void wxClearIconBand() {
    gfx->fillRect(12, 22, 216, 112, kWxBg);
    gfx->drawCircle(120, 120, 118, kWxRing);
}

static void wxRedrawTemperatureOnly() {
    gfx->fillRect(8, kWxTempCyTop, 224, 48, kWxBg);
    drawTemperatureLineF(kWxTempCyTop, 5, kWxTemp);
}

static void wxInitAnimAfterFullPaint() {
    wAnim.lastTickMs = millis();
    wAnim.cloudDx = 0;
    wAnim.cloudDy = 0;
    wAnim.cloudPhase = (float)((millis() & 2047u)) * 0.002f;
    wAnim.sunnyPhase = 0.0f;

    if (weatherKind == WEATHER_KIND_SUNNY) {
        const int16_t cx = kWxCx;
        const int16_t iconY = kWxIconY;
        const float sc = kWxIconScale;
        for (int i = 0; i < 8; i++) {
            float a = (float)i * 3.1415926f / 4.0f;
            wAnim.su_ix[i] = cx + (int16_t)(cosf(a) * (38.0f * sc));
            wAnim.su_iy[i] = iconY + (int16_t)(sinf(a) * (38.0f * sc));
            wAnim.su_ox[i] = cx + (int16_t)(cosf(a) * (52.0f * sc));
            wAnim.su_oy[i] = iconY + (int16_t)(sinf(a) * (52.0f * sc));
        }
        wAnim.sunnyHasPrevSeg = true;
    } else {
        wAnim.sunnyHasPrevSeg = false;
    }

    if (weatherKind == WEATHER_KIND_RAINING) {
        int n = 0;
        for (int i = -1; i <= 1; i++) {
            int16_t rx = kWxCx + (int16_t)(i * 22 * kWxIconScale);
            for (int j = 0; j < 3; j++) {
                wAnim.rain[n].x0 = rx + (int16_t)(j * 5 * kWxIconScale);
                wAnim.rain[n].y0 = kWxIconY + (int16_t)(28 * kWxIconScale) + (int16_t)((n * 5) % 18);
                n++;
            }
        }
    }
}

static void updateWxSunnyAnim() {
    const int16_t cx = kWxCx;
    const int16_t iconY = kWxIconY;
    const float sc = kWxIconScale;
    wAnim.sunnyPhase += 0.10f;

    for (int i = 0; i < 8; i++) {
        float a = (float)i * 3.1415926f / 4.0f;
        int16_t ix = cx + (int16_t)(cosf(a) * (38.0f * sc));
        int16_t iy = iconY + (int16_t)(sinf(a) * (38.0f * sc));
        float omul = 52.0f + sinf(wAnim.sunnyPhase + (float)i * 0.55f) * 6.5f;
        int16_t ox = cx + (int16_t)(cosf(a) * (omul * sc));
        int16_t oy = iconY + (int16_t)(sinf(a) * (omul * sc));

        if (wAnim.sunnyHasPrevSeg) {
            gfx->drawLine(wAnim.su_ix[i], wAnim.su_iy[i], wAnim.su_ox[i], wAnim.su_oy[i], kWxBg);
        }
        gfx->drawLine(ix, iy, ox, oy, 0xFF80);
        wAnim.su_ix[i] = ix;
        wAnim.su_iy[i] = iy;
        wAnim.su_ox[i] = ox;
        wAnim.su_oy[i] = oy;
    }
    wAnim.sunnyHasPrevSeg = true;
}

static void updateWxCloudyAnim() {
    wAnim.cloudPhase += 0.085f;
    int16_t ndx = (int16_t)(sinf(wAnim.cloudPhase) * 2.0f);
    int16_t ndy = (int16_t)(cosf(wAnim.cloudPhase * 0.82f) * 1.5f);
    if (ndx == wAnim.cloudDx && ndy == wAnim.cloudDy) {
        return;
    }

    wxClearIconBand();
    drawWeatherCloud(kWxCx + ndx, kWxIconY + ndy, kWxCloud, kWxCloudHi, kWxIconScale);
    wAnim.cloudDx = ndx;
    wAnim.cloudDy = ndy;
}

static void updateWxPartlyAnim() {
    wAnim.cloudPhase += 0.085f;
    int16_t ndx = (int16_t)(sinf(wAnim.cloudPhase) * 2.0f);
    int16_t ndy = (int16_t)(cosf(wAnim.cloudPhase * 0.82f) * 1.5f);
    if (ndx == wAnim.cloudDx && ndy == wAnim.cloudDy) {
        return;
    }

    wxClearIconBand();
    fillOval(kWxCx - (int16_t)(18 * kWxIconScale), kWxIconY - (int16_t)(14 * kWxIconScale),
             (int16_t)(20 * kWxIconScale), (int16_t)(20 * kWxIconScale), 0xFEA0);
    fillOval(kWxCx - (int16_t)(18 * kWxIconScale), kWxIconY - (int16_t)(14 * kWxIconScale),
             (int16_t)(12 * kWxIconScale), (int16_t)(12 * kWxIconScale), YELLOW);
    drawWeatherCloud(
        kWxCx + ndx + (int16_t)(8 * kWxIconScale),
        kWxIconY + ndy + (int16_t)(8 * kWxIconScale),
        kWxCloud, kWxCloudHi, kWxIconScale * 0.95f
    );
    wAnim.cloudDx = ndx;
    wAnim.cloudDy = ndy;
}

static void updateWxRainAnim() {
    const float sc = kWxIconScale;
    const int16_t dropLen = (int16_t)(26 * sc);
    const int16_t dx = (int16_t)(4 * sc);
    const int16_t yTopMin = kWxIconY + (int16_t)(26 * sc);
    const int16_t yTopMax = kWxIconY + (int16_t)(56 * sc);

    for (int i = 0; i < 9; i++) {
        int16_t x0 = wAnim.rain[i].x0;
        int16_t y0 = wAnim.rain[i].y0;
        gfx->drawLine(x0, y0, (int16_t)(x0 - dx), (int16_t)(y0 + dropLen), kWxBg);
        y0 += 5;
        if (y0 > yTopMax) {
            y0 = (int16_t)(yTopMin + (i * 4) % 14);
        }
        wAnim.rain[i].y0 = y0;
        gfx->drawLine(x0, y0, (int16_t)(x0 - dx), (int16_t)(y0 + dropLen), kWxAccent);
    }
}

static void updateWeatherOverlayAnimation(uint32_t now) {
    if (now - wAnim.lastTickMs < kWxAnimIntervalMs) {
        return;
    }
    wAnim.lastTickMs = now;

    switch (weatherKind) {
        case WEATHER_KIND_SUNNY:
            updateWxSunnyAnim();
            break;
        case WEATHER_KIND_CLOUDY:
            updateWxCloudyAnim();
            break;
        case WEATHER_KIND_PARTIALLY_CLOUDY:
            updateWxPartlyAnim();
            break;
        case WEATHER_KIND_RAINING:
            updateWxRainAnim();
            wxRedrawTemperatureOnly();
            break;
        case WEATHER_KIND_SNOWING:
            // Static giant snowflake; full overlay handles paint.
            break;
        default:
            updateWxCloudyAnim();
            break;
    }
}

static void drawWeatherOverlay() {
    gfx->fillScreen(kWxBg);
    gfx->drawCircle(120, 120, 118, kWxRing);
    wxInitAnimAfterFullPaint();

    switch (weatherKind) {
        case WEATHER_KIND_SUNNY: {
            int16_t r = (int16_t)(28 * kWxIconScale);
            fillOval(kWxCx, kWxIconY, r, r, 0xFEA0);
            fillOval(kWxCx, kWxIconY, (int16_t)(r * 0.55f), (int16_t)(r * 0.55f), YELLOW);
            for (int i = 0; i < 8; i++) {
                float a = (float)i * 3.1415926f / 4.0f;
                int16_t x1 = kWxCx + (int16_t)(cosf(a) * (38.0f * kWxIconScale));
                int16_t y1 = kWxIconY + (int16_t)(sinf(a) * (38.0f * kWxIconScale));
                int16_t x2 = kWxCx + (int16_t)(cosf(a) * (52.0f * kWxIconScale));
                int16_t y2 = kWxIconY + (int16_t)(sinf(a) * (52.0f * kWxIconScale));
                gfx->drawLine(x1, y1, x2, y2, 0xFF80);
            }
            break;
        }
        case WEATHER_KIND_CLOUDY:
            drawWeatherCloud(kWxCx, kWxIconY, kWxCloud, kWxCloudHi, kWxIconScale);
            break;
        case WEATHER_KIND_PARTIALLY_CLOUDY:
            fillOval(kWxCx - (int16_t)(18 * kWxIconScale), kWxIconY - (int16_t)(14 * kWxIconScale),
                     (int16_t)(20 * kWxIconScale), (int16_t)(20 * kWxIconScale), 0xFEA0);
            fillOval(kWxCx - (int16_t)(18 * kWxIconScale), kWxIconY - (int16_t)(14 * kWxIconScale),
                     (int16_t)(12 * kWxIconScale), (int16_t)(12 * kWxIconScale), YELLOW);
            drawWeatherCloud(
                kWxCx + (int16_t)(8 * kWxIconScale),
                kWxIconY + (int16_t)(8 * kWxIconScale),
                kWxCloud, kWxCloudHi, kWxIconScale * 0.95f
            );
            break;
        case WEATHER_KIND_RAINING:
            drawWeatherCloud(kWxCx, kWxIconY - (int16_t)(6 * kWxIconScale), kWxCloud, kWxCloudHi, kWxIconScale);
            for (int i = -1; i <= 1; i++) {
                int16_t rx = kWxCx + (int16_t)(i * 22 * kWxIconScale);
                for (int j = 0; j < 3; j++) {
                    int16_t x0 = rx + (int16_t)(j * 5 * kWxIconScale);
                    gfx->drawLine(x0, kWxIconY + (int16_t)(28 * kWxIconScale),
                                  (int16_t)(x0 - (int16_t)(4 * kWxIconScale)),
                                  kWxIconY + (int16_t)(54 * kWxIconScale), kWxAccent);
                }
            }
            break;
        case WEATHER_KIND_SNOWING:
            drawGiantSnowflake(kWxFlakeCx, kWxFlakeCy, kWxFlakeArm, 0xEFBF);
            break;
        default:
            drawWeatherCloud(kWxCx, kWxIconY, kWxCloud, kWxCloudHi, kWxIconScale);
            break;
    }

    drawTemperatureLineF(kWxTempCyTop, 5, kWxTemp);
}

void drawEyes(int16_t eyeXOffset, int16_t eyeYOffset, int16_t eyeRyL, int16_t eyeRyR, uint16_t color) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68 + eyeXOffset;
    const int16_t cxR = 172 + eyeXOffset;

    fillOval(cxL, baseY + eyeYOffset, rx, eyeRyL, color);
    fillOval(cxR, baseY + eyeYOffset, rx, eyeRyR, color);
}

static bool ovalSpanAtY(int16_t xc, int16_t yc, int16_t rx, int16_t ry, int16_t y, int16_t &x1, int16_t &x2) {
    if (rx <= 0 || ry <= 0) return false;

    int16_t dy = y - yc;
    if (dy < -ry || dy > ry) return false;

    int32_t rx2 = (int32_t)rx * (int32_t)rx;
    int32_t ry2 = (int32_t)ry * (int32_t)ry;
    int32_t dy2 = (int32_t)dy * (int32_t)dy;
    int32_t t = ry2 - dy2;
    if (t < 0) return false;

    int16_t half = (int16_t)(sqrtf((float)(rx2 * t) / (float)ry2) + 0.5f);
    x1 = xc - half;
    x2 = xc + half;
    return true;
}

static void drawSpanIfValid(int16_t y, int16_t x1, int16_t x2, uint16_t color) {
    if (x2 < x1) return;
    gfx->drawFastHLine(x1, y, x2 - x1 + 1, color);
}

static void drawSpanDelta(
    int16_t y,
    bool hadOld, int16_t oldX1, int16_t oldX2,
    bool hasNew, int16_t newX1, int16_t newX2,
    uint16_t onColor, uint16_t offColor
) {
    if (hadOld && !hasNew) {
        drawSpanIfValid(y, oldX1, oldX2, offColor);
        return;
    }
    if (!hadOld && hasNew) {
        drawSpanIfValid(y, newX1, newX2, onColor);
        return;
    }
    if (!hadOld && !hasNew) return;

    // Both exist. Only update non-overlapping segments.
    drawSpanIfValid(y, oldX1, min(oldX2, (int16_t)(newX1 - 1)), offColor);
    drawSpanIfValid(y, max(oldX1, (int16_t)(newX2 + 1)), oldX2, offColor);
    drawSpanIfValid(y, newX1, min(newX2, (int16_t)(oldX1 - 1)), onColor);
    drawSpanIfValid(y, max(newX1, (int16_t)(oldX2 + 1)), newX2, onColor);
}

// Interval A \ B on 1D line; up to two disjoint segments (inclusive endpoints).
static uint8_t subtractSpan1D(
    int16_t a1,
    int16_t a2,
    int16_t b1,
    int16_t b2,
    int16_t* o1a,
    int16_t* o1b,
    int16_t* o2a,
    int16_t* o2b
) {
    if (a2 < a1) {
        return 0;
    }
    if (b2 < b1 || b1 > a2 || b2 < a1) {
        *o1a = a1;
        *o1b = a2;
        return 1;
    }
    int16_t lo = max(a1, b1);
    int16_t hi = min(a2, b2);
    if (lo > hi) {
        *o1a = a1;
        *o1b = a2;
        return 1;
    }
    uint8_t n = 0;
    if (a1 < lo) {
        *o1a = a1;
        *o1b = lo - 1;
        if (*o1b >= *o1a) {
            n++;
        }
    }
    if (hi < a2) {
        if (n == 0) {
            *o1a = hi + 1;
            *o1b = a2;
            if (*o1b >= *o1a) {
                n++;
            }
        } else {
            *o2a = hi + 1;
            *o2b = a2;
            if (*o2b >= *o2a) {
                n++;
            }
        }
    }
    return n;
}

// Happy eye = eye ellipse minus lower carve ellipse (same math as former black overlay).
static void happyEyeSpansAtY(
    int16_t cx,
    int16_t yc,
    int16_t rx,
    int16_t ry,
    int16_t y,
    int16_t* s1a,
    int16_t* s1b,
    int16_t* s2a,
    int16_t* s2b,
    uint8_t* nSeg
) {
    int16_t ea1 = 0, ea2 = -1, ca1 = 0, ca2 = -1;
    bool hasE = ovalSpanAtY(cx, yc, rx, ry, y, ea1, ea2);
    if (!hasE) {
        *nSeg = 0;
        return;
    }
    int16_t cutOff = ry / 2;
    bool hasC = ovalSpanAtY(cx, yc + cutOff, (int16_t)(rx + 1), ry, y, ca1, ca2);
    if (!hasC) {
        *s1a = ea1;
        *s1b = ea2;
        *nSeg = 1;
        return;
    }
    *nSeg = subtractSpan1D(ea1, ea2, ca1, ca2, s1a, s1b, s2a, s2b);
}

// First scanline (inclusive) where mad eye may draw after removing top 50% of bbox [yc-ry, yc+ry].
static int16_t madFlatCutYShowMin(int16_t yc, int16_t ry) {
    return yc; // (yc - ry) + ry: bottom half of vertical extent only
}

// Mad eye = eye ellipse with top 50% of vertical bbox removed (flat horizontal cut / band).
static void madEyeSpansAtY(
    int16_t cx,
    int16_t yc,
    int16_t rx,
    int16_t ry,
    int16_t y,
    int16_t* s1a,
    int16_t* s1b,
    int16_t* s2a,
    int16_t* s2b,
    uint8_t* nSeg
) {
    (void)s2a;
    (void)s2b;
    int16_t ea1 = 0, ea2 = -1;
    bool hasE = ovalSpanAtY(cx, yc, rx, ry, y, ea1, ea2);
    if (!hasE) {
        *nSeg = 0;
        return;
    }
    int16_t yShowMin = madFlatCutYShowMin(yc, ry);
    if (y < yShowMin) {
        *nSeg = 0;
        return;
    }
    *s1a = ea1;
    *s1b = ea2;
    *nSeg = 1;
}

static void drawHappyEyesFull(int16_t lookX, int16_t lookY, int16_t ryL, int16_t ryR) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68 + lookX;
    const int16_t cxR = 172 + lookX;
    const int16_t yc = baseY + lookY;
    int16_t rmax = max(ryL, ryR);
    int16_t yMin = yc - rmax - 2;
    int16_t yMax = yc + rmax + (3 * rmax) / 2 + 3;
    if (yMin < 0) {
        yMin = 0;
    }
    if (yMax > 239) {
        yMax = 239;
    }
    for (int16_t y = yMin; y <= yMax; y++) {
        int16_t s1a, s1b, s2a, s2b;
        uint8_t n;
        happyEyeSpansAtY(cxL, yc, rx, ryL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, CYAN);
        }
        happyEyeSpansAtY(cxR, yc, rx, ryR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, CYAN);
        }
    }
}

static void drawHappyEyesDelta(
    int16_t lookX,
    int16_t lookY,
    int16_t oldRyL,
    int16_t oldRyR,
    int16_t newRyL,
    int16_t newRyR
) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68 + lookX;
    const int16_t cxR = 172 + lookX;
    const int16_t yc = baseY + lookY;
    int16_t rmax = max(max(oldRyL, oldRyR), max(newRyL, newRyR));
    int16_t yMin = yc - rmax - 2;
    int16_t yMax = yc + rmax + (3 * rmax) / 2 + 3;
    if (yMin < 0) {
        yMin = 0;
    }
    if (yMax > 239) {
        yMax = 239;
    }

    for (int16_t y = yMin; y <= yMax; y++) {
        int16_t s1a, s1b, s2a, s2b;
        uint8_t n;

        happyEyeSpansAtY(cxL, yc, rx, oldRyL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, BLACK);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, BLACK);
        }
        happyEyeSpansAtY(cxR, yc, rx, oldRyR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, BLACK);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, BLACK);
        }

        happyEyeSpansAtY(cxL, yc, rx, newRyL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, CYAN);
        }
        happyEyeSpansAtY(cxR, yc, rx, newRyR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        if (n >= 2) {
            drawSpanIfValid(y, s2a, s2b, CYAN);
        }
    }
}

static void drawMadEyesFull(int16_t lookX, int16_t lookY, int16_t ryL, int16_t ryR) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68 + lookX;
    const int16_t cxR = 172 + lookX;
    const int16_t yc = baseY + lookY;
    int16_t rmax = max(ryL, ryR);
    int16_t yShowMinL = madFlatCutYShowMin(yc, ryL);
    int16_t yShowMinR = madFlatCutYShowMin(yc, ryR);
    int16_t yMin = min(yShowMinL, yShowMinR) - 1;
    if (yMin < 0) {
        yMin = 0;
    }
    int16_t yMax = yc + rmax + 2;
    if (yMax > 239) {
        yMax = 239;
    }
    for (int16_t y = yMin; y <= yMax; y++) {
        int16_t s1a, s1b, s2a, s2b;
        uint8_t n;
        madEyeSpansAtY(cxL, yc, rx, ryL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        madEyeSpansAtY(cxR, yc, rx, ryR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
    }
}

static void drawMadEyesDelta(
    int16_t lookX,
    int16_t lookY,
    int16_t oldRyL,
    int16_t oldRyR,
    int16_t newRyL,
    int16_t newRyR
) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68 + lookX;
    const int16_t cxR = 172 + lookX;
    const int16_t yc = baseY + lookY;
    int16_t rmax = max(max(oldRyL, oldRyR), max(newRyL, newRyR));
    int16_t yMin = min(
        min(madFlatCutYShowMin(yc, oldRyL), madFlatCutYShowMin(yc, oldRyR)),
        min(madFlatCutYShowMin(yc, newRyL), madFlatCutYShowMin(yc, newRyR))
    ) - 1;
    if (yMin < 0) {
        yMin = 0;
    }
    int16_t yMax = yc + rmax + 2;
    if (yMax > 239) {
        yMax = 239;
    }

    for (int16_t y = yMin; y <= yMax; y++) {
        int16_t s1a, s1b, s2a, s2b;
        uint8_t n;

        madEyeSpansAtY(cxL, yc, rx, oldRyL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, BLACK);
        }
        madEyeSpansAtY(cxR, yc, rx, oldRyR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, BLACK);
        }

        madEyeSpansAtY(cxL, yc, rx, newRyL, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
        madEyeSpansAtY(cxR, yc, rx, newRyR, y, &s1a, &s1b, &s2a, &s2b, &n);
        if (n >= 1) {
            drawSpanIfValid(y, s1a, s1b, CYAN);
        }
    }
}

static void drawEyeDelta(
    int16_t oldCx,
    int16_t newCx,
    int16_t oldYc,
    int16_t newYc,
    int16_t rx,
    int16_t oldRy,
    int16_t newRy,
    uint16_t onColor,
    uint16_t offColor
) {
    int16_t yMin = min((int16_t)(oldYc - oldRy), (int16_t)(newYc - newRy));
    int16_t yMax = max((int16_t)(oldYc + oldRy), (int16_t)(newYc + newRy));

    for (int16_t y = yMin; y <= yMax; y++) {
        int16_t oldX1 = 0, oldX2 = -1;
        int16_t newX1 = 0, newX2 = -1;
        bool hadOld = ovalSpanAtY(oldCx, oldYc, rx, oldRy, y, oldX1, oldX2);
        bool hasNew = ovalSpanAtY(newCx, newYc, rx, newRy, y, newX1, newX2);
        drawSpanDelta(y, hadOld, oldX1, oldX2, hasNew, newX1, newX2, onColor, offColor);
    }
}

void drawEyesDelta(
    int16_t oldXOffset,
    int16_t oldYOffset,
    int16_t oldRyL,
    int16_t oldRyR,
    int16_t newXOffset,
    int16_t newYOffset,
    int16_t newRyL,
    int16_t newRyR,
    uint16_t onColor
) {
    const int16_t baseY = 112;
    const int16_t rx = 46;
    const int16_t oldCxL = 68 + oldXOffset;
    const int16_t oldCxR = 172 + oldXOffset;
    const int16_t newCxL = 68 + newXOffset;
    const int16_t newCxR = 172 + newXOffset;
    const int16_t oldYc = baseY + oldYOffset;
    const int16_t newYc = baseY + newYOffset;

    drawEyeDelta(oldCxL, newCxL, oldYc, newYc, rx, oldRyL, newRyL, onColor, BLACK);
    drawEyeDelta(oldCxR, newCxR, oldYc, newYc, rx, oldRyR, newRyR, onColor, BLACK);
}

static int16_t triPulse(uint32_t elapsed, uint16_t durationMs, int16_t amplitude) {
    if (durationMs == 0 || elapsed >= durationMs) return 0;
    float t = (float)elapsed / (float)durationMs;   // 0..1
    float tri = 1.0f - fabsf(2.0f * t - 1.0f);      // 0..1..0
    return (int16_t)(tri * (float)amplitude);
}

void showIdleEyes() {
    gfx->fillScreen(BLACK);

    idleAnim.lookX = 0;
    idleAnim.lookY = 0;
    idleAnim.targetLookX = 0;
    idleAnim.targetLookY = 0;
    idleAnim.lookHoldUntilMs = 0;
    idleAnim.nextLookChangeMs = millis() + (uint32_t)random(600, 1800);
    idleAnim.blinkStartMs = 0;
    idleAnim.blinkDurationMs = 0;
    idleAnim.nextBlinkMs = millis() + (uint32_t)random(2200, 5200);
    idleAnim.expressionType = 0;
    idleAnim.expressionActive = false;
    idleAnim.expressionStartMs = 0;
    idleAnim.expressionDurationMs = 0;
    idleAnim.nextExpressionMs = millis() + (uint32_t)random(1800, 4800);
    idleAnim.expressionSide = 0;
    idleAnim.focusLockActive = false;
    idleAnim.focusLockUntilMs = 0;
    idleAnim.prevEyeXOffset = 0;
    idleAnim.prevEyeYOffset = 0;
    idleAnim.prevEyeRyL = 66;
    idleAnim.prevEyeRyR = 66;
    idleAnim.hasPrevFrame = true;

    drawEyes(0, 0, 66, 66, CYAN);
}

void updateIdleEyesAnimation() {
    const uint32_t now = millis();

    // Throttle redraw to avoid unnecessary TFT work.
    if (now - lastIdleEyesUpdate < 50) return;
    lastIdleEyesUpdate = now;

    // Every so often, pick a new "look around" target then hold.
    if (idleAnim.focusLockActive && now >= idleAnim.focusLockUntilMs) {
        idleAnim.focusLockActive = false;
        idleAnim.targetLookX = 0;
        idleAnim.targetLookY = 0;
        idleAnim.lookHoldUntilMs = now + (uint32_t)random(300, 700);
    }

    if (!idleAnim.focusLockActive && now >= idleAnim.nextLookChangeMs && now >= idleAnim.lookHoldUntilMs) {
        idleAnim.targetLookX = random(-10, 11);
        idleAnim.targetLookY = random(-5, 6);
        idleAnim.lookHoldUntilMs = now + (uint32_t)random(180, 450);
        idleAnim.nextLookChangeMs = now + (uint32_t)random(900, 2400);
    }

    // Smoothly chase target to make motion feel natural.
    idleAnim.lookX += (idleAnim.targetLookX - idleAnim.lookX) / 3;
    idleAnim.lookY += (idleAnim.targetLookY - idleAnim.lookY) / 3;

    // Baseline blink fallback when no special expression is running.
    if (!idleAnim.expressionActive && idleAnim.blinkStartMs == 0 && now >= idleAnim.nextBlinkMs) {
        idleAnim.blinkStartMs = now;
        idleAnim.blinkDurationMs = (uint16_t)random(120, 240);
        idleAnim.nextBlinkMs = now + (uint32_t)random(2500, 7000);
    }

    // Start a random expression at long-ish intervals.
    if (!idleAnim.expressionActive && now >= idleAnim.nextExpressionMs) {
        int roll = random(1000);
        idleAnim.expressionActive = true;
        idleAnim.expressionStartMs = now;
        idleAnim.expressionSide = (random(2) == 0) ? -1 : 1;

        // 1=double blink, 3=focus lock, 4=wink, 5=sleepy cycle
        if (roll < 420) {
            idleAnim.expressionType = 1;
            idleAnim.expressionDurationMs = 320; // 120 + 80 + 120
        } else if (roll < 760) {
            idleAnim.expressionType = 3;
            idleAnim.expressionDurationMs = (uint16_t)random(500, 1201);
            idleAnim.focusLockActive = true;
            idleAnim.focusLockUntilMs = now + idleAnim.expressionDurationMs;
            int16_t x = (random(2) == 0) ? random(-14, -7) : random(7, 15);
            idleAnim.targetLookX = x;
            idleAnim.targetLookY = random(-2, 3);
            idleAnim.lookHoldUntilMs = idleAnim.focusLockUntilMs;
        } else if (roll < 900) {
            idleAnim.expressionType = 4;
            idleAnim.expressionDurationMs = (uint16_t)random(120, 181);
        } else {
            idleAnim.expressionType = 5;
            idleAnim.expressionDurationMs = 500; // two heavy squints
        }
    }

    int16_t eyeRyL = 66;
    int16_t eyeRyR = 66;

    // Baseline single blink.
    if (idleAnim.blinkStartMs != 0) {
        uint32_t elapsed = now - idleAnim.blinkStartMs;
        if (elapsed >= idleAnim.blinkDurationMs) {
            idleAnim.blinkStartMs = 0;
        } else {
            int16_t sq = triPulse(elapsed, idleAnim.blinkDurationMs, 54);
            eyeRyL -= sq;
            eyeRyR -= sq;
        }
    }

    // Expression overlays.
    if (idleAnim.expressionActive) {
        uint32_t e = now - idleAnim.expressionStartMs;

        if (e >= idleAnim.expressionDurationMs) {
            idleAnim.expressionActive = false;
            idleAnim.expressionType = 0;
            idleAnim.nextExpressionMs = now + (uint32_t)random(2200, 7000);
        } else {
            if (idleAnim.expressionType == 1) {
                // Double blink: pulse, short pause, pulse.
                int16_t sq = 0;
                if (e < 120) sq = triPulse(e, 120, 56);
                else if (e >= 200 && e < 320) sq = triPulse(e - 200, 120, 56);
                eyeRyL -= sq;
                eyeRyR -= sq;
            } else if (idleAnim.expressionType == 4) {
                // Wink: one eye closes deeply.
                int16_t wink = triPulse(e, idleAnim.expressionDurationMs, 62);
                if (idleAnim.expressionSide < 0) eyeRyL -= wink;
                else eyeRyR -= wink;
            } else if (idleAnim.expressionType == 5) {
                // Sleepy cycle: two heavier, slower squints.
                int16_t sq = 0;
                if (e < 190) sq = triPulse(e, 190, 58);
                else if (e >= 310 && e < 500) sq = triPulse(e - 310, 190, 58);
                eyeRyL -= sq;
                eyeRyR -= sq;
            }
        }
    }

    if (eyeRyL < 8) eyeRyL = 8;
    if (eyeRyR < 8) eyeRyR = 8;

    // Differential redraw: update only pixels that changed between frames.
    if (!idleAnim.hasPrevFrame) {
        gfx->fillScreen(BLACK);
        drawEyes(idleAnim.lookX, idleAnim.lookY, eyeRyL, eyeRyR, CYAN);
    } else {
        drawEyesDelta(
            idleAnim.prevEyeXOffset,
            idleAnim.prevEyeYOffset,
            idleAnim.prevEyeRyL,
            idleAnim.prevEyeRyR,
            idleAnim.lookX,
            idleAnim.lookY,
            eyeRyL,
            eyeRyR,
            CYAN
        );
    }

    idleAnim.prevEyeXOffset = idleAnim.lookX;
    idleAnim.prevEyeYOffset = idleAnim.lookY;
    idleAnim.prevEyeRyL = eyeRyL;
    idleAnim.prevEyeRyR = eyeRyR;
    idleAnim.hasPrevFrame = true;
}

void showRecordingEyesSquint(int16_t ry) {
    gfx->fillScreen(BLACK);

    // Same geometry as idle eyes, but yellow and squinting (ry shrinks).
    const int16_t yc = 112;
    const int16_t rx = 46;
    const int16_t cxL = 68;
    const int16_t cxR = 172;

    fillOval(cxL, yc, rx, ry, YELLOW);
    fillOval(cxR, yc, rx, ry, YELLOW);
}

void resetTimeScreenCache() {
    lastRenderedDay = "";
    lastRenderedDate = "";
    lastRenderedTime = "";
}

void drawCenteredTextLine(const String& text, int16_t y, uint8_t textSize, uint16_t color) {
    const int16_t charW = 6 * textSize;
    const int16_t lineH = 8 * textSize;
    int16_t x = 120 - ((int16_t)text.length() * charW) / 2;
    if (x < 8) x = 8;
    gfx->fillRect(0, y, 240, lineH, BLACK);
    gfx->setTextSize(textSize);
    gfx->setTextColor(color);
    gfx->setCursor(x, y);
    gfx->print(text);
}

void drawDeltaTimeLine(const String& previous, const String& current, int16_t y, uint8_t textSize, uint16_t color) {
    if (previous.length() != current.length()) {
        drawCenteredTextLine(current, y, textSize, color);
        return;
    }

    const int16_t charW = 6 * textSize;
    const int16_t charH = 8 * textSize;
    int16_t xStart = 120 - ((int16_t)current.length() * charW) / 2;
    if (xStart < 8) xStart = 8;

    gfx->setTextSize(textSize);
    gfx->setTextColor(color);

    for (uint16_t i = 0; i < current.length(); i++) {
        if (current.charAt(i) == previous.charAt(i)) continue;
        int16_t x = xStart + ((int16_t)i * charW);
        gfx->fillRect(x, y, charW, charH, BLACK);
        gfx->setCursor(x, y);
        gfx->print(current.charAt(i));
    }
}

void updateRecordingEyesAnimation() {
    const uint32_t now = millis();
    if (now - lastRecordingEyesUpdate < 80) return;
    lastRecordingEyesUpdate = now;

    // Subtle movement so recording face doesn't feel static.
    uint16_t step = (uint16_t)((now / 220) % 8);
    int16_t eyeXOffset = 0;
    if (step < 2) eyeXOffset = -3;
    else if (step < 4) eyeXOffset = -1;
    else if (step < 6) eyeXOffset = 1;
    else eyeXOffset = 3;

    int16_t eyeYOffset = 0;
    int16_t ryPulse = (step % 2 == 0) ? 0 : 2;
    int16_t eyeRyL = 52 - ryPulse; // slight squint + tiny pulse
    int16_t eyeRyR = 52 - ryPulse;

    if (!recordingAnim.hasPrevFrame) {
        gfx->fillScreen(BLACK);
        drawEyes(eyeXOffset, eyeYOffset, eyeRyL, eyeRyR, RED);
    } else {
        drawEyesDelta(
            recordingAnim.prevEyeXOffset,
            recordingAnim.prevEyeYOffset,
            recordingAnim.prevEyeRyL,
            recordingAnim.prevEyeRyR,
            eyeXOffset,
            eyeYOffset,
            eyeRyL,
            eyeRyR,
            RED
        );
    }

    // Recording badge in the same area as the thinking question mark.
    const int16_t recX = 164;
    const int16_t recY = 24;
    gfx->fillRect(recX - 18, recY - 1, 18 + (6 * 3) + 2, (8 * 2) + 2, BLACK);
    fillOval(recX - 10, recY + 7, 4, 4, RED);
    gfx->setTextSize(2);
    gfx->setTextColor(RED);
    gfx->setCursor(recX, recY);
    gfx->print("REC");

    recordingAnim.prevEyeXOffset = eyeXOffset;
    recordingAnim.prevEyeYOffset = eyeYOffset;
    recordingAnim.prevEyeRyL = eyeRyL;
    recordingAnim.prevEyeRyR = eyeRyR;
    recordingAnim.hasPrevFrame = true;
}

void updateUploadingThinkingAnimation() {
    const uint32_t now = millis();
    if (now - lastUploadingEyesUpdate < 80) return;
    lastUploadingEyesUpdate = now;

    // Simple looping "thinking" pose:
    // - squinty cyan eyes
    // - gaze shifts up and to one side
    // - pulsing question mark near top-right
    uint16_t step = (uint16_t)((now / 220) % 8);

    int16_t eyeXOffset = 0;
    int16_t eyeYOffset = 0; // centered like idle
    int16_t eyeRyL = 26;
    int16_t eyeRyR = 24;

    if (step < 2) {
        eyeXOffset = -8; // look up-left
    } else if (step < 4) {
        eyeXOffset = -4;
    } else if (step < 6) {
        eyeXOffset = 4;
    } else {
        eyeXOffset = 8; // look up-right
    }

    if (!uploadAnim.hasPrevFrame) {
        gfx->fillScreen(BLACK);
        drawEyes(eyeXOffset, eyeYOffset, eyeRyL, eyeRyR, CYAN);
    } else {
        drawEyesDelta(
            uploadAnim.prevEyeXOffset,
            uploadAnim.prevEyeYOffset,
            uploadAnim.prevEyeRyL,
            uploadAnim.prevEyeRyR,
            eyeXOffset,
            eyeYOffset,
            eyeRyL,
            eyeRyR,
            CYAN
        );
    }

    // Redraw only the small brow region (old + new bounds).
    int16_t prevBrowOffset = uploadAnim.prevBrowOffset;
    int16_t newBrowOffset = eyeXOffset / 2;
    int16_t browMinX = min((int16_t)(28 + prevBrowOffset), (int16_t)(28 + newBrowOffset));
    int16_t browMaxX = max((int16_t)(212 + prevBrowOffset), (int16_t)(212 + newBrowOffset));
    gfx->fillRect(browMinX, 58, browMaxX - browMinX + 1, 14, BLACK);
    gfx->drawLine(30 + newBrowOffset, 68, 88 + newBrowOffset, 60, CYAN);
    gfx->drawLine(152 + newBrowOffset, 60, 210 + newBrowOffset, 68, CYAN);

    // Pulsing question mark at top-right.
    uint8_t qSize = (step % 2 == 0) ? 3 : 2;
    int16_t qX = 164;
    int16_t qY = 24;
    if (uploadAnim.prevQSize > 0) {
        int16_t prevQw = 6 * uploadAnim.prevQSize;
        int16_t prevQh = 8 * uploadAnim.prevQSize;
        gfx->fillRect(qX - 1, qY - 1, prevQw + 2, prevQh + 2, BLACK);
    }
    gfx->setTextSize(qSize);
    gfx->setTextColor(YELLOW);
    gfx->setCursor(qX, qY);
    gfx->print("?");

    uploadAnim.prevEyeXOffset = eyeXOffset;
    uploadAnim.prevEyeYOffset = eyeYOffset;
    uploadAnim.prevEyeRyL = eyeRyL;
    uploadAnim.prevEyeRyR = eyeRyR;
    uploadAnim.prevBrowOffset = newBrowOffset;
    uploadAnim.prevQSize = qSize;
    uploadAnim.hasPrevFrame = true;
}

void updateFaceEmotionAnimation() {
    const uint32_t now = millis();
    if (now - lastIdleEyesUpdate < 45) return;
    lastIdleEyesUpdate = now;

    uint32_t elapsed = now - faceAnimStartMs;
    int16_t eyeXOffset = idleAnim.lookX;
    int16_t eyeYOffset = idleAnim.lookY;
    if (faceAnimMode == 1) {
        eyeXOffset = happyFrozenLookX;
        eyeYOffset = happyFrozenLookY;
    } else if (faceAnimMode == 2) {
        eyeXOffset = madFrozenLookX;
        eyeYOffset = madFrozenLookY;
    }
    int16_t eyeRyL = 66;
    int16_t eyeRyR = 66;
    // Same pulse cadence for speaking, happy, and mad modes.
    int16_t cadence = (int16_t)triPulse(elapsed % 340, 340, 24);
    eyeRyL -= cadence;
    eyeRyR -= cadence;

    if (eyeRyL < 10) eyeRyL = 10;
    if (eyeRyR < 10) eyeRyR = 10;
    if (eyeRyL > 78) eyeRyL = 78;
    if (eyeRyR > 78) eyeRyR = 78;

    if (faceAnimMode == 1) {
        // Happy: geometry-only cyan (ellipse minus carve); no black overlay over text.
        if (!idleAnim.hasPrevFrame) {
            gfx->fillScreen(BLACK);
            drawHappyEyesFull(eyeXOffset, eyeYOffset, eyeRyL, eyeRyR);
        } else {
            drawHappyEyesDelta(
                eyeXOffset,
                eyeYOffset,
                idleAnim.prevEyeRyL,
                idleAnim.prevEyeRyR,
                eyeRyL,
                eyeRyR
            );
        }
        drawFaceAnimWordsLine();
        idleAnim.prevEyeXOffset = eyeXOffset;
        idleAnim.prevEyeYOffset = eyeYOffset;
        idleAnim.prevEyeRyL = eyeRyL;
        idleAnim.prevEyeRyR = eyeRyR;
        idleAnim.hasPrevFrame = true;
        return;
    }

    if (faceAnimMode == 2) {
        // Mad: flat top-half cut on eyes; same flow as happy.
        if (!idleAnim.hasPrevFrame) {
            gfx->fillScreen(BLACK);
            drawMadEyesFull(eyeXOffset, eyeYOffset, eyeRyL, eyeRyR);
        } else {
            drawMadEyesDelta(
                eyeXOffset,
                eyeYOffset,
                idleAnim.prevEyeRyL,
                idleAnim.prevEyeRyR,
                eyeRyL,
                eyeRyR
            );
        }
        drawFaceAnimWordsLine();
        idleAnim.prevEyeXOffset = eyeXOffset;
        idleAnim.prevEyeYOffset = eyeYOffset;
        idleAnim.prevEyeRyL = eyeRyL;
        idleAnim.prevEyeRyR = eyeRyR;
        idleAnim.hasPrevFrame = true;
        return;
    }

    if (!idleAnim.hasPrevFrame) {
        gfx->fillScreen(BLACK);
        drawEyes(eyeXOffset, eyeYOffset, eyeRyL, eyeRyR, CYAN);
    } else {
        drawEyesDelta(
            idleAnim.prevEyeXOffset,
            idleAnim.prevEyeYOffset,
            idleAnim.prevEyeRyL,
            idleAnim.prevEyeRyR,
            eyeXOffset,
            eyeYOffset,
            eyeRyL,
            eyeRyR,
            CYAN
        );
    }

    drawFaceAnimWordsLine();

    idleAnim.prevEyeXOffset = eyeXOffset;
    idleAnim.prevEyeYOffset = eyeYOffset;
    idleAnim.prevEyeRyL = eyeRyL;
    idleAnim.prevEyeRyR = eyeRyR;
    idleAnim.hasPrevFrame = true;
}

void updateTimeScreen() {
    DateTime now = rtc.now();
    static const char* weekdayNames[] = {
        "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
    };
    static const char* monthNames[] = {
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    };

    char timeBuf[12];   // "HH:MM:SS AM" + null
    char dateBuf[16];   // "Mon DD, YYYY" + null
    const char* dayName = weekdayNames[now.dayOfTheWeek()];
    const char* monthName = monthNames[now.month() - 1];
    int hour24 = now.hour();
    int hour12 = hour24 % 12;
    if (hour12 == 0) hour12 = 12;
    const char* meridiem = (hour24 >= 12) ? "PM" : "AM";

    snprintf(timeBuf, sizeof(timeBuf), "%02d:%02d:%02d %s", hour12, now.minute(), now.second(), meridiem);
    snprintf(dateBuf, sizeof(dateBuf), "%s %02d, %04d", monthName, now.day(), now.year());
    String dayText = String(dayName);
    String dateText = String(dateBuf);
    String timeText = String(timeBuf);

    if (lastRenderedDay != dayText) {
        drawCenteredTextLine(dayText, 58, 2, WHITE);
        lastRenderedDay = dayText;
    }

    if (lastRenderedDate != dateText) {
        drawCenteredTextLine(dateText, 92, 2, WHITE);
        lastRenderedDate = dateText;
    }

    if (lastRenderedTime.length() == 0) {
        drawCenteredTextLine(timeText, 132, 3, WHITE);
    } else {
        drawDeltaTimeLine(lastRenderedTime, timeText, 132, 3, WHITE);
    }
    lastRenderedTime = timeText;
}

bool syncRtcFromWifiNtp() {
    if (WiFi.status() != WL_CONNECTED) {
        return false;
    }

    configTzTime(
        activeTimezoneRule[0] ? activeTimezoneRule : DEFAULT_TIMEZONE_RULE,
        "pool.ntp.org",
        "time.nist.gov",
        "time.google.com"
    );

    struct tm timeinfo;
    if (!getLocalTime(&timeinfo, 10000)) {
        Serial.println("[RTC] NTP sync failed (timeout waiting for time).");
        return false;
    }

    // Write local wall clock into RTC (already localized by timezone rule).
    rtc.adjust(
        DateTime(
            timeinfo.tm_year + 1900,
            timeinfo.tm_mon + 1,
            timeinfo.tm_mday,
            timeinfo.tm_hour,
            timeinfo.tm_min,
            timeinfo.tm_sec
        )
    );
    DateTime now = rtc.now();
    Serial.printf(
        "[RTC] Synced from WiFi NTP (%s): %04d/%02d/%02d %02d:%02d:%02d\n",
        activeTimezoneRule[0] ? activeTimezoneRule : DEFAULT_TIMEZONE_RULE,
        now.year(), now.month(), now.day(), now.hour(), now.minute(), now.second()
    );
    return true;
}

// ==========================================
//          HARDWARE SETUP
// ==========================================
void setupMicrophone() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM),
        .sample_rate = SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 16, 
        .dma_buf_len = 128,  
        .use_apll = false
    };
    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_PIN_NO_CHANGE,
        .ws_io_num = I2S_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = I2S_SD
    };
    i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_NUM_0, &pin_config);
}

static void loadRuntimeVisionFromPrefs() {
    preferences.begin("pixel_prefs", true);
    deviceVisionCaptureEnabled = preferences.getBool("vision_cap", true);
    preferences.end();
}

static void setDeviceVisionCaptureEnabled(bool en) {
    deviceVisionCaptureEnabled = en;
    preferences.begin("pixel_prefs", false);
    preferences.putBool("vision_cap", en);
    preferences.end();
}

static void loadTimezoneRuleFromPrefs() {
    preferences.begin("pixel_prefs", true);
    String tz = preferences.getString("tz_rule", DEFAULT_TIMEZONE_RULE);
    preferences.end();
    tz.trim();
    if (tz.length() == 0) {
        tz = DEFAULT_TIMEZONE_RULE;
    }
    strncpy(activeTimezoneRule, tz.c_str(), sizeof(activeTimezoneRule) - 1);
    activeTimezoneRule[sizeof(activeTimezoneRule) - 1] = '\0';
}

static void setTimezoneRule(const char* tzRule) {
    String tz = String(tzRule ? tzRule : "");
    tz.trim();
    if (tz.length() == 0) {
        tz = DEFAULT_TIMEZONE_RULE;
    }
    strncpy(activeTimezoneRule, tz.c_str(), sizeof(activeTimezoneRule) - 1);
    activeTimezoneRule[sizeof(activeTimezoneRule) - 1] = '\0';

    preferences.begin("pixel_prefs", false);
    preferences.putString("tz_rule", activeTimezoneRule);
    preferences.end();
}



// ==========================================
//          SETTINGS SCREEN UI
// ==========================================
static const uint16_t kSetBg       = 0x10A2; // dark gray
static const uint16_t kSetRing     = 0x2124;
static const uint16_t kSetTitle    = 0xEF9D; // warm white
static const uint16_t kSetLabel    = 0xB5B6; // light gray
static const uint16_t kSetOnColor  = 0x07E0; // green
static const uint16_t kSetOffColor = 0xF800; // red

static const int16_t kVidBtnX = 52;
static const int16_t kVidBtnY = 102;
static const int16_t kVidBtnW = 136;
static const int16_t kVidBtnH = 36;

void drawSettingsScreen() {
    gfx->fillScreen(kSetBg);
    gfx->drawCircle(120, 120, 118, kSetRing);

    // Title
    gfx->setTextSize(2);
    gfx->setTextColor(kSetTitle);
    const char* title = "SETTINGS";
    int16_t tw = (int16_t)(strlen(title) * 12);
    gfx->setCursor((240 - tw) / 2, 50);
    gfx->print(title);

    // Video toggle button
    uint16_t btnColor = deviceVisionCaptureEnabled ? kSetOnColor : kSetOffColor;
    gfx->fillRoundRect(kVidBtnX, kVidBtnY, kVidBtnW, kVidBtnH, 10, btnColor);
    gfx->setTextSize(2);
    gfx->setTextColor(WHITE);
    const char* vidText = deviceVisionCaptureEnabled ? "VIDEO: ON" : "VIDEO: OFF";
    int16_t vtw = (int16_t)(strlen(vidText) * 12);
    gfx->setCursor((240 - vtw) / 2, kVidBtnY + 10);
    gfx->print(vidText);

    // Video label
    gfx->setTextSize(1);
    gfx->setTextColor(kSetLabel);
    const char* camLabel = "CAMERA CAPTURE";
    int16_t clw = (int16_t)(strlen(camLabel) * 6);
    gfx->setCursor((240 - clw) / 2, kVidBtnY + kVidBtnH + 6);
    gfx->print(camLabel);
}

void captureVideoFrame() {
    if (!isWsConnected) return;
    if (!deviceVisionCaptureEnabled) return;
    if (millis() - lastFrameTime < FRAME_INTERVAL_MS) return;
    lastFrameTime = millis();

    if (video_frame_count >= MAX_FRAMES) return;

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) return;

    uint8_t* frame_buf = (uint8_t*)ps_malloc(fb->len);
    if (frame_buf) {
        memcpy(frame_buf, fb->buf, fb->len);
        video_frames[video_frame_count] = frame_buf;
        video_frame_sizes[video_frame_count] = fb->len;
        video_frame_count++;
    } 
    
    esp_camera_fb_return(fb);
}

void processAudio() {
    size_t bytesRead = 0;
    if (recordedAudioLen + 512 > FLASH_RECORD_SIZE) return;
    
    i2s_read(I2S_NUM_0, rec_buffer + 44 + recordedAudioLen, 512, &bytesRead, portMAX_DELAY);
    if (bytesRead > 0) {
        recordedAudioLen += bytesRead;
    }
}

// ==========================================
//      BLE PROVISIONING CALLBACKS
// ==========================================
class ProvisionCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String rxValue = pCharacteristic->getValue().c_str();

      if (rxValue.length() > 0) {
        StaticJsonDocument<256> doc;
        DeserializationError error = deserializeJson(doc, rxValue);
        
        if (!error) {
            String new_ssid = doc["ssid"].as<String>();
            String new_pass = doc["password"].as<String>();
            
            if(new_ssid.length() > 0) {
                preferences.begin("wifi_creds", false); 
                preferences.putString("ssid", new_ssid);
                preferences.putString("password", new_pass);
                preferences.end();
                
                needsRestart = true;
            }
        }
      }
    }
};

void startBLEProvisioning() {
    currentState = STATE_SETUP;
    updateScreen("BLE SETUP", BLUE);
    
    BLEDevice::init(BLE_SERVICE_NAME);
    BLEServer *pServer = BLEDevice::createServer();
    BLEService *pService = pServer->createService(SERVICE_UUID);
    
    BLECharacteristic *pCharacteristic = pService->createCharacteristic(
                                         WIFI_CREDS_CHAR_UUID,
                                         BLECharacteristic::PROPERTY_WRITE
                                       );
                                       
    pCharacteristic->setCallbacks(new ProvisionCallbacks());
    pService->start();
    
    BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
    pAdvertising->addServiceUUID(SERVICE_UUID);
    pAdvertising->setScanResponse(true);
    pAdvertising->setMinPreferred(0x06);  
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
}

void setupWiFi() {
    preferences.begin("wifi_creds", true); 
    String saved_ssid = preferences.getString("ssid", "");
    String saved_pass = preferences.getString("password", "");
    preferences.end();
    
    if (saved_ssid == "") {
        startBLEProvisioning();
        return;
    }
    
    updateScreen("CONNECTING", YELLOW);
    esp_wifi_set_max_tx_power(78); 
    WiFi.mode(WIFI_STA);

    bool connected = false;
    for (uint8_t attempt = 0; attempt < WIFI_CONNECT_ATTEMPTS; attempt++) {
        WiFi.disconnect(true, false);
        delay(80);
        WiFi.begin(saved_ssid.c_str(), saved_pass.c_str());

        uint32_t attemptStart = millis();
        while (WiFi.status() != WL_CONNECTED && (millis() - attemptStart) < WIFI_CONNECT_TIMEOUT_MS) {
            delay(250);
        }

        if (WiFi.status() == WL_CONNECTED) {
            connected = true;
            break;
        }
    }

    if (!connected) {
        preferences.begin("wifi_meta", false);
        uint8_t failCount = preferences.getUChar("fail_count", 0);
        if (failCount < 255) failCount++;
        preferences.putUChar("fail_count", failCount);
        preferences.end();

        if (failCount >= WIFI_FAILS_BEFORE_CREDS_CLEAR) {
            // Last-resort recovery after many failed boots.
            preferences.begin("wifi_creds", false);
            preferences.remove("ssid");
            preferences.remove("password");
            preferences.end();

            preferences.begin("wifi_meta", false);
            preferences.putUChar("fail_count", 0);
            preferences.end();
        }

        if (failCount >= WIFI_FAILS_BEFORE_BLE_FALLBACK) {
            startBLEProvisioning();
        } else {
            updateScreen("WIFI RETRY", YELLOW);
            delay(800);
            ESP.restart();
        }
        return;
    }
    
    preferences.begin("wifi_meta", false);
    preferences.putUChar("fail_count", 0);
    preferences.end();

    WiFi.setSleep(false);
    syncRtcFromWifiNtp();
    lastRtcWifiSyncAttemptMs = millis();
    currentState = STATE_IDLE; 
    showIdleEyes();

    loadRuntimeVisionFromPrefs();

    webSocket.begin(backend_ip, backend_port, "/ws/stream");
    webSocket.onEvent([](WStype_t type, uint8_t * payload, size_t length) {
        switch(type) {
            case WStype_DISCONNECTED:
                isWsConnected = false;
                break;
            case WStype_CONNECTED:
                isWsConnected = true;
                break;
            case WStype_TEXT:
                if (payload && length > 0) {
                    StaticJsonDocument<384> doc;
                    DeserializationError err = deserializeJson(doc, payload, length);
                    if (!err) {
                        const char* msgType = doc["type"] | "";
                        const char* status = doc["status"] | "";

                        if (strcmp(msgType, "gemini_first_token") == 0) {
                            geminiFirstTokenReceived = true;
                        } else if (strcmp(msgType, "runtime_vision") == 0) {
                            bool en = doc["enabled"] | true;
                            setDeviceVisionCaptureEnabled(en);
                            if (!en && currentState == STATE_RECORDING) {
                                for (int i = 0; i < video_frame_count; i++) {
                                    if (video_frames[i]) {
                                        free(video_frames[i]);
                                        video_frames[i] = NULL;
                                    }
                                }
                                video_frame_count = 0;
                            }
                        } else if (strcmp(msgType, "runtime_timezone") == 0) {
                            const char* tzRule = doc["timezone_rule"] | DEFAULT_TIMEZONE_RULE;
                            setTimezoneRule(tzRule);
                            if (WiFi.status() == WL_CONNECTED) {
                                syncRtcFromWifiNtp();
                                lastRtcWifiSyncAttemptMs = millis();
                            }
                        } else if (strcmp(msgType, "face_animation") == 0) {
                            const char* anim = doc["animation"] | "speaking";
                            const char* w = doc["words"] | "";
                            int dur = doc["duration_ms"] | FACE_ANIM_DURATION_MS;
                            if (dur < 800) dur = 800;
                            if (dur > 10000) dur = 10000;

                            if (strcmp(anim, "weather") == 0) {
                                uint8_t kind = WEATHER_KIND_CLOUDY;
                                int tempF = 72;
                                if (parseWeatherWords(w, &kind, &tempF)) {
                                    weatherKind = kind;
                                    weatherTempDisplay = tempF;
                                } else {
                                    weatherKind = WEATHER_KIND_CLOUDY;
                                    weatherTempDisplay = 72;
                                }
                                weatherOverlayActive = true;
                                weatherNeedsRedraw = true;
                                weatherOverlayUntilMs = millis() + (uint32_t)dur;
                                faceAnimActive = false;
                                faceAnimWords[0] = '\0';
                                if (currentState == STATE_UPLOADING) {
                                    suppressUploadThinkingAfterWeather = true;
                                }
                            } else if (strcmp(anim, "map") == 0) {
                                faceAnimActive = false;
                                faceAnimWords[0] = '\0';
                                clearFaceAnimCaptionBox();
                                mapHasImage = false;
                                mapNeedsRedraw = false;
                                mapOverlayActive = true;
                                mapOverlayUntilMs = millis() + (uint32_t)dur;
                                gfx->fillScreen(0x1082);
                                gfx->setTextColor(0xDEFB);
                                gfx->setTextSize(3);
                                gfx->setCursor(80, 110);
                                gfx->print("Map");
                                if (currentState == STATE_UPLOADING) {
                                    suppressUploadThinkingAfterMap = true;
                                }
                            } else {
                                setFaceAnimWordsFromPayload(w);
                                gfx->fillScreen(BLACK);
                                // Force caption redraw; screen was cleared even if words match last animation.
                                s_faceCaptionLastText[0] = '\0';
                                s_faceCaptionVisible = false;
                                if (strcmp(anim, "happy") == 0) {
                                    faceAnimMode = 1;
                                } else if (strcmp(anim, "mad") == 0) {
                                    faceAnimMode = 2;
                                } else {
                                    faceAnimMode = 0;
                                }
                                if (faceAnimMode == 1) {
                                    happyFrozenLookX = idleAnim.lookX;
                                    happyFrozenLookY = idleAnim.lookY;
                                } else if (faceAnimMode == 2) {
                                    madFrozenLookX = idleAnim.lookX;
                                    madFrozenLookY = idleAnim.lookY;
                                }
                                faceAnimActive = true;
                                faceAnimStartMs = millis();
                                faceAnimUntilMs = millis() + (uint32_t)FACE_ANIM_DURATION_MS;
                                idleAnim.hasPrevFrame = false;
                                if (!timeScreenActive && !weatherOverlayActive && !idleAnim.hasPrevFrame) {
                                    idleAnim.hasPrevFrame = false;
                                }
                            }
                        } else if (strcmp(msgType, "show_weather") == 0) {
                            float tRaw = doc["temperature"] | 0.0f;
                            weatherTempDisplay = (int)(tRaw >= 0.0f ? (tRaw + 0.5f) : (tRaw - 0.5f));
                            int dur = doc["duration_ms"] | 5000;
                            const char* cond = doc["condition"] | "cloudy";
                            if (strcmp(cond, "sunny") == 0) {
                                weatherKind = WEATHER_KIND_SUNNY;
                            } else if (strcmp(cond, "cloudy") == 0) {
                                weatherKind = WEATHER_KIND_CLOUDY;
                            } else if (strcmp(cond, "partially_cloudy") == 0) {
                                weatherKind = WEATHER_KIND_PARTIALLY_CLOUDY;
                            } else if (strcmp(cond, "raining") == 0) {
                                weatherKind = WEATHER_KIND_RAINING;
                            } else if (strcmp(cond, "snowing") == 0) {
                                weatherKind = WEATHER_KIND_SNOWING;
                            } else {
                                weatherKind = WEATHER_KIND_CLOUDY;
                            }
                            weatherOverlayActive = true;
                            weatherNeedsRedraw = true;
                            weatherOverlayUntilMs = millis() + (uint32_t)dur;
                            if (currentState == STATE_UPLOADING) {
                                suppressUploadThinkingAfterWeather = true;
                            }
                        } else if (strcmp(status, "error") == 0) {
                            // Don't leave user stuck in thinking animation on backend errors.
                            geminiFirstTokenReceived = true;
                        }
                    }
                }
                break;
            case WStype_BIN:
                if (payload && length > 1 && payload[0] == 0x04) {
                    const uint8_t *jpg = payload + 1;
                    size_t jpgLen = length - 1;
                    if (s_mapRgb565 == nullptr) {
                        s_mapRgb565 = (uint16_t *)heap_caps_malloc(
                            MAP_DISPLAY_PX * MAP_DISPLAY_PX * sizeof(uint16_t),
                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
                    }
                    if (s_mapRgb565 != nullptr && jpgLen > 0) {
                        if (s_jpegDec.openRAM((uint8_t *)jpg, (int32_t)jpgLen, mapJpegDrawCallback)) {
                            s_jpegDec.setPixelType(RGB565_BIG_ENDIAN);
                            s_jpegDec.decode(0, 0, 0);
                            s_jpegDec.close();
                            mapHasImage = true;
                            mapNeedsRedraw = true;
                            mapOverlayActive = true;
                            mapOverlayUntilMs = millis() + MAP_OVERLAY_EXTEND_MS;
                        }
                    }
                }
                break;
            case WStype_ERROR:          
            case WStype_FRAGMENT_TEXT_START:
            case WStype_FRAGMENT_BIN_START:
            case WStype_FRAGMENT:
            case WStype_FRAGMENT_FIN:
                break;
        }
    });

    webSocket.setReconnectInterval(5000); 
}

void triggerGeminiProcessing() {
    if (isWsConnected) {
        uint8_t header = 0x03; 
        webSocket.sendBIN(&header, 1);
    }
}

// ==========================================
//     FREERTOS UPLOAD TASK (CORE 0)
// ==========================================
void uploadDataTask(void * pvParameters) {
    for(;;) {
        if (triggerUpload) {
            
            if (!isWsConnected) {
                for (int i=0; i<video_frame_count; i++) {
                    if (video_frames[i]) {
                        free(video_frames[i]);
                        video_frames[i] = NULL;
                    }
                }
                recordedAudioLen = 0;
                video_frame_count = 0;
            } else {
                
                size_t offset = 0;
                while(offset < recordedAudioLen) {
                    size_t chunk = 4096; 
                    if (offset + chunk > recordedAudioLen) chunk = recordedAudioLen - offset;
                    
                    uint8_t* packet = (uint8_t*)malloc(chunk + 1);
                    if (packet) {
                        packet[0] = 0x01;
                        memcpy(packet + 1, rec_buffer + 44 + offset, chunk);
                        webSocket.sendBIN(packet, chunk + 1);
                        free(packet);
                    }
                    offset += chunk;
                    webSocket.loop(); 
                    vTaskDelay(pdMS_TO_TICKS(1)); 
                }

                for (int i=0; i<video_frame_count; i++) {
                    size_t fsize = video_frame_sizes[i];
                    uint8_t* packet = (uint8_t*)malloc(fsize + 1);
                    if (packet) {
                        packet[0] = 0x02;
                        memcpy(packet + 1, video_frames[i], fsize);
                        webSocket.sendBIN(packet, fsize + 1);
                        free(packet);
                    }
                    
                    if (video_frames[i]) {
                        free(video_frames[i]);
                        video_frames[i] = NULL;
                    }
                    
                    webSocket.loop();
                    vTaskDelay(pdMS_TO_TICKS(1));
                }

                recordedAudioLen = 0;
                video_frame_count = 0;
                triggerGeminiProcessing();
            }

            triggerUpload = false;
            if (!isWsConnected) {
                currentState = STATE_IDLE;
                suppressUploadThinkingAfterWeather = false;
                showIdleEyes();
            }
        }
        
        vTaskDelay(pdMS_TO_TICKS(10)); 
    }
}

// ==========================================
//                ARDUINO SETUP
// ==========================================
void setup() {
    Serial.begin(115200);
    randomSeed((uint32_t)esp_random());
    
    pinMode(TFT_BL, OUTPUT); digitalWrite(TFT_BL, HIGH);
    gfx->begin(80000000); 
    updateScreen("BOOTING...", WHITE);
    
    // Initialize native I2C for the CHSC6X Touch Controller & RTC
    Wire.begin(TOUCH_SDA, TOUCH_SCL);
    pinMode(TOUCH_INT, INPUT_PULLUP);

    // Initialize RTC
    if (!rtc.begin()) {
        Serial.println("[ERROR] Couldn't find RTC!");
    } else {
        if (rtc.lostPower()) {
            Serial.println("[RTC] Power lost or first boot, setting time to compile time!");
            rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
        }
        Serial.println("[RTC] Initialized successfully.");
    }

    rec_buffer = (uint8_t *)ps_malloc(FLASH_RECORD_SIZE + 44);
    if (rec_buffer == NULL) {
        updateScreen("MEM ERROR", RED);
        while(1);
    }

    setupMicrophone();
    
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0; config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = 15; config.pin_d1 = 17; config.pin_d2 = 18; config.pin_d3 = 16;
    config.pin_d4 = 14; config.pin_d5 = 12; config.pin_d6 = 11; config.pin_d7 = 48;
    config.pin_xclk = 10; config.pin_pclk = 13; config.pin_vsync = 38; config.pin_href = 47;
    config.pin_sccb_sda = 40; config.pin_sccb_scl = 39; config.pin_pwdn = -1; config.pin_reset = -1;
    config.xclk_freq_hz = 20000000; config.frame_size = FRAMESIZE_VGA; 
    config.pixel_format = PIXFORMAT_JPEG; config.grab_mode = CAMERA_GRAB_LATEST;
    config.fb_location = CAMERA_FB_IN_PSRAM; 
    config.jpeg_quality = 20; 
    config.fb_count = 2;
    esp_camera_init(&config);

    loadTimezoneRuleFromPrefs();
    setupWiFi();

    xTaskCreatePinnedToCore(
        uploadDataTask, "Upload Task", 10000, NULL, 1, NULL, 0
    );
}

// ==========================================
//                ARDUINO LOOP
// ==========================================
void loop() {
    static RobotState previousVisualState = STATE_SETUP;

    // Force a full refresh on transitions to/from the thinking animation.
    if (currentState != previousVisualState) {
        if ((currentState == STATE_UPLOADING || previousVisualState == STATE_UPLOADING) && !weatherOverlayActive && !mapOverlayActive) {
            gfx->fillScreen(BLACK);
            idleAnim.hasPrevFrame = false;
            uploadAnim.hasPrevFrame = false;
            uploadAnim.prevQSize = 0;
            lastUploadingEyesUpdate = 0;
            lastIdleEyesUpdate = 0;
            lastRecordingEyesUpdate = 0;
        }
        previousVisualState = currentState;
    }

    if (weatherOverlayActive && millis() >= weatherOverlayUntilMs) {
        weatherOverlayActive = false;
        idleAnim.hasPrevFrame = false;
        if (!timeScreenActive && !faceAnimActive && !mapOverlayActive) {
            showIdleEyes();
            lastIdleEyesUpdate = 0;
        } else {
            gfx->fillScreen(BLACK);
        }
    }

    if (mapOverlayActive && millis() >= mapOverlayUntilMs) {
        mapOverlayActive = false;
        mapHasImage = false;
        mapNeedsRedraw = false;
        suppressUploadThinkingAfterMap = false;
        idleAnim.hasPrevFrame = false;
        if (!timeScreenActive && !faceAnimActive && !weatherOverlayActive) {
            showIdleEyes();
            lastIdleEyesUpdate = 0;
        } else {
            gfx->fillScreen(BLACK);
        }
    }

    if (faceAnimActive && millis() >= faceAnimUntilMs) {
        faceAnimActive = false;
        faceAnimMode = 0;
        faceAnimWords[0] = '\0';
        clearFaceAnimCaptionBox();
        gfx->fillScreen(BLACK);
        idleAnim.hasPrevFrame = false;
        uploadAnim.hasPrevFrame = false;
        recordingAnim.hasPrevFrame = false;
        if (!timeScreenActive && !weatherOverlayActive && !mapOverlayActive && currentState == STATE_IDLE) {
            lastIdleEyesUpdate = 0;
        }
    }

    // --- RTC SERIAL TICKER ---
    if (millis() - lastRtcPrintTime > 1000) {
        lastRtcPrintTime = millis();
        DateTime now = rtc.now();
        
        Serial.printf("[RTC] %04d/%02d/%02d %02d:%02d:%02d\n", 
                      now.year(), now.month(), now.day(), 
                      now.hour(), now.minute(), now.second());
    }

    // Hourly RTC re-sync while WiFi is connected.
    if (
        WiFi.status() == WL_CONNECTED &&
        (millis() - lastRtcWifiSyncAttemptMs) >= RTC_WIFI_SYNC_INTERVAL_MS
    ) {
        lastRtcWifiSyncAttemptMs = millis();
        syncRtcFromWifiNtp();
    }

    // --- Time Screen UI Refresh ---
    if (timeScreenActive && currentState == STATE_IDLE) {
        if (millis() - lastTimeScreenUpdate > 1000) {
            lastTimeScreenUpdate = millis();
            updateTimeScreen();
        }
    }

    if (currentState != STATE_SETUP) {
        webSocket.loop();
    }

    // ========================================================
    //      Native CHSC6X Touch & Gesture Implementation
    // ========================================================
    bool currentTouchState = digitalRead(TOUCH_INT);
    bool gestureReady = false;
    
    // 1. Read Data on Interrupt Pulse (Active LOW)
    if (currentTouchState == LOW) {
        Wire.requestFrom(CHSC6X_I2C_ID, 5);
        if (Wire.available() == 5) {
            uint8_t points = Wire.read(); 
            Wire.read(); // Event ID
            int tempX = Wire.read(); 
            Wire.read(); // Reserved ID
            int tempY = Wire.read(); 
            
            if (points > 0) {
                if (!isTouching) {
                    isTouching = true;
                    startX = tempX;
                    startY = tempY;
                    touchStartTime = millis();
                }
                endX = tempX;
                endY = tempY;
                lastActiveTouchTime = millis();
            } 
            else if (points == 0 && isTouching) {
                // The IC explicitly sent a "lifted" event
                gestureReady = true;
            }
        }
    }

    // 2. Timeout Fallback
    // If we haven't received a pulse in 50ms, assume the finger was lifted
    if (isTouching && (millis() - lastActiveTouchTime > 50)) {
        gestureReady = true;
    }

    // 3. Evaluate the fully completed gesture
    if (gestureReady) {
        isTouching = false; // Reset state for the next touch
        
        int deltaX = endX - startX;
        int deltaY = endY - startY;
        unsigned long touchDuration = millis() - touchStartTime;
        
        int absX = abs(deltaX);
        int absY = abs(deltaY);
        
        // Tuning Thresholds
        int minSwipeDist = 30;  // Pixels to travel to trigger a swipe
        int maxTapDist = 20;    // Max drift allowed during a tap
        int maxTapTime = 500;   // Max milliseconds for a tap
        
        // Evaluate TAP
        if (absX <= maxTapDist && absY <= maxTapDist && touchDuration < maxTapTime) {
            
            // Apply debounce cooldown
            if (millis() - lastTapTime > tapCooldown) {
                lastTapTime = millis();
                
                if (currentState == STATE_SETTINGS) {
                    // Handle settings screen taps
                    int tapX = endX;
                    int tapY = endY;

                    // Video toggle button tap
                    if (tapY >= kVidBtnY && tapY <= (kVidBtnY + kVidBtnH)
                             && tapX >= kVidBtnX && tapX <= (kVidBtnX + kVidBtnW)) {
                        bool newVal = !deviceVisionCaptureEnabled;
                        setDeviceVisionCaptureEnabled(newVal);
                        // Notify backend of the change
                        if (isWsConnected) {
                            StaticJsonDocument<128> vDoc;
                            vDoc["type"] = "vision_changed";
                            vDoc["enabled"] = newVal;
                            char buf[128];
                            serializeJson(vDoc, buf, sizeof(buf));
                            webSocket.sendTXT(buf);
                        }
                        settingsScreenNeedsRedraw = true;
                    }
                    // Tap outside controls → exit settings
                    else {
                        currentState = STATE_IDLE;
                        showIdleEyes();
                    }
                }
                else if (currentState == STATE_IDLE) {
                    Serial.println("\n*** TAP DETECTED: STARTING RECORD ***\n");
                    weatherOverlayActive = false;
                    currentState = STATE_RECORDING;
                    timeScreenActive = false;
                    resetTimeScreenCache();
                    lastRecordingEyesUpdate = 0; // force immediate redraw
                    recordingAnim.hasPrevFrame = false;
                    lastFrameTime = 0;
                    recordedAudioLen = 0;
                    video_frame_count = 0;
                    updateRecordingEyesAnimation();
                } 
                else if (currentState == STATE_RECORDING) {
                    Serial.println("\n*** TAP DETECTED: STOPPING RECORD ***\n");
                    currentState = STATE_UPLOADING;
                    suppressUploadThinkingAfterWeather = false;
                    timeScreenActive = false;
                    resetTimeScreenCache();
                    lastUploadingEyesUpdate = 0; // force immediate redraw
                    updateUploadingThinkingAnimation();
                    triggerUpload = true; 
                }
            }
        }
        // Evaluate SWIPE
        else if (absX > minSwipeDist || absY > minSwipeDist) {
            if (absX > absY) {
                if (deltaX > 0) {
                    Serial.println("SWIPE RIGHT");
                    if (timeScreenActive && currentState == STATE_IDLE) {
                        timeScreenActive = false;
                        resetTimeScreenCache();
                        showIdleEyes();
                    }
                } else {
                    Serial.println("SWIPE LEFT");
                    // Swipe left -> show time screen (RTC)
                    if (currentState == STATE_IDLE) {
                        timeScreenActive = true;
                        gfx->fillScreen(BLACK);
                        resetTimeScreenCache();
                        lastTimeScreenUpdate = 0;
                        updateTimeScreen();
                    }
                }
            } else {
                if (deltaY > 0) {
                    Serial.println("SWIPE DOWN");
                    if (currentState == STATE_IDLE) {
                        timeScreenActive = false;
                        resetTimeScreenCache();
                        currentState = STATE_SETTINGS;
                        settingsScreenNeedsRedraw = true;
                    }
                } else {
                    Serial.println("SWIPE UP");
                    if (currentState == STATE_SETTINGS) {
                        currentState = STATE_IDLE;
                        showIdleEyes();
                    } else if (timeScreenActive && currentState == STATE_IDLE) {
                        timeScreenActive = false;
                        resetTimeScreenCache();
                        showIdleEyes();
                    }
                }
            }
        }
    }

    // ========================================================
    //      State Machine
    // ========================================================
    if(currentState == STATE_SETUP) {
        if (needsRestart) {
            updateScreen("SAVED!", GREEN);
            delay(1000);
            ESP.restart();
        }
        delay(100);
        return; 
    }

    if (geminiFirstTokenReceived && currentState == STATE_UPLOADING) {
        geminiFirstTokenReceived = false;
        currentState = STATE_IDLE;
        suppressUploadThinkingAfterWeather = false;
        suppressUploadThinkingAfterMap = false;
        if (weatherOverlayActive) {
            weatherNeedsRedraw = true;
        } else if (mapOverlayActive) {
            lastIdleEyesUpdate = 0;
        } else if (faceAnimActive) {
            // Keep the active face animation fully in control of the frame.
            lastIdleEyesUpdate = 0;
        } else {
            showIdleEyes();
        }
    }

    if (weatherOverlayActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        if (weatherNeedsRedraw) {
            drawWeatherOverlay();
            weatherNeedsRedraw = false;
        } else {
            updateWeatherOverlayAnimation(millis());
        }
    } else if (mapOverlayActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        if (mapHasImage && mapNeedsRedraw && s_mapRgb565 != nullptr) {
            gfx->draw16bitRGBBitmap(0, 0, s_mapRgb565, MAP_DISPLAY_PX, MAP_DISPLAY_PX);
            mapNeedsRedraw = false;
        }
    } else if (faceAnimActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        updateFaceEmotionAnimation();
    } else if (currentState == STATE_RECORDING) {
        updateRecordingEyesAnimation();
        processAudio();
        captureVideoFrame(); 
    } else if (currentState == STATE_UPLOADING) {
        if (!suppressUploadThinkingAfterWeather && !suppressUploadThinkingAfterMap) {
            updateUploadingThinkingAnimation();
        } else if (!timeScreenActive) {
            updateIdleEyesAnimation();
        }
    } else if (currentState == STATE_SETTINGS) {
        if (settingsScreenNeedsRedraw) {
            drawSettingsScreen();
            settingsScreenNeedsRedraw = false;
        }
    } else if (currentState == STATE_IDLE && !timeScreenActive) {
        updateIdleEyesAnimation();
    }
}