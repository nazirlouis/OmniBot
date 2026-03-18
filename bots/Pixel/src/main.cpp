#include <Arduino.h>
#include <WiFi.h>
#include <Arduino_GFX_Library.h>
#include "esp_camera.h"
#include <driver/i2s.h>
#include <Preferences.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <ArduinoJson.h>

// ==========================================
//        USER CONFIGURATION
// ==========================================
// ADD YOUR COMPUTER's LOCAL IP ADDRESS HERE
const char* backend_ip = "10.0.0.180"; // <--- CHANGE THIS!
const int backend_port = 8000;

#define BLE_SERVICE_NAME "Pixel"
#define SERVICE_UUID           "4fafc201-1fb5-459e-8fcc-c5c9c331914b" // Required but unused by our bleak scanner
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
#define TOUCH_INT  D7 

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

#define SAMPLE_RATE 16000
#define MAX_RECORD_SECONDS 10 
#define FLASH_RECORD_SIZE (SAMPLE_RATE * MAX_RECORD_SECONDS * 2) 

uint8_t *rec_buffer = NULL; 
size_t rec_len = 0;

// State Machine
enum RobotState { STATE_IDLE, STATE_RECORDING, STATE_READY, STATE_SETUP };
volatile RobotState currentState = STATE_SETUP;

// Animation Memory
int last_bar_heights[6] = {0}; 
int last_scan_x = 0;           
int scan_x = 0;
int scan_dir = 5;
unsigned long nextBlinkTime = 0;

// FPS Counter Globals
int fps_count = 0;
unsigned long last_fps_time = 0;

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
        .dma_buf_count = 8,
        .dma_buf_len = 64,
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

void createWavHeader(uint8_t *header, int wavSize) {
    header[0] = 'R'; header[1] = 'I'; header[2] = 'F'; header[3] = 'F';
    unsigned int fileSize = wavSize + 36;
    header[4] = (byte)(fileSize & 0xFF); header[5] = (byte)((fileSize >> 8) & 0xFF);
    header[6] = (byte)((fileSize >> 16) & 0xFF); header[7] = (byte)((fileSize >> 24) & 0xFF);
    header[8] = 'W'; header[9] = 'A'; header[10] = 'V'; header[11] = 'E';
    header[12] = 'f'; header[13] = 'm'; header[14] = 't'; header[15] = ' ';
    header[16] = 16; header[17] = 0; header[18] = 0; header[19] = 0;
    header[20] = 1; header[21] = 0; header[22] = 1; header[23] = 0;
    header[24] = 0x80; header[25] = 0x3E; header[26] = 0; header[27] = 0;
    header[28] = 0x00; header[29] = 0x7D; header[30] = 0; header[31] = 0;
    header[32] = 2; header[33] = 0; header[34] = 16; header[35] = 0;
    header[36] = 'd'; header[37] = 'a'; header[38] = 't'; header[39] = 'a';
    header[40] = (byte)(wavSize & 0xFF); header[41] = (byte)((wavSize >> 8) & 0xFF);
    header[42] = (byte)((wavSize >> 16) & 0xFF); header[43] = (byte)((wavSize >> 24) & 0xFF);
}

// ==========================================
//   AUDIO & VISUAL PROCESSING
// ==========================================
void processAudio() {
    size_t bytesRead = 0;
    
    if (rec_len < FLASH_RECORD_SIZE) {
        i2s_read(I2S_NUM_0, rec_buffer + 44 + rec_len, 512, &bytesRead, 0);
        if (bytesRead == 0) return; 

        rec_len += bytesRead;
        int16_t* samples = (int16_t*)(rec_buffer + 44 + rec_len - bytesRead);
        for(int i=0; i < bytesRead/2; i++) samples[i] <<= 2; 

        long sum = 0;
        for(int i=0; i < bytesRead/2; i++) sum += abs(samples[i]);
        int avg = sum / (bytesRead/2);
        
        int center_y = 130;
        int max_h = 90;
        int bar_x_positions[6] = {40, 60, 80, 130, 150, 170};
        int current_h[6];

        for(int i=0; i<6; i++) {
            current_h[i] = map(avg, 0, 1500, 5, 80) + random(-10, 15);
            if(current_h[i] < 5) current_h[i] = 5;
            if(current_h[i] > max_h) current_h[i] = max_h;
        }

        for(int i=0; i<6; i++) {
            int x = bar_x_positions[i];
            int last = last_bar_heights[i];
            int curr = current_h[i];

            if (curr != last) {
                if (curr > last) {
                    gfx->fillRect(x, center_y - (curr/2), 15, (curr/2) - (last/2), CYAN); 
                    gfx->fillRect(x, center_y + (last/2), 15, (curr/2) - (last/2), CYAN);
                } else {
                    gfx->fillRect(x, center_y - (last/2), 15, (last/2) - (curr/2), BLACK);
                    gfx->fillRect(x, center_y + (curr/2), 15, (last/2) - (curr/2), BLACK);
                }
                last_bar_heights[i] = curr;
            }
        }
    }
}

void processThinking() {
    gfx->fillRoundRect(45 + last_scan_x, 90, 8, 80, 4, BLACK);
    gfx->fillRoundRect(135 + last_scan_x, 90, 8, 80, 4, BLACK);

    scan_x += scan_dir;
    if (scan_x > 55 || scan_x < 5) scan_dir = -scan_dir;

    gfx->fillRoundRect(45 + scan_x, 90, 8, 80, 4, MAGENTA);
    gfx->fillRoundRect(135 + scan_x, 90, 8, 80, 4, MAGENTA);

    last_scan_x = scan_x;
    delay(20); 
}

void renderEyes(int dx, bool blink) {
    gfx->fillScreen(BLACK);
    if (blink) {
        gfx->fillRoundRect(40, 145, 70, 12, 6, 0x07FF);
        gfx->fillRoundRect(130, 145, 70, 12, 6, 0x07FF);
    } else {
        gfx->fillRoundRect(40 + dx, 85, 70, 90, 35, 0x07FF);
        gfx->fillRoundRect(130 + dx, 85, 70, 90, 35, 0x07FF);
    }
}

// ==========================================
//      BLE PROVISIONING CALLBACKS
// ==========================================
class ProvisionCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String rxValue = pCharacteristic->getValue().c_str();

      if (rxValue.length() > 0) {
        Serial.println("Reeceived Provisioning Payload!");
        Serial.println(rxValue);
        
        StaticJsonDocument<256> doc;
        DeserializationError error = deserializeJson(doc, rxValue);
        
        if (!error) {
            String new_ssid = doc["ssid"].as<String>();
            String new_pass = doc["password"].as<String>();
            
            if(new_ssid.length() > 0) {
                Serial.println("Saving Wi-Fi Credentials to NVS Flash...");
                preferences.begin("wifi_creds", false); 
                preferences.putString("ssid", new_ssid);
                preferences.putString("password", new_pass);
                preferences.end();
                
                Serial.println("Saved! Restarting ESP32...");
                
                // Draw a quick success icon to the screen
                gfx->fillScreen(GREEN);
                delay(1000);
                ESP.restart(); // Reboot chip so it connects via the new setupWiFi()
            }
        } else {
            Serial.println("Failed to parse JSON.");
        }
      }
    }
};

void startBLEProvisioning() {
    Serial.println("\n--- ENTERING BLE SETUP MODE ---");
    currentState = STATE_SETUP;
    
    // Draw a Bluetooth symbol icon or text to let user know it's advertising
    gfx->fillScreen(BLUE);
    gfx->setTextColor(WHITE); gfx->setTextSize(2); 
    gfx->setCursor(35, 110); gfx->print("BLE Setup Mode");
    
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
    pAdvertising->setMinPreferred(0x06);  // helps with iPhone connections issue
    pAdvertising->setMinPreferred(0x12);
    BLEDevice::startAdvertising();
    
    Serial.println("Advertising DesktopBot_Setup. Waiting for React App...");
}

// ==========================================
//         WIFI PREFERENCES ENGINE
// ==========================================
void setupWiFi() {
    preferences.begin("wifi_creds", true); // Open NVS in Read-Only mode
    String saved_ssid = preferences.getString("ssid", "");
    String saved_pass = preferences.getString("password", "");
    preferences.end();
    
    if (saved_ssid == "") {
        Serial.println("No WiFi credentials found in flash memory.");
        startBLEProvisioning();
        return;
    }
    
    Serial.printf("Found saved credentials! Attempting to connect to: %s\n", saved_ssid.c_str());
    WiFi.begin(saved_ssid.c_str(), saved_pass.c_str());
    
    int timeoutCounter = 0;
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        timeoutCounter++;
        
        // If it takes more than 15 seconds to connect, the password must have changed.
        // Erase memory and start BLE Provisioning again.
        if (timeoutCounter > 30) { 
            Serial.println("\nFailed to connect. Network may be down or password changed.");
            Serial.println("Erasing old credentials and starting BLE mode...");
            preferences.begin("wifi_creds", false);
            preferences.remove("ssid");
            preferences.remove("password");
            preferences.end();
            startBLEProvisioning();
            return;
        }
    }
    
    Serial.println("\nWiFi Connected! IP Address: ");
    Serial.println(WiFi.localIP());
    currentState = STATE_IDLE; // Enter Brain mode
}


// ==========================================
//         FASTAPI HTTP CLIENT
// ==========================================
void pingBackend() {
    WiFiClient client;
    if (client.connect(backend_ip, backend_port)) {
        client.print("GET /ping HTTP/1.1\r\n");
        client.print("Host: "); client.print(backend_ip); client.print("\r\n\r\n");
        client.stop();
    }
}

void sendDataToFastAPI() {
    Serial.println("\n--- INITIATING UPLOAD TO BRAIN ---");
    
    // 1. Take the Picture
    Serial.println("Capturing Image...");
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("Camera capture failed!");
        return;
    }

    // 2. Connect to Server
    WiFiClient client;
    if (client.connect(backend_ip, backend_port)) {
        Serial.println("Connected to FastAPI!");

        // 3. Build Multipart Form Data Headers
        String boundary = "----ESP32Boundary123456";
        String contentType = "multipart/form-data; boundary=" + boundary;

        String audioHeader = "--" + boundary + "\r\n";
        audioHeader += "Content-Disposition: form-data; name=\"audio\"; filename=\"audio.wav\"\r\n";
        audioHeader += "Content-Type: audio/wav\r\n\r\n";

        String imageHeader = "\r\n--" + boundary + "\r\n";
        imageHeader += "Content-Disposition: form-data; name=\"image\"; filename=\"image.jpg\"\r\n";
        imageHeader += "Content-Type: image/jpeg\r\n\r\n";

        String footer = "\r\n--" + boundary + "--\r\n";

        // Calculate total payload size
        size_t wav_total_len = rec_len + 44;
        size_t contentLength = audioHeader.length() + wav_total_len + imageHeader.length() + fb->len + footer.length();

        // 4. Send HTTP POST Headers
        client.print("POST /process HTTP/1.1\r\n");
        client.print("Host: "); client.print(backend_ip); client.print("\r\n");
        client.print("Content-Type: "); client.print(contentType); client.print("\r\n");
        client.print("Content-Length: "); client.print(contentLength); client.print("\r\n");
        client.print("\r\n"); // End of headers

        // 5. Send Audio Chunked (Saves Memory)
        Serial.println("Uploading Audio...");
        client.print(audioHeader);
        uint8_t *wav_ptr = rec_buffer;
        size_t remaining = wav_total_len;
        while(remaining > 0) {
            size_t chunk = (remaining > 1024) ? 1024 : remaining;
            client.write(wav_ptr, chunk);
            wav_ptr += chunk;
            remaining -= chunk;
        }

        // 6. Send Image Chunked
        Serial.println("Uploading Image...");
        client.print(imageHeader);
        uint8_t *img_ptr = fb->buf;
        remaining = fb->len;
        while(remaining > 0) {
            size_t chunk = (remaining > 1024) ? 1024 : remaining;
            client.write(img_ptr, chunk);
            img_ptr += chunk;
            remaining -= chunk;
        }

        // 7. Send Footer
        client.print(footer);

        // 8. Wait for Server Response while running "Thinking" animation
        Serial.println("Waiting for Gemini response...");
        while (client.connected() && !client.available()) {
            processThinking(); // Keep robot alive!
        }

        // 9. Read the Response
        Serial.println("\n=== RESPONSE FROM SERVER ===");
        while (client.available()) {
            String line = client.readStringUntil('\n');
            Serial.println(line);
        }
        Serial.println("============================");
        
        client.stop();
    } else {
        Serial.println("Failed to connect to backend!");
    }

    esp_camera_fb_return(fb); // Free image memory
}

// ==========================================
//                 MAIN
// ==========================================
void setup() {
    Serial.begin(115200);
    
    // Init Display
    pinMode(TFT_BL, OUTPUT); digitalWrite(TFT_BL, HIGH);
    gfx->begin(80000000); gfx->fillScreen(BLACK);
    
    // Allocate Memory & Check
    Serial.print("Allocating PSRAM...");
    rec_buffer = (uint8_t *)ps_malloc(FLASH_RECORD_SIZE + 44);
    if (rec_buffer == NULL) {
        Serial.println("FAILED!");
        gfx->setTextColor(RED); gfx->setTextSize(2); 
        gfx->setCursor(10, 100); gfx->print("MEM ERROR!");
        while(1);
    }
    Serial.println("OK!");

    pinMode(TOUCH_INT, INPUT_PULLUP);
    setupMicrophone();
    
    // Camera
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0; config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = 15; config.pin_d1 = 17; config.pin_d2 = 18; config.pin_d3 = 16;
    config.pin_d4 = 14; config.pin_d5 = 12; config.pin_d6 = 11; config.pin_d7 = 48;
    config.pin_xclk = 10; config.pin_pclk = 13; config.pin_vsync = 38; config.pin_href = 47;
    config.pin_sccb_sda = 40; config.pin_sccb_scl = 39; config.pin_pwdn = -1; config.pin_reset = -1;
    config.xclk_freq_hz = 20000000; config.frame_size = FRAMESIZE_VGA; 
    config.pixel_format = PIXFORMAT_JPEG; config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_PSRAM; config.jpeg_quality = 12; config.fb_count = 2;
    esp_camera_init(&config);

    // Call our new Wi-Fi engine!
    setupWiFi();
}

void loop() {
    // If in BLE Setup Mode, halt loop here indefinitely so it doesn't process audio/camera.
    if(currentState == STATE_SETUP) {
        delay(100);
        return; 
    }

    fps_count++;
    if (millis() - last_fps_time >= 1000) {
        // Send a ping to our Backend Dashboard
        pingBackend();
        fps_count = 0;
        last_fps_time = millis();
    }

    if (digitalRead(TOUCH_INT) == LOW) {
        delay(150); 
        if (digitalRead(TOUCH_INT) == LOW) {
            if (currentState == STATE_IDLE) {
                // START RECORDING
                currentState = STATE_RECORDING;
                rec_len = 0; 
                gfx->fillScreen(BLACK);
            } 
            else if (currentState == STATE_RECORDING) {
                // STOP RECORDING & TRIGGER SEND
                currentState = STATE_READY;
                createWavHeader(rec_buffer, rec_len);
                gfx->fillScreen(BLACK);
                gfx->drawRoundRect(40, 85, 70, 90, 35, 0x3333); 
                gfx->drawRoundRect(130, 85, 70, 90, 35, 0x3333); 
            }
            while(digitalRead(TOUCH_INT) == LOW);
        }
    }

    if (currentState == STATE_RECORDING) {
        processAudio(); 
    } 
    else if (currentState == STATE_READY) {
        // Send data to python script
        sendDataToFastAPI(); 
        
        // When finished, go back to normal eyes automatically
        currentState = STATE_IDLE;
        nextBlinkTime = millis() + 500;
        gfx->fillScreen(BLACK);
    }
    else if (currentState == STATE_IDLE) {
        if (millis() > nextBlinkTime) {
            renderEyes(0, true);
            delay(150);
            renderEyes(0, false);
            nextBlinkTime = millis() + random(2000, 5000);
        }
    }
}