# OmniBot

Open-source stack for ESP32-based AI robots that talk to Google Gemini over Wi‑Fi, with a FastAPI “brain” and a React control room on your PC.

## Repository layout

```
OmniBot/
├── app/
│   ├── backend/          # FastAPI + Google GenAI (Gemini), BLE provisioning, WebSockets
│   └── frontend/         # React + Vite dashboard
└── bots/
    └── Pixel/            # First bot: Seeed XIAO ESP32S3 Sense + round display (PlatformIO)
```

## What it does

- **Bots** open a WebSocket to the backend and stream **PCM audio** (16-bit, 16 kHz) and optional **JPEG video** frames. A stop packet triggers Gemini to answer using the buffered turn.
- **Gemini** uses **per-device in-memory conversation history** (list of prior turns). Each request builds a **fresh** `GenerateContentConfig` with **Google Search** *or* **Maps** grounding (never both—Gemini returns `400` if combined), plus `face_animation`. On **Maps** turns the model is instructed to use `face_animation` with **`map`** (empty words); after the turn the hub renders the contextual map in headless Chromium and sends a **JPEG** to Pixel over the bot WebSocket. **[semantic-router](https://github.com/aurelio-labs/semantic-router)** (FastEmbed local embeddings, no PyTorch) classifies the **current user turn** into local/geo intent (traffic, directions, “near me”, etc.) → **Maps** when allowed, otherwise **Search**. The model still receives **full history + new user content**.
- **Dashboard** connects to a separate monitor WebSocket for live logs, streamed AI text, audio/video previews, and tool telemetry.
- **BLE provisioning** sends Wi‑Fi credentials from the PC to Pixel (no credentials baked into firmware beyond first-hop backend IP/port).

## Prerequisites

- **Python 3** with `pip`
- **Node.js** (for the frontend)
- **Google AI API key** for Gemini ([Google AI Studio](https://aistudio.google.com/))
- **Bluetooth** on the PC (for provisioning real hardware)
- **Windows** for automatic Wi‑Fi SSID scan in the setup UI (`netsh`). On other OSes, enter SSID manually or extend `setup_wifi_networks` in `app/backend/app.py`.

## Configuration

Create `app/backend/.env`:

```env
GEMINI_API_KEY=your_key_here
# Required for OpenStreetMap Nominatim (geocoding postal + country into lat/lng for Maps grounding).
# Use a stable app name + contact URL or email per https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_USER_AGENT=OmnibotHub/1.0 (your-contact-or-repo-url)
# Maps JavaScript API key: dashboard contextual widget + headless screenshot for Pixel (optional if Maps grounding off).
# GOOGLE_MAPS_JS_API_KEY=your_maps_js_key
# Optional: print Maps/location diagnostics to the backend terminal (postal, country, lat/lng, tools, history length).
OMNIBOT_MAPS_DEBUG=1
# Optional: also log semantic route decisions (routing text, prefer_maps, effective builtin tool). Enabled automatically when OMNIBOT_MAPS_DEBUG is on.
OMNIBOT_ROUTE_DEBUG=1
# Optional: similarity threshold for the local/geo route (default 0.58 for FastEmbed bge-small). See semantic_pixel_router.py.
# OMNIBOT_ROUTE_THRESHOLD=0.58
# Optional: override FastEmbed model cache directory (default: app/backend/models/fastembed_cache).
# OMNIBOT_FASTEMBED_CACHE=C:/path/to/cache
```

Per-bot settings (model, system instruction, timezone, vision on/off, optional Maps grounding + location) are stored in `app/backend/bot_settings.json` when you save from the UI or when the device reports vision changes.

**Maps grounding:** Turning this on in **Settings** geocodes **postal/ZIP + country** on each save via **Nominatim** (no Maps API key). The country dropdown uses **`i18n-iso-countries`**. Coordinates are passed as `toolConfig.retrievalConfig` for the [Google Maps grounding tool](https://ai.google.dev/gemini-api/docs/maps-grounding). **Routing:** Each turn is classified with **semantic-router** (`semantic-router[fastembed]` — first run downloads a small FastEmbed model, cached under `app/backend/models/fastembed_cache` unless you set `OMNIBOT_FASTEMBED_CACHE`). If the prompt looks like local/geo/navigation intent **and** Maps grounding is **on** **and** valid **lat/lng** are stored, that turn uses **only** `google_maps` + `face_animation`. Otherwise the turn uses **Google Search** + `face_animation`. If Maps is **off** in settings, every turn uses Search regardless of the router. If the router prefers Maps but coordinates are missing, that turn falls back to Search.

**Important:** Gemini returns `400` if both `google_maps` and `google_search` appear in the same request; the backend always attaches at most one of them.

**Conversation memory:** Multi-turn context is kept **in RAM** on the backend (`history_by_device`), keyed by `device_id`. It is **cleared** when you save bot settings or call `POST /api/text-command/reset/{device_id}`; it is **lost** if the backend process restarts. Saving settings always applies the latest model, system instruction, and tools on the **next** turn without needing a separate “session” object.

## Quick start

### Backend

```bash
cd app/backend
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python app.py
```

The hub uses **Playwright** to capture a screenshot of the Google Maps contextual widget for Pixel when Maps grounding returns a widget context token. Chromium is installed by `python -m playwright install chromium` (once per machine). Ensure `GOOGLE_MAPS_JS_API_KEY` is set in `.env` if you use Maps grounding and want that snapshot on the bot (same key as the dashboard contextual map).

The server listens on **http://0.0.0.0:8000** (all interfaces). If you use Conda, activate your environment instead of `venv` before `pip install` / `python app.py`.

### Frontend

```bash
cd app/frontend
npm install
npm run dev
```

The UI expects the API at **http://localhost:8000** and the monitor socket at **ws://localhost:8000/ws/monitor** (see `app/frontend/src/App.jsx` and `setupService.js`). Change these if you run the backend on another host.

### Pixel firmware

Open `bots/Pixel` in **PlatformIO**, set `backend_ip` / `backend_port` in `src/main.cpp`, build, and upload. Details: [`bots/Pixel/README.md`](bots/Pixel/README.md).

## Architecture

```
[Pixel]  ──WebSocket /ws/stream──▶  [FastAPI backend]  ──▶  [Gemini + Google Search + optional Maps + tools]
                                           │
                                           ├── bot_settings.json
                                           │
[React dashboard]  ◀──WebSocket /ws/monitor──┘
        │
        └── REST: setup, settings, text commands, runtime toggles
```

## Frontend (dashboard)

Modes from the sidebar:

- **Dashboard** — intelligence feed, typed commands to Gemini (`POST /api/text-command`), connection status.
- **Setup** — BLE scan (`GET /setup/scan`), Wi‑Fi list (`GET /setup/wifi-networks`), provision (`POST /setup/provision`).
- **Settings** — load/save `GET`/`POST /api/settings/{device_id}` (default target id in UI: `default_bot`), including optional Maps grounding and Nominatim-backed location fields.

## Backend API summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/ping` | Health check; also broadcasts an `esp32_connected`-style event to monitor clients |
| `GET` | `/setup/scan` | BLE scan; lists devices whose name contains `Pixel` |
| `GET` | `/setup/wifi-networks` | Wi‑Fi SSIDs (Windows `netsh`) |
| `POST` | `/setup/provision` | Write JSON `{"ssid","password"}` to Pixel’s BLE Wi‑Fi characteristic |
| `GET` | `/api/settings/{device_id}` | Read model, system instruction, timezone rule, vision flag, Maps/location fields |
| `POST` | `/api/settings/{device_id}` | Save those fields; geocodes via Nominatim when Maps grounding is on; response includes `maps_geocode: { ok, error? }`; pushes runtime vision/timezone to the bot if connected |
| `POST` | `/api/text-command` | Body: `{ "message", "device_id" }` — one Gemini turn (appends to in-memory history), stream to UI, reply to bot WebSocket |
| `POST` | `/api/text-command/reset/{device_id}` | Clear in-memory multi-turn history for that device |
| `GET` / `POST` | `/api/runtime/{device_id}/vision` | Get/set whether JPEG frames are assembled into video for the model |
| `GET` / `POST` | `/api/runtime/{device_id}/timezone` | Get/set POSIX TZ string for the bot’s clock sync |
| WebSocket | `/ws/stream` | Binary AV protocol from ESP32; JSON control/response both ways |
| WebSocket | `/ws/monitor` | JSON events for the React dashboard |

### Bot binary protocol (`/ws/stream`)

Each binary message: **1 byte type** + payload.

| Byte | Meaning |
|------|---------|
| `0x01` | PCM audio chunk (16-bit LE, mono, 16 kHz) |
| `0x02` | JPEG video frame |
| `0x03` | End of turn — assemble WAV (+ optional MP4), call Gemini, reply with JSON `{"status","reply"}` or error |

### Monitor WebSocket message types (representative)

Examples the UI handles: `esp32_connected`, `esp32_disconnected`, `processing_started`, `audio_captured`, `video_captured`, `ai_response_stream_*`, `error`, `tool_call`, `vision_changed`, `timezone_changed`.

## Bots

| Bot | Board | Doc |
|-----|-------|-----|
| **Pixel** | Seeed XIAO ESP32S3 Sense + round display | [`bots/Pixel/README.md`](bots/Pixel/README.md) |

## Python dependencies

See `app/backend/requirements.txt` (FastAPI, uvicorn, `google-genai`, `semantic-router[fastembed]`, explicit **`fastembed`** on Python 3.13+ because that extra’s dependency marker skips 3.13, Bleak, OpenCV/imageio for video assembly, etc.). The router uses **FastEmbed** / ONNX (no `transformers`); **Pillow** is pinned below 12 for compatibility with current **fastembed**. Expect a one-time embedding model download on first classification.

Frontend also depends on **`i18n-iso-countries`** for the settings country list (`npm install` in `app/frontend`).

## License / contributing

Add your preferred license and contribution notes here if you publish the repo publicly.
