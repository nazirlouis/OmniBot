# Pixel

**Pixel** is a desktop companion robot built on the **Seeed XIAO ESP32S3 Sense** and the **XIAO 1.28″ round display**. It streams microphone (and optional camera) data to the **OmniBot hub** over Wi‑Fi so **Gemini** can respond; the round screen shows eyes, status, and replies.

Firmware is a **PlatformIO** project in this folder (`platformio.ini`, `src/main.cpp`).

---

## Quick setup

1. **Run the OmniBot hub** on a PC on the same LAN as Pixel ([main README](../../README.md): install → start, or Docker).
2. **Set the hub address in firmware** — In `src/main.cpp`, set `backend_ip` to your PC’s **LAN IP** (not `127.0.0.1`) and `backend_port` to the hub port (`8000` for local dev; **8080** on the host if you use the default Docker mapping). **Rebuild and upload** after any change.
3. **Open the project in PlatformIO**, build, and flash the board.
4. **Provision Wi‑Fi** — On first boot (or with no saved Wi‑Fi), the display shows **BLE SETUP**. On the PC, open the dashboard → **Add New Bot**, scan Bluetooth, select **Pixel**, and send your SSID and password.

When Wi‑Fi works and the hub is reachable, Pixel connects with a WebSocket; the dashboard **Intelligence Feed** shows the bot as connected.

---

## Using Pixel day to day

### Voice and vision (touch)

- From the **idle** face, **tap once** to **start** recording (mic + optional camera if **VIDEO** is on).
- **Tap again** to **stop** and send the turn to the hub. Pixel shows a short “thinking” state, then the reply.
- **Vision** on the device: **swipe down** from idle → **SETTINGS** → **VIDEO: ON/OFF**. When the hub is connected, this stays in line with **Pixel bot** settings in the web UI.

If the hub is **offline** when you finish a recording, that turn is **not** uploaded; wait until the bot shows as connected again, then try another turn.

### On-device settings (swipe down from idle)

| Control | What it does |
|--------|----------------|
| **VIDEO: ON/OFF** | Include JPEG frames in each turn when ON. |
| **BT SETUP** | Disconnects from the hub, turns **Wi‑Fi off**, and advertises **BLE** so you can send a **new** SSID/password from the dashboard (**Add New Bot**). |
| **CLR WIFI** | Erases saved SSID/password and **reboots**. Next boot uses BLE setup if nothing is stored. |

**Swipe up** closes the settings screen.

### Clock screen

**Swipe left** from idle opens the **time** screen. **Swipe right** or **up** to leave. The hub can sync timezone from **Hub / application** settings when connected.

### Gestures (short taps and swipes)

| Gesture | When | Action |
|---------|------|--------|
| **Tap** | Idle | Start recording |
| **Tap** | Recording | Stop and send turn |
| **Swipe left** | Idle | Open clock screen |
| **Swipe right** | Clock | Close clock |
| **Swipe down** | Idle | Open settings |
| **Swipe up** | Settings or clock | Close / return to idle |

---

## Wi‑Fi: first connect and recovery

1. Flash firmware with the correct `backend_ip` / `backend_port`.
2. Boot with no saved credentials → **BLE SETUP**; device advertises as **Pixel**.
3. On the PC: **Add New Bot** → choose Pixel → enter Wi‑Fi credentials.

**If Wi‑Fi fails repeatedly:** the firmware retries associations, then after several failed boots can fall back to **BLE SETUP** or clear stored Wi‑Fi so you can reprovision without re-flashing. See **BT SETUP** and **CLR WIFI** above for manual changes.

**Change Wi‑Fi while Pixel already works:** **SETTINGS** → **BT SETUP**, then on the PC use **Add New Bot** to send the new network.

**Hub was stopped:** Pixel reconnects periodically over Wi‑Fi; start the hub again and wait—you usually do **not** need to reboot the board.

**You changed PC IP or port:** update `backend_ip` / `backend_port` in `src/main.cpp`, rebuild, and flash.

**Firewall:** allow inbound TCP to the hub port from Pixel’s IP on the LAN.

---

## Hardware

| Component | Part | Notes |
|-----------|------|--------|
| MCU | Seeed XIAO ESP32S3 Sense | PSRAM used for audio + camera |
| Display | 1.28″ round TFT (GC9A01, 240×240) | SPI |
| Touch | CST816S (I2C) | Gestures in firmware |
| RTC | PCF8563 | Clock screen |
| Microphone | Onboard PDM MEMS | I2S |
| Camera | OV2640 (Sense) | DVP pins in `main.cpp` |

**Build:** `platformio.ini` enables **OPI PSRAM** and related flags—do not remove them if you want audio and JPEG capture together.

**Pins:** Display, touch, mic, and camera pins are defined in `src/main.cpp` (see **Camera Pins** and nearby tables).

---

## Libraries

Declared in `platformio.ini` (e.g. `esp32-camera`, WebSockets, ArduinoJson, display stack, RTClib, JPEG decode). ESP32 BLE and Wi‑Fi come from the Arduino-ESP32 core.

---

## Related docs

- **OmniBot hub + dashboard:** [`../../README.md`](../../README.md)
