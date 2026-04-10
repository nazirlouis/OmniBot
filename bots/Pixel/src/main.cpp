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
#include <string>
#include "freertos/semphr.h"

// ==========================================
//        USER CONFIGURATION
// ==========================================
// Hub PC on your LAN (not 127.0.0.1 — the ESP32 must reach the machine running the hub).
const char* backend_ip = "192.168.1.100";
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
Arduino_GFX *gfx = new Arduino_GC9A01(bus, TFT_RST, 3, true);
Preferences preferences;
RTC_PCF8563 rtc;

#define SAMPLE_RATE 16000
/** PCM bytes per wake stream packet body (hub wake + VAD). */
#define WAKE_STREAM_CHUNK 2048
/** JPEG frame interval during idle wake streaming when vision is on (matches hub ~10fps clip). */
#define WAKE_VIDEO_FRAME_MS 100

WebSocketsClient webSocket;
bool isWsConnected = false;
/** Set true only after hub `webSocket.begin()` (Wi‑Fi connected path). Used to guard `webSocket.loop()`. */
bool hubWebSocketEnabled = false;

/** WebSocketsClient is not thread-safe; serialize sendBIN + loop across wake task and Arduino loop(). */
static SemaphoreHandle_t g_wsSendMutex = nullptr;

static void wsSendMutexInit() {
    if (!g_wsSendMutex) {
        g_wsSendMutex = xSemaphoreCreateMutex();
    }
}

static void wsLock() {
    if (g_wsSendMutex) {
        xSemaphoreTake(g_wsSendMutex, portMAX_DELAY);
    }
}

static void wsUnlock() {
    if (g_wsSendMutex) {
        xSemaphoreGive(g_wsSendMutex);
    }
}

enum RobotState { STATE_IDLE, STATE_SETUP, STATE_UPLOADING, STATE_SETTINGS };
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

unsigned long lastIdleEyesUpdate = 0;
unsigned long lastUploadingEyesUpdate = 0;

bool needsRestart = false;
volatile bool geminiFirstTokenReceived = false;

// When false, skip JPEG capture during record (matches app "Vision" / server use_vision).
bool deviceVisionCaptureEnabled = true;
/** Hub pushes wake streaming on/off (mic upload for openWakeWord + VAD). */
bool deviceWakeWordEnabled = true;
/** When true, hub uses Gemini Live: keep mic PCM streaming during UPLOADING (thinking) state. */
bool deviceLiveVoiceStream = false;

// Hub presence face scan (0x06) + prefs mirrored from hub runtime JSON
bool presenceScanEnabled = false;
uint32_t presenceScanIntervalSec = 5;
uint32_t greetingCooldownMinutes = 30;
uint32_t sleepTimeoutSec = 300;
unsigned long lastPresenceScanMs = 0;
unsigned long lastActivityMs = 0;
volatile bool pendingEnrollmentCapture = false;
bool sleepAnimActive = false;
bool wakeUpAnimActive = false;
uint32_t wakeUpAnimStartMs = 0;
bool sleepAnimHasPrevFrame = false;
int16_t sleepAnimPrevY = 120;
/** "zzz" above right eye during sleep; cleared when eyes bob. */
static bool s_sleepZzzHavePrev = false;
static int16_t s_sleepZzzClearX = 0;
static int16_t s_sleepZzzClearY = 0;
/** Bounding box for comic Z cluster (cleared each frame; includes float margin). */
static const int16_t kSleepZzzBoxW = 78;
static const int16_t kSleepZzzBoxH = 48;
static const uint16_t kSleepZzzFill = 0x05FF;
static const int16_t kSleepWakeEyeCenterLeftX = 68;
static const int16_t kSleepWakeEyeCenterRightX = 172;
static const int16_t kSleepWakeEyeCenterY = 160;

enum WakeScanPhase {
    WAKE_SCAN_PHASE_A = 0,
    WAKE_SCAN_PHASE_B = 1,
    WAKE_SCAN_PHASE_C = 2,
};
WakeScanPhase wakeScanPhase = WAKE_SCAN_PHASE_A;
uint32_t wakeScanPhaseStartMs = 0;
bool wakeScanHasPrevFrame = false;
int16_t wakeScanPrevBarX = 0;
int16_t wakeScanPrevBarW = 0;
int16_t wakeScanPrevEyeW = 0;
int16_t wakeScanPrevSlitY = 0;
int16_t wakeScanPrevSlitH = 0;

// Settings screen
bool settingsScreenNeedsRedraw = true;

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
// Directions overlay (JSON show_directions before binary 0x04 map)
bool mapDirectionsHasMetrics = false;
float mapRouteMiles = 0.0f;
int mapRouteMinutes = 0;
static bool s_mapDirectionsBackdropApplied = false;

// Calling card from hub (JSON show_calling_card + binary 0x05 place photo JPEG)
#define CALLING_CARD_TITLE_MAX 80
#define CALLING_CARD_ADDR_MAX 120
#define CALLING_CARD_CAT_MAX 48
#define CALLING_CARD_DEFAULT_PHOTO_W 240
#define CALLING_CARD_DEFAULT_PHOTO_H 240
bool callingCardOverlayActive = false;
uint32_t callingCardOverlayUntilMs = 0;
bool callingCardNeedsRedraw = true;
bool callingCardHasPhoto = false;
bool suppressUploadThinkingAfterCallingCard = false;
static char callingCardTitle[CALLING_CARD_TITLE_MAX + 1];
static char callingCardAddress[CALLING_CARD_ADDR_MAX + 1];
static char callingCardCategory[CALLING_CARD_CAT_MAX + 1];
static bool callingCardHasRating = false;
static float callingCardRating = 0.0f;
static bool callingCardHasReviewCount = false;
static int32_t callingCardReviewCount = 0;
static int16_t callingCardPhotoW = CALLING_CARD_DEFAULT_PHOTO_W;
static int16_t callingCardPhotoH = CALLING_CARD_DEFAULT_PHOTO_H;
static uint16_t *s_callingCardPhoto565 = nullptr;
static size_t s_callingCardPhotoCapacity = 0;
static JPEGDEC s_jpegDecCallingCard;

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

static int callingCardJpegDrawCallback(JPEGDRAW *pDraw) {
    if (!s_callingCardPhoto565) {
        return 0;
    }
    uint16_t *src = pDraw->pPixels;
    int16_t x = pDraw->x;
    int16_t y = pDraw->y;
    int16_t w = pDraw->iWidth;
    int16_t h = pDraw->iHeight;
    int16_t stride = callingCardPhotoW;
    for (int16_t row = 0; row < h; row++) {
        int16_t yy = y + row;
        if (yy < 0 || yy >= callingCardPhotoH) {
            continue;
        }
        uint16_t *dst = s_callingCardPhoto565 + yy * stride + x;
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
// Optional caption under eyes if hub sends "words" (hub normally omits).
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

const uint8_t WIFI_CONNECT_ATTEMPTS = 2;
/** Per-attempt association window (boot only; keep short). */
const uint16_t WIFI_CONNECT_TIMEOUT_MS = 2000;
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

static const char* wifiStatusLabel(wl_status_t s) {
    switch (s) {
        case WL_IDLE_STATUS: return "IDLE";
        case WL_NO_SSID_AVAIL: return "NO AP";
        case WL_SCAN_COMPLETED: return "SCAN";
        case WL_CONNECTED: return "OK";
        case WL_CONNECT_FAILED: return "AUTH?";
        case WL_CONNECTION_LOST: return "LOST";
        case WL_DISCONNECTED: return "DISC";
        default: return "?";
    }
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

static int16_t ccApproxTextWidthPx(const char *s, int textSize) {
    if (!s) {
        return 0;
    }
    return (int16_t)(strlen(s) * 6 * textSize);
}

/** Width/height of a string at the current text size (Adafruit/Arduino_GFX metrics). */
static void ccTextBounds(const char *s, int textSize, uint16_t *outW, uint16_t *outH) {
    *outW = 0;
    *outH = 0;
    if (!s || !s[0]) {
        return;
    }
    gfx->setTextSize(textSize);
    int16_t x1 = 0;
    int16_t y1 = 0;
    uint16_t w = 0;
    uint16_t h = 0;
    gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
    *outW = w;
    *outH = h;
}

static void ccAsciiToUpper(char *dst, const char *src, size_t dstSz) {
    size_t i = 0;
    for (; i + 1 < dstSz && src[i]; i++) {
        char c = src[i];
        if (c >= 'a' && c <= 'z') {
            c = (char)(c - 32);
        }
        dst[i] = c;
    }
    dst[i] = '\0';
}

static void ccPrintCenteredShadow(const char *line, int16_t y, int textSize, uint16_t fg) {
    if (!line || !line[0]) {
        return;
    }
    uint16_t tw = 0;
    uint16_t th = 0;
    ccTextBounds(line, textSize, &tw, &th);
    int16_t x = (MAP_DISPLAY_PX / 2) - (int16_t)(tw / 2);
    const int16_t inset = 10;
    if (x < inset) {
        x = inset;
    }
    if (x + (int16_t)tw > MAP_DISPLAY_PX - inset) {
        x = (int16_t)(MAP_DISPLAY_PX - inset - (int16_t)tw);
    }
    gfx->setTextSize(textSize);
    gfx->setTextColor(BLACK);
    gfx->setCursor((int16_t)(x + 1), (int16_t)(y + 1));
    gfx->print(line);
    gfx->setTextColor(fg);
    gfx->setCursor(x, y);
    gfx->print(line);
}

/** Perceived ~50% black overlay on RGB565 (no display alpha — darkens toward black). */
static uint16_t ccRgb565BlendBlack50(uint16_t c) {
    uint32_t r = (c >> 11) & 0x1F;
    uint32_t g = (c >> 5) & 0x3F;
    uint32_t b = c & 0x1F;
    r >>= 1;
    g >>= 1;
    b >>= 1;
    return (uint16_t)((r << 11) | (g << 5) | b);
}

static void ccDarkenPhotoBackdropRect(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
    if (!callingCardHasPhoto || s_callingCardPhoto565 == nullptr) {
        return;
    }
    int16_t bw = callingCardPhotoW;
    int16_t bh = callingCardPhotoH;
    if (bw <= 0 || bh <= 0) {
        return;
    }
    if (x0 < 0) {
        x0 = 0;
    }
    if (y0 < 0) {
        y0 = 0;
    }
    if (x1 >= bw) {
        x1 = (int16_t)(bw - 1);
    }
    if (y1 >= bh) {
        y1 = (int16_t)(bh - 1);
    }
    if (x1 < x0 || y1 < y0) {
        return;
    }
    for (int16_t yy = y0; yy <= y1; yy++) {
        uint16_t *row = s_callingCardPhoto565 + (int32_t)yy * (int32_t)bw;
        for (int16_t xx = x0; xx <= x1; xx++) {
            row[xx] = ccRgb565BlendBlack50(row[xx]);
        }
    }
}

static void ccSplitTwoLinesMax(const char *src, int maxFirst, char *line1, size_t l1sz, char *line2, size_t l2sz) {
    line1[0] = '\0';
    line2[0] = '\0';
    if (!src || !src[0]) {
        return;
    }
    size_t n = strlen(src);
    if (n <= (size_t)maxFirst) {
        strncpy(line1, src, l1sz - 1);
        line1[l1sz - 1] = '\0';
        return;
    }
    int split = maxFirst;
    while (split > 0 && src[split] != ' ') {
        split--;
    }
    if (split <= 0) {
        split = maxFirst;
    }
    strncpy(line1, src, (size_t)split);
    line1[split] = '\0';
    const char *rest = src + split;
    while (*rest == ' ') {
        rest++;
    }
    strncpy(line2, rest, l2sz - 1);
    line2[l2sz - 1] = '\0';
}

static void drawCallingCardOverlay() {
    const int16_t cx = MAP_DISPLAY_PX / 2;

    char titleUpper[CALLING_CARD_TITLE_MAX + 1];
    ccAsciiToUpper(titleUpper, callingCardTitle, sizeof(titleUpper));

    char L1[44];
    char L2[CALLING_CARD_TITLE_MAX + 1];
    // Shorter lines than square LCD: round panel clips wide title text (~12 chars/line at size 2).
    ccSplitTwoLinesMax(titleUpper, 12, L1, sizeof(L1), L2, sizeof(L2));
    if (strlen(L2) > 12) {
        L2[9] = '\0';
        strcat(L2, "...");
    }

    int16_t titleH = (int16_t)((L2[0] != '\0') ? 36 : 18);
    int16_t ratingH = callingCardHasRating ? 22 : 0;
    int16_t revH = (callingCardHasRating && callingCardHasReviewCount) ? 12 : 0;
    int16_t catH = (callingCardCategory[0] != '\0') ? 12 : 0;
    int16_t gap = 6;
    int16_t blockH = (int16_t)(titleH + gap + ratingH + revH + gap + catH + 4);
    const int16_t cy = MAP_DISPLAY_PX / 2;
    int16_t y = (int16_t)(cy - blockH / 2);
    if (y < 28) {
        y = 28;
    }

    const int16_t bgPadX = 6;
    const int16_t bgPadY = 8;
    int16_t bgY0 = (int16_t)(y - bgPadY);
    int16_t bgY1 = (int16_t)(y + blockH + bgPadY);
    int16_t bgX0 = bgPadX;
    int16_t bgX1 = (int16_t)(MAP_DISPLAY_PX - 1 - bgPadX);
    ccDarkenPhotoBackdropRect(bgX0, bgY0, bgX1, bgY1);

    if (callingCardHasPhoto && s_callingCardPhoto565 != nullptr) {
        gfx->draw16bitRGBBitmap(0, 0, s_callingCardPhoto565, callingCardPhotoW, callingCardPhotoH);
    } else {
        gfx->fillScreen(0x2104);
        gfx->setTextSize(2);
        gfx->setTextColor(WHITE);
        const char *np = "No photo";
        int16_t tw = ccApproxTextWidthPx(np, 2);
        gfx->setCursor((int16_t)(cx - tw / 2), 112);
        gfx->print(np);
    }

    if (L1[0]) {
        ccPrintCenteredShadow(L1, y, 2, WHITE);
        y += 18;
    }
    if (L2[0]) {
        ccPrintCenteredShadow(L2, y, 2, WHITE);
        y += 18;
    }
    y += gap;

    if (callingCardHasRating) {
        char rs[8];
        snprintf(rs, sizeof(rs), "%.1f", callingCardRating);
        int tsR = 2;
        int16_t numW = ccApproxTextWidthPx(rs, tsR);
        const int16_t starPitch = 14;
        const int16_t starR = 4;
        int16_t starBlockW = (int16_t)(5 * starPitch + 4);
        int16_t gapNum = 8;
        int16_t totalW = (int16_t)(starBlockW + gapNum + numW);
        int16_t left = (int16_t)(cx - totalW / 2);
        int16_t starY = (int16_t)(y + 10);
        for (int i = 0; i < 5; i++) {
            bool full = callingCardRating >= (float)(i + 1) - 0.35f;
            uint16_t starC = full ? 0xFE00 : 0x6B4D;
            gfx->fillCircle((int16_t)(left + starR + i * starPitch), starY, starR, starC);
        }
        gfx->setTextSize(tsR);
        gfx->setTextColor(BLACK);
        gfx->setCursor((int16_t)(left + starBlockW + gapNum + 1), (int16_t)(y + 1));
        gfx->print(rs);
        gfx->setTextColor(WHITE);
        gfx->setCursor((int16_t)(left + starBlockW + gapNum), y);
        gfx->print(rs);
        y = (int16_t)(y + ratingH);

        if (callingCardHasReviewCount && callingCardReviewCount >= 0) {
            char rv[40];
            snprintf(rv, sizeof(rv), "(%ld Reviews)", (long)callingCardReviewCount);
            ccPrintCenteredShadow(rv, y, 1, 0xDEFB);
            y += revH;
        }
        y += gap;
    }

    if (callingCardCategory[0]) {
        ccPrintCenteredShadow(callingCardCategory, y, 1, WHITE);
        y += catH;
    }

}

static void mapApplyDirectionsBackdropOnce() {
    if (!mapDirectionsHasMetrics || s_mapDirectionsBackdropApplied || s_mapRgb565 == nullptr) {
        return;
    }
    const int16_t y0 = 148;
    const int16_t y1 = MAP_DISPLAY_PX - 1;
    for (int16_t yy = y0; yy <= y1; yy++) {
        uint16_t *row = s_mapRgb565 + (int32_t)yy * MAP_DISPLAY_PX;
        for (int16_t xx = 0; xx < MAP_DISPLAY_PX; xx++) {
            row[xx] = ccRgb565BlendBlack50(row[xx]);
        }
    }
    s_mapDirectionsBackdropApplied = true;
}

static void drawMapOverlayFrame() {
    if (!mapHasImage || s_mapRgb565 == nullptr) {
        return;
    }
    mapApplyDirectionsBackdropOnce();
    gfx->draw16bitRGBBitmap(0, 0, s_mapRgb565, MAP_DISPLAY_PX, MAP_DISPLAY_PX);
    if (mapDirectionsHasMetrics) {
        char line1[20];
        char line2[24];
        snprintf(line1, sizeof(line1), "%.1f mi", (double)mapRouteMiles);
        snprintf(line2, sizeof(line2), "%d min", mapRouteMinutes);
        ccPrintCenteredShadow(line1, 170, 2, WHITE);
        ccPrintCenteredShadow(line2, 194, 2, 0xDEFB);
    }
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

static void resetIdleAnimStateForShow() {
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
}

void showIdleEyes() {
    gfx->fillScreen(BLACK);
    resetIdleAnimStateForShow();
    drawEyes(0, 0, 66, 66, CYAN);
}

/** After wake phase C, the framebuffer already shows full idle ovals — reset state without a black flash. */
static void resumeIdleAfterWakeAnimation() {
    resetIdleAnimStateForShow();
}

static void markActivity(const char* source) {
    (void)source;
    lastActivityMs = millis();
    bool wasSleeping = sleepAnimActive;
    sleepAnimActive = false;
    if (wasSleeping) {
        wakeUpAnimActive = true;
        wakeUpAnimStartMs = millis();
        wakeScanPhase = WAKE_SCAN_PHASE_A;
        wakeScanPhaseStartMs = wakeUpAnimStartMs;
        wakeScanHasPrevFrame = false;
        idleAnim.hasPrevFrame = false;
        sleepAnimHasPrevFrame = false;
        s_sleepZzzHavePrev = false;
        gfx->fillScreen(BLACK);
    }
}

/** Thick comic-book Z: cyan fill, black outline (reference art style). hMul = letter height in u units. */
static void drawComicZ(int16_t x, int16_t y, int16_t u, int16_t hMul, uint16_t fill, uint16_t ol) {
    if (u < 2) {
        u = 2;
    }
    if (hMul < 4) {
        hMul = 4;
    }
    const int16_t w = 6 * u;
    const int16_t h = (int16_t)(hMul * u);
    const int16_t t = u;
    gfx->fillRect((int16_t)(x - 1), (int16_t)(y - 1), (int16_t)(w + 2), (int16_t)(t + 2), ol);
    gfx->fillRect((int16_t)(x - 1), (int16_t)(y + h - t), (int16_t)(w + 2), (int16_t)(t + 2), ol);
    const int16_t x0 = (int16_t)(x + w - t);
    const int16_t y0 = (int16_t)(y + t);
    const int16_t x1 = (int16_t)(x + t);
    const int16_t y1 = (int16_t)(y + h - t);
    int16_t steps = (int16_t)(max(w, h) / 2);
    if (steps < 5) {
        steps = 5;
    }
    for (int16_t s = 0; s <= steps; s++) {
        int16_t px = (int16_t)(x0 + (int16_t)(((int32_t)(x1 - x0) * s) / steps));
        int16_t py = (int16_t)(y0 + (int16_t)(((int32_t)(y1 - y0) * s) / steps));
        gfx->fillRect((int16_t)(px - 1), (int16_t)(py - 1), (int16_t)(t + 2), (int16_t)(t + 2), ol);
    }
    gfx->fillRect(x, y, w, t, fill);
    gfx->fillRect(x, (int16_t)(y + h - t), w, t, fill);
    for (int16_t s = 0; s <= steps; s++) {
        int16_t px = (int16_t)(x0 + (int16_t)(((int32_t)(x1 - x0) * s) / steps));
        int16_t py = (int16_t)(y0 + (int16_t)(((int32_t)(y1 - y0) * s) / steps));
        gfx->fillRect(px, py, t, t, fill);
    }
}

static void drawSleepZzzOverlay(int16_t eyeTopY, uint32_t nowMs) {
    if (s_sleepZzzHavePrev) {
        gfx->fillRect(s_sleepZzzClearX, s_sleepZzzClearY, kSleepZzzBoxW, kSleepZzzBoxH, BLACK);
    }
    const uint16_t fill = kSleepZzzFill;
    const uint16_t ol = BLACK;
    const int16_t baseX = 180;
    const int16_t baseY = (int16_t)(eyeTopY - 52);

    // Gentle drift + slight per-letter wobble (ms-based so it keeps moving while eyes are still).
    const uint32_t t = nowMs % 4000U;
    const float ph = (float)t / 4000.0f * 6.2831853f;
    const int16_t gx = (int16_t)(sinf(ph) * 3.0f);
    const int16_t gy = (int16_t)(sinf(ph * 1.27f + 0.4f) * 2.5f);

    drawComicZ((int16_t)(baseX + 2 + gx + (int16_t)(sinf(ph * 2.1f) * 1.2f)),
               (int16_t)(baseY + gy + (int16_t)(sinf(ph * 1.9f + 0.2f) * 1.0f)),
               3,
               5,
               fill,
               ol);
    drawComicZ((int16_t)(baseX + 30 + gx + (int16_t)(sinf(ph * 2.4f + 1.1f) * 1.2f)),
               (int16_t)(baseY + 8 + gy + (int16_t)(sinf(ph * 2.0f + 2.0f) * 1.0f)),
               2,
               5,
               fill,
               ol);
    drawComicZ((int16_t)(baseX + 8 + gx + (int16_t)(sinf(ph * 2.7f + 0.5f) * 1.2f)),
               (int16_t)(baseY + 26 + gy + (int16_t)(sinf(ph * 2.2f + 1.7f) * 1.0f)),
               2,
               4,
               fill,
               ol);

    s_sleepZzzClearX = (int16_t)(baseX - 4);
    s_sleepZzzClearY = (int16_t)(baseY - 4);
    s_sleepZzzHavePrev = true;
}

static void updateSleepEyesAnimation() {
    const uint32_t now = millis();
    if (now - lastIdleEyesUpdate < 80) return;
    lastIdleEyesUpdate = now;

    // Sleep = thin rectangular lids with a gentle vertical "breathing" bob.
    const uint32_t cycleMs = 2600;
    const float phase = (float)(now % cycleMs) / (float)cycleMs;  // 0..1
    const float wave = sinf(phase * 6.2831853f);                  // -1..1
    const int16_t breathOffsetY = (int16_t)(wave * 3.0f);         // subtle rise/fall

    const int16_t eyeW = 74;
    const int16_t eyeH = 10;
    const int16_t leftX = kSleepWakeEyeCenterLeftX - (eyeW / 2);
    const int16_t rightX = kSleepWakeEyeCenterRightX - (eyeW / 2);
    const int16_t y = (kSleepWakeEyeCenterY - (eyeH / 2)) + breathOffsetY;

    if (!sleepAnimHasPrevFrame) {
        gfx->fillRect(leftX, y, eyeW, eyeH, CYAN);
        gfx->fillRect(rightX, y, eyeW, eyeH, CYAN);
        drawSleepZzzOverlay(y, now);
        sleepAnimHasPrevFrame = true;
        sleepAnimPrevY = y;
        return;
    }

    if (y == sleepAnimPrevY) {
        drawSleepZzzOverlay(y, now);
        return;
    }

    // Differential redraw to avoid full-screen flicker during sleep bobbing.
    gfx->fillRect(leftX, sleepAnimPrevY, eyeW, eyeH, BLACK);
    gfx->fillRect(rightX, sleepAnimPrevY, eyeW, eyeH, BLACK);
    gfx->fillRect(leftX, y, eyeW, eyeH, CYAN);
    gfx->fillRect(rightX, y, eyeW, eyeH, CYAN);
    drawSleepZzzOverlay(y, now);
    sleepAnimPrevY = y;
}

static void updateWakeUpAnimation() {
    const uint32_t now = millis();
    // Failsafe: never stay stuck in wake (should complete via phase C).
    if (now - wakeUpAnimStartMs > 8000u) {
        wakeUpAnimActive = false;
        wakeScanHasPrevFrame = false;
        wakeScanPrevBarX = 0;
        wakeScanPrevBarW = 0;
        wakeScanPrevEyeW = 0;
        wakeScanPrevSlitY = 0;
        wakeScanPrevSlitH = 0;
        idleAnim.hasPrevFrame = false;
        showIdleEyes();
        return;
    }

    const uint32_t phaseElapsed = now - wakeScanPhaseStartMs;
    const uint32_t phaseADurationMs = 500;
    const uint32_t phaseBDurationMs = 1000;

    if (wakeScanPhase == WAKE_SCAN_PHASE_A && phaseElapsed >= phaseADurationMs) {
        wakeScanPhase = WAKE_SCAN_PHASE_B;
        wakeScanPhaseStartMs = now;
        wakeScanHasPrevFrame = false;
    } else if (wakeScanPhase == WAKE_SCAN_PHASE_B && phaseElapsed >= phaseBDurationMs) {
        wakeScanPhase = WAKE_SCAN_PHASE_C;
        wakeScanPhaseStartMs = now;
        wakeScanHasPrevFrame = false;
        gfx->fillScreen(BLACK);
    }

    if (wakeScanPhase == WAKE_SCAN_PHASE_C) {
        // Ease from slit-sized ovals to full idle geometry, then hand off without a flash.
        const uint32_t cElapsed = now - wakeScanPhaseStartMs;
        const uint32_t cDuration = 400;
        float t = (cDuration > 0) ? (float)cElapsed / (float)cDuration : 1.0f;
        if (t > 1.0f) {
            t = 1.0f;
        }
        int16_t ry = (int16_t)(20 + (46.0f * t));
        int16_t yOffset = (int16_t)(8.0f * (1.0f - t));
        if (ry < 14) {
            ry = 14;
        }
        if (ry > 66) {
            ry = 66;
        }
        drawEyes(0, yOffset, ry, ry, CYAN);

        if (cElapsed >= cDuration) {
            wakeUpAnimActive = false;
            wakeScanHasPrevFrame = false;
            wakeScanPrevBarX = 0;
            wakeScanPrevBarW = 0;
            wakeScanPrevEyeW = 0;
            wakeScanPrevSlitY = 0;
            wakeScanPrevSlitH = 0;
            resumeIdleAfterWakeAnimation();
        }
        return;
    }

    // Phase A/B: slit + moving scan bar with differential redraw.
    const int16_t eyeW = (wakeScanPhase == WAKE_SCAN_PHASE_A) ? 62 : 74;
    const int16_t eyeH = (wakeScanPhase == WAKE_SCAN_PHASE_A) ? 8 : 10;
    const int16_t leftX = kSleepWakeEyeCenterLeftX - (eyeW / 2);
    const int16_t rightX = kSleepWakeEyeCenterRightX - (eyeW / 2);
    const int16_t slitY = kSleepWakeEyeCenterY - (eyeH / 2);
    const uint32_t curPhaseElapsed = now - wakeScanPhaseStartMs;
    const uint32_t curPhaseDuration = (wakeScanPhase == WAKE_SCAN_PHASE_A) ? phaseADurationMs : phaseBDurationMs;
    float p = (curPhaseDuration > 0) ? (float)curPhaseElapsed / (float)curPhaseDuration : 1.0f;
    if (p > 1.0f) p = 1.0f;

    const int16_t barW = 8;
    const int16_t barXMin = leftX - 4;
    const int16_t barTravel = eyeW + 8;
    float barProgress = p;
    if (wakeScanPhase == WAKE_SCAN_PHASE_B) {
        // Red phase sweeps back-and-forth for one second.
        float twoWay = p * 2.0f;
        barProgress = (twoWay <= 1.0f) ? twoWay : (2.0f - twoWay);
    }
    const int16_t barX = (int16_t)(barXMin + (barProgress * barTravel));
    const uint16_t scanColor = (wakeScanPhase == WAKE_SCAN_PHASE_A) ? WHITE : RED;

    if (!wakeScanHasPrevFrame) {
        // First frame in a phase: draw base slit + scan bar.
        gfx->fillRect(leftX, slitY, eyeW, eyeH, CYAN);
        gfx->fillRect(rightX, slitY, eyeW, eyeH, CYAN);
        gfx->fillRect(barX, slitY, barW, eyeH, scanColor);
        gfx->fillRect(barX + (rightX - leftX), slitY, barW, eyeH, scanColor);
        wakeScanHasPrevFrame = true;
        wakeScanPrevBarX = barX;
        wakeScanPrevBarW = barW;
        wakeScanPrevEyeW = eyeW;
        wakeScanPrevSlitY = slitY;
        wakeScanPrevSlitH = eyeH;
        return;
    }

    // If slit geometry changed between phases, clear old and repaint current slit once.
    if (wakeScanPrevSlitY != slitY || wakeScanPrevSlitH != eyeH || wakeScanPrevEyeW != eyeW) {
        int16_t prevLeftX = kSleepWakeEyeCenterLeftX - (wakeScanPrevEyeW / 2);
        int16_t prevRightX = kSleepWakeEyeCenterRightX - (wakeScanPrevEyeW / 2);
        gfx->fillRect(prevLeftX, wakeScanPrevSlitY, wakeScanPrevEyeW, wakeScanPrevSlitH, BLACK);
        gfx->fillRect(prevRightX, wakeScanPrevSlitY, wakeScanPrevEyeW, wakeScanPrevSlitH, BLACK);
        gfx->fillRect(leftX, slitY, eyeW, eyeH, CYAN);
        gfx->fillRect(rightX, slitY, eyeW, eyeH, CYAN);
    }

    if (barX != wakeScanPrevBarX) {
        // Erase previous bar segment, restore slit beneath, then draw new bar.
        gfx->fillRect(wakeScanPrevBarX, slitY, wakeScanPrevBarW, eyeH, CYAN);
        gfx->fillRect(
            wakeScanPrevBarX + (rightX - leftX), slitY, wakeScanPrevBarW, eyeH, CYAN
        );
        gfx->fillRect(barX, slitY, barW, eyeH, scanColor);
        gfx->fillRect(barX + (rightX - leftX), slitY, barW, eyeH, scanColor);
        wakeScanPrevBarX = barX;
    }
    wakeScanPrevBarW = barW;
    wakeScanPrevEyeW = eyeW;
    wakeScanPrevSlitY = slitY;
    wakeScanPrevSlitH = eyeH;
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

/** Reject garbage DateTime when PCF8563 I2C read fails (invalid month/day etc.). */
static bool isPlausibleRtc(const DateTime& t) {
    int y = t.year();
    if (y < 2020 || y > 2100) {
        return false;
    }
    if (t.month() < 1 || t.month() > 12) {
        return false;
    }
    if (t.day() < 1 || t.day() > 31) {
        return false;
    }
    if (t.hour() > 23 || t.minute() > 59 || t.second() > 59) {
        return false;
    }
    return true;
}

void updateTimeScreen() {
    DateTime now = rtc.now();
    if (!isPlausibleRtc(now)) {
        return;
    }
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

static void syncEspCameraPowerState();

static void setDeviceVisionCaptureEnabled(bool en) {
    deviceVisionCaptureEnabled = en;
    preferences.begin("pixel_prefs", false);
    preferences.putBool("vision_cap", en);
    preferences.end();
    syncEspCameraPowerState();
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

static void loadWakeWordFromPrefs() {
    preferences.begin("pixel_prefs", true);
    deviceWakeWordEnabled = preferences.getBool("wake_word", true);
    preferences.end();
}

static void setDeviceWakeWordEnabled(bool en) {
    deviceWakeWordEnabled = en;
    preferences.begin("pixel_prefs", false);
    preferences.putBool("wake_word", en);
    preferences.end();
}

static void loadPresenceFromPrefs() {
    preferences.begin("pixel_prefs", true);
    presenceScanEnabled = preferences.getBool("presence_scan", false);
    presenceScanIntervalSec = preferences.getUInt("presence_iv", 5);
    greetingCooldownMinutes = preferences.getUInt("presence_cd", 30);
    preferences.end();
    if (presenceScanIntervalSec < 3u) {
        presenceScanIntervalSec = 3u;
    }
    if (presenceScanIntervalSec > 300u) {
        presenceScanIntervalSec = 300u;
    }
    if (greetingCooldownMinutes < 1u) {
        greetingCooldownMinutes = 1u;
    }
    if (greetingCooldownMinutes > 720u) {
        greetingCooldownMinutes = 720u;
    }
}

static void loadSleepTimeoutFromPrefs() {
    preferences.begin("pixel_prefs", true);
    sleepTimeoutSec = preferences.getUInt("sleep_to", 300);
    preferences.end();
    if (sleepTimeoutSec < 30u) {
        sleepTimeoutSec = 30u;
    }
    if (sleepTimeoutSec > 1800u) {
        sleepTimeoutSec = 1800u;
    }
}

static void applyRuntimePresenceScan(bool en, uint32_t intervalSec, uint32_t cooldownMin) {
    presenceScanEnabled = en;
    presenceScanIntervalSec = intervalSec;
    if (presenceScanIntervalSec < 3u) {
        presenceScanIntervalSec = 3u;
    }
    if (presenceScanIntervalSec > 300u) {
        presenceScanIntervalSec = 300u;
    }
    greetingCooldownMinutes = cooldownMin;
    if (greetingCooldownMinutes < 1u) {
        greetingCooldownMinutes = 1u;
    }
    if (greetingCooldownMinutes > 720u) {
        greetingCooldownMinutes = 720u;
    }
    preferences.begin("pixel_prefs", false);
    preferences.putBool("presence_scan", presenceScanEnabled);
    preferences.putUInt("presence_iv", presenceScanIntervalSec);
    preferences.putUInt("presence_cd", greetingCooldownMinutes);
    preferences.end();
    lastPresenceScanMs = 0;
    syncEspCameraPowerState();
}

static void applyRuntimeSleepTimeout(uint32_t timeoutSec) {
    sleepTimeoutSec = timeoutSec;
    if (sleepTimeoutSec < 30u) {
        sleepTimeoutSec = 30u;
    }
    if (sleepTimeoutSec > 1800u) {
        sleepTimeoutSec = 1800u;
    }
    preferences.begin("pixel_prefs", false);
    preferences.putUInt("sleep_to", sleepTimeoutSec);
    preferences.end();
    markActivity("runtime_sleep_timeout");
}

static camera_config_t g_camConfig;
static bool g_espCameraPowered = false;

static void fillDefaultCameraConfig(camera_config_t* c) {
    c->ledc_channel = LEDC_CHANNEL_0;
    c->ledc_timer = LEDC_TIMER_0;
    c->pin_d0 = 15;
    c->pin_d1 = 17;
    c->pin_d2 = 18;
    c->pin_d3 = 16;
    c->pin_d4 = 14;
    c->pin_d5 = 12;
    c->pin_d6 = 11;
    c->pin_d7 = 48;
    c->pin_xclk = 10;
    c->pin_pclk = 13;
    c->pin_vsync = 38;
    c->pin_href = 47;
    c->pin_sccb_sda = 40;
    c->pin_sccb_scl = 39;
    c->pin_pwdn = -1;
    c->pin_reset = -1;
    c->xclk_freq_hz = 20000000;
    c->frame_size = FRAMESIZE_VGA;
    c->pixel_format = PIXFORMAT_JPEG;
    c->grab_mode = CAMERA_GRAB_LATEST;
    c->fb_location = CAMERA_FB_IN_PSRAM;
    c->jpeg_quality = 20;
    c->fb_count = 2;
}

static bool ensureEspCameraPowered() {
    if (g_espCameraPowered && esp_camera_sensor_get() != nullptr) {
        return true;
    }
    fillDefaultCameraConfig(&g_camConfig);
    esp_err_t err = esp_camera_init(&g_camConfig);
    if (err != ESP_OK) {
        Serial.printf("[Cam] esp_camera_init failed: %d\n", (int)err);
        g_espCameraPowered = false;
        return false;
    }
    g_espCameraPowered = true;
    return true;
}

static void syncEspCameraPowerState() {
    const bool needCam =
        deviceVisionCaptureEnabled || presenceScanEnabled || pendingEnrollmentCapture;
    if (needCam) {
        (void)ensureEspCameraPowered();
    } else {
        if (!g_espCameraPowered) {
            return;
        }
        esp_camera_deinit();
        g_espCameraPowered = false;
        Serial.println("[Cam] powered down (vision & presence off, no enrollment)");
    }
}

/**
 * Face presence (0x06) and enrollment (0x07) snapshots.
 * Uses VGA + low jpeg_quality value (ESP: lower number = better JPEG). Requires
 * WEBSOCKETS_MAX_DATA_SIZE in platformio.ini for multi‑10KB frames.
 */
static const framesize_t kFaceHubFramesize = FRAMESIZE_VGA;
/** ESP camera JPEG quality 0–63; lower = higher quality (larger files). */
static const int kFaceHubJpegQuality = 12;

static bool captureAndSendJpegPacket(uint8_t packetType) {
    const bool isFaceHubPacket = (packetType == 0x06 || packetType == 0x07);
    if (!hubWebSocketEnabled || !isWsConnected) {
        if (isFaceHubPacket) {
            Serial.println("[Face] snapshot skipped (hub WebSocket not connected)");
        }
        return false;
    }
    if (!ensureEspCameraPowered()) {
        if (isFaceHubPacket) {
            Serial.println("[Face] snapshot skipped (camera not available)");
        }
        return false;
    }
    sensor_t* s = esp_camera_sensor_get();
    if (!s) {
        if (isFaceHubPacket) {
            Serial.println("[Face] snapshot failed (no camera sensor)");
        }
        return false;
    }
    if (isFaceHubPacket) {
        const char* why = (packetType == 0x06) ? "presence face scan" : "enrollment reference";
        Serial.printf("[Face] capturing VGA (quality=%d) for %s...\n", kFaceHubJpegQuality, why);
    }
    framesize_t prevSize = s->status.framesize;
    int prevQ = s->status.quality;
    s->set_framesize(s, kFaceHubFramesize);
    s->set_quality(s, kFaceHubJpegQuality);
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
        s->set_framesize(s, prevSize);
        s->set_quality(s, prevQ);
        if (isFaceHubPacket) {
            Serial.println("[Face] snapshot failed (esp_camera_fb_get)");
        }
        return false;
    }
    size_t len = fb->len;
    uint8_t* packet = (uint8_t*)ps_malloc(len + 1);
    if (!packet) {
        esp_camera_fb_return(fb);
        s->set_framesize(s, prevSize);
        s->set_quality(s, prevQ);
        if (isFaceHubPacket) {
            Serial.printf("[Face] snapshot failed (ps_malloc %u bytes)\n", (unsigned)(len + 1));
        }
        return false;
    }
    packet[0] = packetType;
    memcpy(packet + 1, fb->buf, len);
    esp_camera_fb_return(fb);
    s->set_framesize(s, prevSize);
    s->set_quality(s, prevQ);
    wsLock();
    webSocket.sendBIN(packet, len + 1);
    webSocket.loop();
    wsUnlock();
    if (isFaceHubPacket) {
        const char* why = (packetType == 0x06) ? "presence scan" : "enrollment";
        Serial.printf(
            "[Face] sent %s to hub: JPEG %u bytes, packet 0x%02X (%u bytes total)\n",
            why,
            (unsigned)len,
            (unsigned)packetType,
            (unsigned)(len + 1)
        );
    }
    free(packet);
    return true;
}

// ==========================================
//          SETTINGS SCREEN UI
//          (Vision / presence scan — hub only; device keeps BT + Wi‑Fi tools.)
// ==========================================
static const uint16_t kSetBg       = 0x10A2; // dark gray
static const uint16_t kSetRing     = 0x2124;
static const uint16_t kSetTitle    = 0xEF9D; // warm white
static const uint16_t kSetLabel    = 0xB5B6; // light gray

static const int16_t kBleBtnX = 52;
static const int16_t kBleBtnY = 88;
static const int16_t kBleBtnW = 136;
static const int16_t kBleBtnH = 32;
static const uint16_t kBleBtnFill = 0x19BF; // blue-gray, readable on round panel

static const int16_t kWifiResetBtnX = 52;
static const int16_t kWifiResetBtnY = 158;
static const int16_t kWifiResetBtnW = 136;
static const int16_t kWifiResetBtnH = 30;
static const uint16_t kWifiResetFill = 0xC986; // distinct from BT

void drawSettingsScreen() {
    gfx->fillScreen(kSetBg);
    gfx->drawCircle(120, 120, 118, kSetRing);

    // Title
    gfx->setTextSize(2);
    gfx->setTextColor(kSetTitle);
    const char* title = "SETTINGS";
    int16_t tw = (int16_t)(strlen(title) * 12);
    gfx->setCursor((240 - tw) / 2, 40);
    gfx->print(title);

    // Bluetooth Wi‑Fi provisioning (turns off Wi‑Fi, same flow as first-time BLE setup)
    gfx->fillRoundRect(kBleBtnX, kBleBtnY, kBleBtnW, kBleBtnH, 10, kBleBtnFill);
    gfx->setTextSize(2);
    gfx->setTextColor(WHITE);
    const char* bleText = "BT SETUP";
    int16_t btw = (int16_t)(strlen(bleText) * 12);
    gfx->setCursor((240 - btw) / 2, kBleBtnY + 7);
    gfx->print(bleText);

    gfx->setTextSize(1);
    gfx->setTextColor(kSetLabel);
    const char* bleLabel = "CHANGE WIFI VIA BT";
    int16_t blw = (int16_t)(strlen(bleLabel) * 6);
    gfx->setCursor((240 - blw) / 2, kBleBtnY + kBleBtnH + 4);
    gfx->print(bleLabel);

    // Erase stored Wi‑Fi (NVS); next boot uses BLE setup or new provision
    gfx->fillRoundRect(kWifiResetBtnX, kWifiResetBtnY, kWifiResetBtnW, kWifiResetBtnH, 8, kWifiResetFill);
    gfx->setTextSize(2);
    gfx->setTextColor(WHITE);
    const char* clrText = "CLR WIFI";
    int16_t ctw = (int16_t)(strlen(clrText) * 12);
    gfx->setCursor((240 - ctw) / 2, kWifiResetBtnY + 6);
    gfx->print(clrText);

    gfx->setTextSize(1);
    gfx->setTextColor(kSetLabel);
    const char* clrLabel = "ERASE SAVED WIFI";
    int16_t clrw = (int16_t)(strlen(clrLabel) * 6);
    gfx->setCursor((240 - clrw) / 2, kWifiResetBtnY + kWifiResetBtnH + 4);
    gfx->print(clrLabel);
}

// ==========================================
//      BLE PROVISIONING CALLBACKS
// ==========================================
class ProvisionCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      // GATT write runs on NimBLE/BT tasks with small stacks — keep heap JSON, cap payload size.
      // NimBLE-Arduino returns std::string from getValue(), not Arduino String.
      std::string rxValue = pCharacteristic->getValue();
      if (rxValue.empty() || rxValue.size() > 900) {
        return;
      }

      {
        DynamicJsonDocument doc(768);
        DeserializationError error = deserializeJson(doc, rxValue);
        
        if (!error) {
            // Hub sends two writes: (1) ssid+password (2) hub_ip+hub_port only. Never treat hub-only JSON as Wi‑Fi.
            String new_ssid;
            String new_pass;
            bool hasWifi = false;
            if (!doc["ssid"].isNull()) {
                if (doc["ssid"].is<const char*>()) {
                    const char* s = doc["ssid"].as<const char*>();
                    if (s && s[0] && strcmp(s, "null") != 0) {
                        new_ssid = String(s);
                        new_ssid.trim();
                    }
                } else {
                    new_ssid = doc["ssid"].as<String>();
                    new_ssid.trim();
                }
                if (new_ssid.length() > 0) {
                    if (doc["password"].is<const char*>()) {
                        new_pass = String(doc["password"].as<const char*>());
                    } else {
                        new_pass = doc["password"].as<String>();
                    }
                    hasWifi = true;
                }
            }

            bool hasHub = false;
            if (!doc["hub_ip"].isNull()) {
                String hip = doc["hub_ip"].is<const char*>() ? String(doc["hub_ip"].as<const char*>()) : doc["hub_ip"].as<String>();
                hip.trim();
                hasHub = hip.length() > 0;
            }

            if (hasWifi) {
                Serial.printf("[BLE] WiFi credentials received over GATT (ssid len=%u)\n", (unsigned)new_ssid.length());
                preferences.begin("wifi_creds", false);
                preferences.putString("ssid", new_ssid);
                preferences.putString("password", new_pass);
                preferences.end();
            }

            if (hasHub) {
                String hip = doc["hub_ip"].as<String>();
                hip.trim();
                int hp = doc["hub_port"] | 8000;
                if (hp < 1 || hp > 65535) hp = 8000;
                preferences.begin("hub_ep", false);
                preferences.putString("ip", hip);
                preferences.putUInt("port", (uint32_t)hp);
                preferences.end();
                Serial.printf("[BLE] Hub endpoint stored for WebSocket: %s:%d\n", hip.c_str(), hp);
            }

            if (hasWifi || hasHub) {
                needsRestart = true;
            }
        }
      }
    }
};

void startBLEProvisioning() {
    currentState = STATE_SETUP;
    updateScreen("BLE SETUP", BLUE);
    Serial.println("[BLE] startBLEProvisioning: screen shows BLE SETUP");

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
    Serial.println("[BLE] NimBLE advertising ON — connect from hub (name Pixel, provisioning GATT writable)");
}

/** Erase stored SSID/password (and fail counter), show message, restart — next boot opens BLE setup if no creds. */
static void clearWifiCredentialsAndRestart() {
    Serial.println("[WiFi] CLR WIFI: disconnecting, erasing NVS wifi_creds + wifi_meta.fail_count");
    if (hubWebSocketEnabled) {
        webSocket.disconnect();
        delay(50);
    }
    hubWebSocketEnabled = false;
    isWsConnected = false;
    if (WiFi.getMode() != WIFI_OFF) {
        WiFi.disconnect(true, false);
        WiFi.mode(WIFI_OFF);
        delay(80);
    }

    preferences.begin("wifi_creds", false);
    preferences.remove("ssid");
    preferences.remove("password");
    preferences.end();

    preferences.begin("wifi_meta", false);
    preferences.putUChar("fail_count", 0);
    preferences.end();

    Serial.println("[WiFi] Stored Wi-Fi credentials cleared; rebooting");
    updateScreen("CLR WIFI", RED);
    delay(700);
    ESP.restart();
}

/** Disconnect hub stream, turn off Wi‑Fi, then advertise BLE for Wi‑Fi reprovisioning (from SETTINGS). */
static void enterBleProvisioningFromSettings() {
    Serial.println("[BT] enterBleProvisioningFromSettings: user chose BT SETUP in SETTINGS");
    updateScreen("BT ON", CYAN);
    delay(250);

    if (hubWebSocketEnabled) {
        Serial.println("[BT] disconnecting hub WebSocket...");
        webSocket.disconnect();
        delay(80);
    }
    hubWebSocketEnabled = false;
    isWsConnected = false;

    Serial.printf("[BT] WiFi before off: status=%d mode=%d\n", (int)WiFi.status(), (int)WiFi.getMode());
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
    delay(200);
    Serial.println("[BT] WiFi OFF — radio ready for BLE");

    startBLEProvisioning();
}

/** No Wi‑Fi on boot: idle face only. BLE provisioning runs only from SETTINGS → BT SETUP. */
static void enterWifiOfflineIdle() {
    Serial.println("[WiFi] Stopping Wi‑Fi; idle face. Use SETTINGS → BT SETUP to add or change Wi‑Fi.");
    if (WiFi.getMode() != WIFI_OFF) {
        WiFi.disconnect(true, false);
        WiFi.mode(WIFI_OFF);
        delay(80);
    }
    hubWebSocketEnabled = false;
    isWsConnected = false;
    currentState = STATE_IDLE;
    showIdleEyes();
    loadRuntimeVisionFromPrefs();
}

void setupWiFi() {
    preferences.begin("wifi_creds", true); 
    String saved_ssid = preferences.getString("ssid", "");
    String saved_pass = preferences.getString("password", "");
    preferences.end();

    saved_ssid.trim();
    if (saved_ssid.length() == 0) {
        enterWifiOfflineIdle();
        return;
    }
    
    esp_wifi_set_max_tx_power(78); 
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);

    bool connected = false;
    for (uint8_t attempt = 0; attempt < WIFI_CONNECT_ATTEMPTS; attempt++) {
        WiFi.disconnect(true, false);
        delay(80);
        Serial.printf("[WiFi] begin ssid len=%u attempt %u/%u\n", (unsigned)saved_ssid.length(), (unsigned)(attempt + 1), (unsigned)WIFI_CONNECT_ATTEMPTS);
        updateScreen("CONNECTING", YELLOW);
        WiFi.begin(saved_ssid.c_str(), saved_pass.c_str());

        uint32_t attemptStart = millis();
        uint32_t lastLog = 0;
        wl_status_t lastSt = WL_IDLE_STATUS;

        while (WiFi.status() != WL_CONNECTED && (millis() - attemptStart) < WIFI_CONNECT_TIMEOUT_MS) {
            yield();
            delay(100);
            uint32_t now = millis();
            wl_status_t st = (wl_status_t)WiFi.status();
            if (st != lastSt) {
                lastSt = st;
                Serial.printf("[WiFi] status -> %d (%s)\n", (int)st, wifiStatusLabel(st));
            }
            if (now - lastLog >= 1500) {
                lastLog = now;
                Serial.printf("[WiFi] still connecting... %lus (%s)\n", (unsigned long)((now - attemptStart) / 1000), wifiStatusLabel(st));
            }
        }

        if (WiFi.status() == WL_CONNECTED) {
            connected = true;
            Serial.printf("[WiFi] connected, IP %s\n", WiFi.localIP().toString().c_str());
            break;
        }
        Serial.printf("[WiFi] attempt %u failed, status=%d (%s)\n", (unsigned)(attempt + 1), (int)WiFi.status(), wifiStatusLabel((wl_status_t)WiFi.status()));
    }

    if (!connected) {
        enterWifiOfflineIdle();
        return;
    }
    
    preferences.begin("wifi_meta", false);
    preferences.putUChar("fail_count", 0);
    preferences.end();

    updateScreen("TIME SYNC", CYAN);
    syncRtcFromWifiNtp();
    lastRtcWifiSyncAttemptMs = millis();
    currentState = STATE_IDLE; 
    showIdleEyes();

    loadRuntimeVisionFromPrefs();

    {
        preferences.begin("hub_ep", true);
        String hubHost = preferences.getString("ip", "");
        uint32_t hubPortU = preferences.getUInt("port", 0);
        preferences.end();
        if (hubHost.length() == 0) {
            hubHost = String(backend_ip);
        }
        uint16_t hubPort = (hubPortU > 0 && hubPortU <= 65535u) ? (uint16_t)hubPortU : (uint16_t)backend_port;
        Serial.printf("[Hub] WebSocket connecting to %s:%u\n", hubHost.c_str(), (unsigned)hubPort);
        webSocket.begin(hubHost, hubPort, "/ws/stream");
    }
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
                    StaticJsonDocument<2048> doc;
                    DeserializationError err = deserializeJson(doc, payload, length);
                    if (!err) {
                        const char* msgType = doc["type"] | "";
                        const char* status = doc["status"] | "";

                        if (strcmp(msgType, "gemini_first_token") == 0) {
                            geminiFirstTokenReceived = true;
                        } else if (strcmp(msgType, "runtime_vision") == 0) {
                            // Only apply when hub sends explicit "enabled" (avoid default-on if key is missing).
                            JsonVariant ven = doc["enabled"];
                            if (!ven.isNull()) {
                                setDeviceVisionCaptureEnabled(ven.as<bool>());
                            }
                        } else if (strcmp(msgType, "runtime_wake_word") == 0) {
                            bool en = doc["enabled"] | true;
                            setDeviceWakeWordEnabled(en);
                        } else if (strcmp(msgType, "runtime_live_voice") == 0) {
                            deviceLiveVoiceStream = doc["enabled"] | false;
                        } else if (strcmp(msgType, "wake_processing") == 0) {
                            if (currentState == STATE_IDLE) {
                                currentState = STATE_UPLOADING;
                                lastUploadingEyesUpdate = 0;
                                updateUploadingThinkingAnimation();
                            }
                        } else if (strcmp(msgType, "runtime_timezone") == 0) {
                            const char* tzRule = doc["timezone_rule"] | DEFAULT_TIMEZONE_RULE;
                            setTimezoneRule(tzRule);
                            if (WiFi.status() == WL_CONNECTED) {
                                syncRtcFromWifiNtp();
                                lastRtcWifiSyncAttemptMs = millis();
                            }
                        } else if (strcmp(msgType, "runtime_presence_scan") == 0) {
                            bool en = doc["enabled"] | false;
                            uint32_t iv = (uint32_t)(doc["interval_sec"] | 5);
                            uint32_t cd = (uint32_t)(doc["cooldown_minutes"] | 30);
                            applyRuntimePresenceScan(en, iv, cd);
                        } else if (strcmp(msgType, "runtime_sleep_timeout") == 0) {
                            uint32_t toSec = (uint32_t)(doc["timeout_sec"] | 300);
                            applyRuntimeSleepTimeout(toSec);
                        } else if (strcmp(msgType, "activity_event") == 0) {
                            const char* src = doc["source"] | "hub";
                            markActivity(src);
                        } else if (strcmp(msgType, "request_reference_capture") == 0) {
                            pendingEnrollmentCapture = true;
                            syncEspCameraPowerState();
                        } else if (strcmp(msgType, "face_animation") == 0) {
                            const char* anim = doc["animation"] | "speaking";
                            const char* w = doc["words"] | "";
                            int dur = doc["duration_ms"] | FACE_ANIM_DURATION_MS;
                            if (dur < 800) dur = 800;
                            if (dur > 10000) dur = 10000;

                            {
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
                                if (!timeScreenActive && !idleAnim.hasPrevFrame) {
                                    idleAnim.hasPrevFrame = false;
                                }
                            }
                        } else if (strcmp(status, "error") == 0) {
                            // Don't leave user stuck in thinking animation on backend errors.
                            geminiFirstTokenReceived = true;
                        }
                    }
                }
                break;
            case WStype_BIN:
                // Map/calling-card binary packets (0x04/0x05) removed.
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
    hubWebSocketEnabled = true;
}

// Rolling JPEGs to hub (0x02) while idle + wake + vision on — hub builds MP4 for Gemini + dashboard.
// Hub "browser live voice" disables wake word on-device but still needs 0x02 when vision is on (Gemini Live).
static unsigned long s_lastWakeVideoFrameMs = 0;

// ==========================================
//     FREERTOS WAKE STREAM (CORE 0)
//     Single task: 0x10 PCM + optional 0x02 JPEG (vision on). Avoids concurrent WebSocket use (disconnect loops).
//     PCM is skipped when wake word is off (e.g. browser mic); JPEG still sent if vision is enabled.
// ==========================================
void wakeStreamTask(void* pvParameters) {
    uint8_t* pkt = (uint8_t*)malloc(WAKE_STREAM_CHUNK + 1);
    if (!pkt) {
        Serial.println("[Wake] malloc failed for stream buffer");
        vTaskDelete(NULL);
        return;
    }
    (void)pvParameters;
    for (;;) {
        if (
            hubWebSocketEnabled &&
            isWsConnected &&
            (currentState == STATE_IDLE
                || (currentState == STATE_UPLOADING
                    && (deviceLiveVoiceStream || deviceVisionCaptureEnabled))) &&
            (deviceWakeWordEnabled || deviceVisionCaptureEnabled) &&
            !timeScreenActive &&
            !mapOverlayActive &&
            !callingCardOverlayActive &&
            !faceAnimActive
        ) {
            size_t n = 0;
            if (deviceWakeWordEnabled) {
                i2s_read(I2S_NUM_0, pkt + 1, WAKE_STREAM_CHUNK, &n, pdMS_TO_TICKS(120));
            }

            uint8_t* videoPacket = nullptr;
            size_t videoLen = 0;
            if (deviceVisionCaptureEnabled) {
                unsigned long now = millis();
                if (now - s_lastWakeVideoFrameMs >= WAKE_VIDEO_FRAME_MS) {
                    s_lastWakeVideoFrameMs = now;
                    if (ensureEspCameraPowered()) {
                        camera_fb_t* fb = esp_camera_fb_get();
                        if (fb && fb->len > 0 && fb->len < 62000) {
                            videoLen = fb->len;
                            videoPacket = (uint8_t*)ps_malloc(videoLen + 1);
                            if (videoPacket) {
                                videoPacket[0] = 0x02;
                                memcpy(videoPacket + 1, fb->buf, videoLen);
                            }
                        }
                        if (fb) {
                            esp_camera_fb_return(fb);
                        }
                    }
                }
            }

            wsLock();
            if (n > 0) {
                pkt[0] = 0x10;
                webSocket.sendBIN(pkt, n + 1);
            }
            if (videoPacket) {
                webSocket.sendBIN(videoPacket, videoLen + 1);
                free(videoPacket);
                videoPacket = nullptr;
            }
            webSocket.loop();
            wsUnlock();

            vTaskDelay(pdMS_TO_TICKS(2));
        } else {
            vTaskDelay(pdMS_TO_TICKS(25));
        }
    }
}

// ==========================================
//                ARDUINO SETUP
// ==========================================
void setup() {
    Serial.begin(115200);
    wsSendMutexInit();
    randomSeed((uint32_t)esp_random());
    
    pinMode(TFT_BL, OUTPUT); digitalWrite(TFT_BL, HIGH);
    gfx->begin(80000000); 
    updateScreen("BOOTING...", WHITE);
    
    // Initialize native I2C for the CHSC6X Touch Controller & RTC (shared bus).
    Wire.begin(TOUCH_SDA, TOUCH_SCL);
    Wire.setClock(100000);
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

    setupMicrophone();

    loadTimezoneRuleFromPrefs();
    loadWakeWordFromPrefs();
    loadPresenceFromPrefs();
    loadSleepTimeoutFromPrefs();
    loadRuntimeVisionFromPrefs();
    lastPresenceScanMs = millis();
    lastActivityMs = millis();
    syncEspCameraPowerState();
    setupWiFi();

    xTaskCreatePinnedToCore(
        wakeStreamTask, "WakeStream", 16384, NULL, 1, NULL, 0
    );
}

// ==========================================
//                ARDUINO LOOP
// ==========================================
void loop() {
    static RobotState previousVisualState = STATE_SETUP;

    // Force a full refresh on transitions to/from the thinking animation.
    if (currentState != previousVisualState) {
        if ((currentState == STATE_UPLOADING || previousVisualState == STATE_UPLOADING) && !mapOverlayActive
            && !callingCardOverlayActive) {
            gfx->fillScreen(BLACK);
            idleAnim.hasPrevFrame = false;
            uploadAnim.hasPrevFrame = false;
            uploadAnim.prevQSize = 0;
            lastUploadingEyesUpdate = 0;
            lastIdleEyesUpdate = 0;
        }
        previousVisualState = currentState;
    }

    if (callingCardOverlayActive && millis() >= callingCardOverlayUntilMs) {
        callingCardOverlayActive = false;
        callingCardHasPhoto = false;
        callingCardNeedsRedraw = false;
        suppressUploadThinkingAfterCallingCard = false;
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
        mapDirectionsHasMetrics = false;
        mapRouteMiles = 0.0f;
        mapRouteMinutes = 0;
        s_mapDirectionsBackdropApplied = false;
        suppressUploadThinkingAfterMap = false;
        idleAnim.hasPrevFrame = false;
        if (!timeScreenActive && !faceAnimActive && !callingCardOverlayActive) {
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
        if (!timeScreenActive && !mapOverlayActive && !callingCardOverlayActive
            && currentState == STATE_IDLE) {
            lastIdleEyesUpdate = 0;
        }
    }

    // --- RTC SERIAL TICKER ---
    if (millis() - lastRtcPrintTime > 1000) {
        lastRtcPrintTime = millis();
        static unsigned long lastRtcBadLogMs = 0;
        DateTime now = rtc.now();
        if (isPlausibleRtc(now)) {
            Serial.printf(
                "[RTC] %04d/%02d/%02d %02d:%02d:%02d\n",
                now.year(),
                now.month(),
                now.day(),
                now.hour(),
                now.minute(),
                now.second()
            );
        } else if (millis() - lastRtcBadLogMs > 5000) {
            lastRtcBadLogMs = millis();
            Serial.println(
                "[RTC] I2C read failed or bogus time (touch+RTC share Wire — check flex/cable, power)"
            );
        }
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

    if (hubWebSocketEnabled && currentState != STATE_SETUP) {
        wsLock();
        webSocket.loop();
        wsUnlock();
    }

    if (
        currentState == STATE_IDLE &&
        !timeScreenActive &&
        !mapOverlayActive &&
        !callingCardOverlayActive &&
        !faceAnimActive &&
        !wakeUpAnimActive
    ) {
        uint32_t timeoutMs = sleepTimeoutSec * 1000UL;
        if (timeoutMs < 30000UL) {
            timeoutMs = 30000UL;
        }
        unsigned long nowMs = millis();
        if (!sleepAnimActive && (nowMs - lastActivityMs >= timeoutMs)) {
            sleepAnimActive = true;
            sleepAnimHasPrevFrame = false;
            s_sleepZzzHavePrev = false;
            idleAnim.hasPrevFrame = false;
            gfx->fillScreen(BLACK);
        }
    } else if (sleepAnimActive) {
        sleepAnimActive = false;
        sleepAnimHasPrevFrame = false;
        s_sleepZzzHavePrev = false;
        wakeScanHasPrevFrame = false;
        wakeScanPrevBarX = 0;
        wakeScanPrevBarW = 0;
        wakeScanPrevEyeW = 0;
        wakeScanPrevSlitY = 0;
        wakeScanPrevSlitH = 0;
    }

    if (hubWebSocketEnabled && isWsConnected && currentState == STATE_IDLE) {
        if (pendingEnrollmentCapture) {
            if (captureAndSendJpegPacket(0x07)) {
                pendingEnrollmentCapture = false;
                lastPresenceScanMs = millis();
                syncEspCameraPowerState();
            }
        } else if (
            presenceScanEnabled &&
            !timeScreenActive &&
            !mapOverlayActive &&
            !callingCardOverlayActive &&
            !faceAnimActive
        ) {
            uint32_t intervalMs = presenceScanIntervalSec * 1000UL;
            if (intervalMs < 3000UL) {
                intervalMs = 3000UL;
            }
            unsigned long nowMs = millis();
            if (nowMs - lastPresenceScanMs >= intervalMs) {
                lastPresenceScanMs = nowMs;
                captureAndSendJpegPacket(0x06);
            }
        }
    }

    // ========================================================
    //      Native CHSC6X Touch & Gesture Implementation
    // ========================================================
    bool currentTouchState = digitalRead(TOUCH_INT);
    bool gestureReady = false;
    
    // 1. Read Data on Interrupt Pulse (Active LOW)
    if (currentTouchState == LOW) {
        size_t got = Wire.requestFrom((uint8_t)CHSC6X_I2C_ID, (size_t)5);
        if (got == 5 && Wire.available() == 5) {
            uint8_t points = Wire.read(); 
            Wire.read(); // Event ID
            int tempX = Wire.read(); 
            Wire.read(); // Reserved ID
            int tempY = Wire.read(); 
            
            // Map raw CHSC6X to GC9A01 rotation-3 framebuffer (same axes as gfx->draw*).
            // Rotation: (tempX,tempY) -> (tempY, 239-tempX). Then mirror Y so on-screen
            // "up" matches drawable coords; without this, SETTINGS rows (BT / CLR WIFI)
            // read misaligned and bottom taps read as gaps / "outside".
            int mappedX = tempY;
            int mappedY = 239 - tempX;
            mappedY = 219 - mappedY;
            if (mappedY < 0) {
                mappedY = 0;
            } else if (mappedY > 239) {
                mappedY = 239;
            }
            tempX = mappedX;
            tempY = mappedY;
            
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
        markActivity("touch");
        
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

                    // Bluetooth Wi‑Fi setup
                    if (tapY >= kBleBtnY && tapY <= (kBleBtnY + kBleBtnH)
                             && tapX >= kBleBtnX && tapX <= (kBleBtnX + kBleBtnW)) {
                        Serial.printf("[BT] BT SETUP button pressed (tap %d,%d)\n", tapX, tapY);
                        enterBleProvisioningFromSettings();
                    }
                    // Erase saved Wi‑Fi only (restart → BLE setup or reprovision)
                    else if (tapY >= kWifiResetBtnY && tapY <= (kWifiResetBtnY + kWifiResetBtnH)
                             && tapX >= kWifiResetBtnX && tapX <= (kWifiResetBtnX + kWifiResetBtnW)) {
                        Serial.printf("[WiFi] CLR WIFI button pressed (tap %d,%d)\n", tapX, tapY);
                        clearWifiCredentialsAndRestart();
                    }
                    // Tap outside controls → exit settings
                    else {
                        currentState = STATE_IDLE;
                        showIdleEyes();
                    }
                }
            }
        }
        // Evaluate SWIPE
        else if (absX > minSwipeDist || absY > minSwipeDist) {
            if (absX > absY) {
                if (deltaX < 0) {
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
                if (deltaY < 0) {
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
        suppressUploadThinkingAfterMap = false;
        suppressUploadThinkingAfterCallingCard = false;
        if (callingCardOverlayActive) {
            callingCardNeedsRedraw = true;
            lastIdleEyesUpdate = 0;
        } else if (mapOverlayActive) {
            lastIdleEyesUpdate = 0;
        } else if (faceAnimActive) {
            // Keep the active face animation fully in control of the frame.
            lastIdleEyesUpdate = 0;
        } else {
            showIdleEyes();
        }
    }

    if (callingCardOverlayActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        if (callingCardNeedsRedraw) {
            drawCallingCardOverlay();
            callingCardNeedsRedraw = false;
        }
    } else if (mapOverlayActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        if (mapHasImage && mapNeedsRedraw && s_mapRgb565 != nullptr) {
            drawMapOverlayFrame();
            mapNeedsRedraw = false;
        }
    } else if (faceAnimActive && (currentState == STATE_IDLE || currentState == STATE_UPLOADING) && !timeScreenActive) {
        updateFaceEmotionAnimation();
    } else if (currentState == STATE_UPLOADING) {
        if (!suppressUploadThinkingAfterMap && !suppressUploadThinkingAfterCallingCard) {
            updateUploadingThinkingAnimation();
        } else if (!timeScreenActive) {
            updateIdleEyesAnimation();
        }
    } else if (currentState == STATE_SETTINGS) {
        if (settingsScreenNeedsRedraw) {
            drawSettingsScreen();
            settingsScreenNeedsRedraw = false;
        }
    } else if (wakeUpAnimActive && currentState == STATE_IDLE && !timeScreenActive) {
        updateWakeUpAnimation();
    } else if (sleepAnimActive && currentState == STATE_IDLE && !timeScreenActive) {
        updateSleepEyesAnimation();
    } else if (currentState == STATE_IDLE && !timeScreenActive) {
        updateIdleEyesAnimation();
    }
}