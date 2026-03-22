# Pixel

Pixel is a desktop companion robot based on the **Seeed XIAO ESP32S3 Sense** and the **XIAO 1.28″ round display**. It captures microphone audio and (optionally) camera frames, shows animated eyes and UI on the round TFT, and streams a turn to the OmniBot backend for **Gemini** responses over Wi‑Fi.

Firmware lives in this folder as a **PlatformIO** project (`platformio.ini` + `src/main.cpp`).

## Hardware

| Component | Part | Notes |
|-----------|------|--------|
| MCU | Seeed XIAO ESP32S3 Sense | PSRAM required for audio + camera pipelines |
| Display | 1.28″ round TFT (GC9A01, 240×240) | SPI |
| Touch | CST816S class controller (I2C) | Gestures processed in firmware |
| RTC | PCF8563 (battery-backed) | I2C; used for clock screen |
| Microphone | Onboard PDM MEMS (Sense) | I2S in firmware |
| Camera | OV2640 (Sense) | DVP pins as in `main.cpp` |

## Build notes (memory)

`platformio.ini` sets **OPI PSRAM** and related flags. Do not remove these if you want simultaneous audio buffering and JPEG capture:

- `board_build.arduino.memory_type = qio_opi`
- `-DBOARD_HAS_PSRAM` and `-mfix-esp32-psram-cache-issue`

## Features

- **Animated eyes** — idle, recording, uploading (“thinking”), and emotion overlays
- **Voice + optional vision** — short tap-driven capture sessions; JPEG frames sent only when vision is enabled (matches backend “Vision” / `use_vision`)
- **Gemini** — backend builds WAV and optional MP4 from frames, runs multimodal chat, returns text to the device
- **On-device settings** — swipe-down screen to toggle vision; syncs to backend over the same WebSocket
- **Clock screen** — swipe to show RTC-oriented time display (timezone rule pushed from backend when connected)
- **Weather + face tools** — backend can send `show_weather` and `face_animation` JSON so Pixel shows icons, temperature, short captions, and **map** overlays (JPEG via `0x04`)

## First-time Wi‑Fi (BLE)

1. Flash firmware with your PC’s LAN IP and port in `src/main.cpp`:

   ```cpp
   const char* backend_ip = "192.168.x.x";
   const int backend_port = 8000;
   ```

2. Boot Pixel. In **SETUP** mode it advertises BLE as **`Pixel`** (see `BLE_SERVICE_NAME` and UUIDs in `main.cpp`).

3. On the PC, run the OmniBot backend and open the dashboard **Setup** flow: scan BLE, pick Pixel, send SSID/password. Pixel stores credentials and joins Wi‑Fi.

4. After provisioning, Pixel connects to `ws://<backend_ip>:<backend_port>/ws/stream`.

## Touch interaction

Gestures are **short taps** and **swipes** (see thresholds in `main.cpp`: ~20 px drift / 500 ms for tap, 30 px for swipe).

| Gesture | When | Action |
|---------|------|--------|
| **Tap** | Idle | Start recording (audio + optional JPEG frames) |
| **Tap** | Recording | Stop capture, upload turn (`0x03`), show processing animation |
| **Swipe left** | Idle | Open **clock / time** screen |
| **Swipe right** | Time screen open | Close time screen, return to idle eyes |
| **Swipe down** | Idle | Open **on-device settings** (vision toggle) |
| **Swipe up** | Settings | Return to idle eyes |
| **Swipe up** | Time screen | Close time screen |
| **Tap** | Settings | Hit **vision** control to toggle capture; tap outside to exit |

Long **press-and-hold** is not the primary model; recording is started/stopped with **quick taps**.

## WebSocket transport

### Binary frames (device → server)

| Prefix | Payload | Meaning |
|--------|---------|---------|
| `0x01` | Raw PCM | 16-bit little-endian, mono, **16 kHz** |
| `0x02` | JPEG | One frame; backend can assemble ~10 fps MP4 when vision is on |
| `0x03` | (empty) | **End of turn** — process buffers with Gemini |
| `0x04` | JPEG | **Map snapshot** — hub screenshot of the Maps contextual widget (240×240 target); shown while `face_animation` **map** overlay is active |

### JSON (server → device)

Examples sent by the backend:

- `runtime_vision` — `{ "type": "runtime_vision", "enabled": true|false }`
- `runtime_timezone` — `{ "type": "runtime_timezone", "timezone_rule": "..." }`
- `show_weather` — weather overlay duration and condition/temperature
- `face_animation` — speaking / happy / mad / weather / **map** (map mode uses empty `words`; hub may follow with binary `0x04` JPEG)
- `gemini_first_token` — optional UI hint that the model started streaming
- Success/error replies — e.g. `{ "status": "success", "reply": "..." }`

### JSON (device → server)

- `vision_changed` — when the user toggles vision on the settings screen so `bot_settings.json` stays in sync

## Pin reference (display / touch / mic)

| Signal | Pin |
|--------|-----|
| TFT SCK | D8 |
| TFT MOSI | D10 |
| TFT MISO | D9 |
| TFT CS | D1 |
| TFT DC | D3 |
| TFT Backlight | D6 |
| Touch INT | D7 |
| Touch SDA / SCL | D4 / D5 |
| I2S mic WS / SD | GPIO 42 / 41 |

Camera DVP pins are defined under **Camera Pins** in `main.cpp` for the Sense module.

## Libraries (PlatformIO)

From `platformio.ini`:

- `esp32-camera`
- `moononournation/GFX Library for Arduino`
- `bblanchon/ArduinoJson`
- `links2004/WebSockets`
- `adafruit/RTClib`
- `bitbank2/JPEGDEC` (decode hub map JPEGs)

ESP32 BLE and Wi‑Fi come from the Arduino-ESP32 core.

## Related docs

- **OmniBot hub** (backend + dashboard): [`../../README.md`](../../README.md)
