# OmniBot

Open-source stack for ESP32-based AI robots that talk to Google Gemini over Wi-Fi, with a FastAPI "brain" and a React control room on your PC.

Licensed under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Repository layout

```
OmniBot/
├── app/
│   ├── backend/          # FastAPI + Google GenAI (Gemini), BLE provisioning, WebSockets
│   └── frontend/         # React + Vite dashboard
├── Dockerfile            # Multi-stage: build UI + run hub
├── docker-compose.yml
└── bots/
    └── Pixel/            # First bot: Seeed XIAO ESP32S3 Sense + round display (PlatformIO)
```

## What it does

- **Bots** open a WebSocket to the backend and stream **PCM audio** (16-bit, 16 kHz) and optional **JPEG video** frames. A stop packet triggers Gemini to answer using the buffered turn.
- **Gemini** uses **per-device in-memory conversation history** (list of prior turns). Each request builds a **fresh** `GenerateContentConfig` with **Google Search** *or* **Maps** grounding (never both-Gemini returns `400` if combined), plus `face_animation`. On **Maps** turns the model is instructed to use `face_animation` with **`map`** (empty words); the hub then fetches a static Google Maps image and sends a **JPEG** to Pixel over the bot WebSocket. **[semantic-router](https://github.com/aurelio-labs/semantic-router)** (FastEmbed local embeddings, no PyTorch) classifies the **current user turn** into local/geo intent (traffic, directions, "near me", etc.) -> **Maps** when allowed, otherwise **Search**. The model still receives **full history + new user content**.
- **Dashboard** connects to a separate monitor WebSocket for live logs, streamed AI text, audio/video previews, and tool telemetry.
- **BLE provisioning** sends Wi‑Fi credentials from the PC to Pixel (no credentials baked into firmware beyond first-hop backend IP/port).

## Prerequisites

- **Python 3** with `pip`
- **Node.js** (for the frontend)
- **Google AI API key** for Gemini ([Google AI Studio](https://aistudio.google.com/))
- **Bluetooth** on the PC (for provisioning real hardware)
- **Wi‑Fi scan (optional):** automatic listing uses **Windows** (`netsh`), **Linux** with NetworkManager (`nmcli`), or **macOS** (`airport` when available). If scanning is unavailable, use **Join Other Network** and type the SSID manually.

## Configuration

Create `app/backend/.env` from [`app/backend/.env.example`](app/backend/.env.example):

```env
GEMINI_API_KEY=your_key_here
# Required for OpenStreetMap Nominatim (geocoding postal + country into lat/lng for Maps grounding).
# Use a stable app name + contact URL or email per https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_USER_AGENT=OmnibotHub/1.0 (your-contact-or-repo-url)
# Maps JavaScript API key: frontend contextual widget + fallback map image key.
# GOOGLE_MAPS_JS_API_KEY=your_maps_js_key
# Optional dedicated Static Maps key for backend map snapshots (if omitted, backend falls back to GOOGLE_MAPS_JS_API_KEY).
# GOOGLE_MAPS_STATIC_API_KEY=your_maps_static_key
# Optional: print Maps/location diagnostics to the backend terminal (postal, country, lat/lng, tools, history length).
OMNIBOT_MAPS_DEBUG=1
# Optional: also log semantic route decisions (routing text, prefer_maps, effective builtin tool). Enabled automatically when OMNIBOT_MAPS_DEBUG is on.
OMNIBOT_ROUTE_DEBUG=1
# Optional: directory for bot_settings.json, hub_secrets.json, hub_app_settings.json, and .env loading (default: folder containing app.py).
# OMNIBOT_DATA_DIR=/path/to/data
```

**Hub secrets:** API keys can also be set from the dashboard under **Hub settings** (stored in `hub_secrets.json` under the data directory). **Environment variables always override** file-based secrets, which is what you want for Docker and production injectors.

**Pixel (per device id, e.g. `default_bot`):** model, system instruction, and vision on/off are stored in `bot_settings.json`. **Hub-wide:** API keys go in `hub_secrets.json`; timezone and Maps grounding location go in `hub_app_settings.json`. The UI splits these into **Pixel bot** vs **Hub / application** settings.

**Maps grounding:** Turning this on in **Settings** geocodes **postal/ZIP + country** on each save via **Nominatim** (no Maps API key needed for geocoding). The country dropdown uses **`i18n-iso-countries`**. Coordinates are passed as `toolConfig.retrievalConfig` for the [Google Maps grounding tool](https://ai.google.dev/gemini-api/docs/maps-grounding). **Routing:** each turn is classified with **semantic-router** (`semantic-router[fastembed]`; first run downloads a FastEmbed model). If the prompt looks like local/geo/navigation intent **and** Maps grounding is **on** **and** valid **lat/lng** are stored, that turn uses **only** `google_maps` + `face_animation`. Otherwise it uses **Google Search** + `face_animation`. If Maps is **off**, every turn uses Search. If the router prefers Maps but coordinates are missing, that turn falls back to Search.

**Important:** Gemini returns `400` if both `google_maps` and `google_search` appear in the same request; the backend always attaches at most one of them.

**Conversation memory:** Multi-turn context is kept **in RAM** on the backend (`history_by_device`), keyed by `device_id`. It is **cleared** when you save bot settings or call `POST /api/text-command/reset/{device_id}`; it is **lost** if the backend process restarts. Saving settings always applies the latest model, system instruction, and tools on the **next** turn without needing a separate “session” object.

## Quick start

You do **not** need to create a `.env` file by hand. Start the backend and frontend, open the dashboard, and paste your **Gemini API key** on the welcome screen — it is saved to `hub_secrets.json` under the hub data directory. (Optional: set `GEMINI_API_KEY` in `.env` instead if you prefer environment variables.)

### Backend

```bash
cd app/backend
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Optional: `copy .env.example .env` and set `GEMINI_API_KEY` there if you do not want to use the UI.

When Maps grounding is enabled, the hub uses the Google Static Maps API to fetch a map image and forwards a normalized JPEG to Pixel. Backend map fetch uses `GOOGLE_MAPS_STATIC_API_KEY` when set, otherwise it falls back to `GOOGLE_MAPS_JS_API_KEY`.

The server listens on **http://0.0.0.0:8000** (all interfaces). If you use Conda, activate your environment instead of `venv` before `pip install` / `python app.py`.

### Frontend

```bash
cd app/frontend
npm install
npm run dev
```

The dev server **proxies** `/api`, `/setup`, `/ping`, and `/ws` to the backend on port 8000 (see `app/frontend/vite.config.js`). The client resolves the hub URL via `app/frontend/src/hubOrigin.js`: optional **`VITE_HUB_API_ORIGIN`**, otherwise **`window.location.origin`** (works when the built UI is served from the same host as the API, e.g. Docker or a reverse proxy), with a localhost fallback. The maps loader can still set **`VITE_GOOGLE_MAPS_JS_API_KEY`** for the Maps script.

### Pixel firmware

Open `bots/Pixel` in **PlatformIO**, set `backend_ip` / `backend_port` in `src/main.cpp`, build, and upload. Details: [`bots/Pixel/README.md`](bots/Pixel/README.md).

### Docker (one container)

**Docker** packages the app into an **image** (a filesystem snapshot + run instructions). A **container** is a running instance of that image. **Volumes** persist data on your machine when the container is recreated (`bot_settings.json`, `hub_secrets.json`, etc. under `OMNIBOT_DATA_DIR`).

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine (Linux).

From the repository root:

```bash
docker compose up --build
```

Then open **http://localhost:8080** (host port `8080` maps to port `8000` in the container). The built React UI is served by FastAPI from the same origin; paste your Gemini API key on the welcome screen if you did not set `GEMINI_API_KEY`.

- **Data:** A named volume `omnibot_data` stores `/data` inside the container (JSON settings and secrets). It survives `docker compose down`. To remove the volume and wipe hub data: `docker compose down -v` (destructive).
- **Optional env:** Set `GEMINI_API_KEY` in a `.env` file next to `docker-compose.yml` or export it before `docker compose up`; the compose file passes it through. Other variables match [`app/backend/.env.example`](app/backend/.env.example).
- **Ports:** Change the left side of `ports` in [`docker-compose.yml`](docker-compose.yml) (e.g. `9080:8000`) if `8080` is busy. Point Pixel’s `backend_ip` in firmware to your **host machine’s LAN IP** (not `localhost` from the device) and the mapped port (e.g. `8080`).
- **Health:** Compose uses `GET /ping` for health checks (first startup can take a while while **semantic-router** downloads its embedding model).
- **BLE / Wi‑Fi setup:** Bluetooth from the host is **often not available inside desktop Docker** the same way as on your PC. If BLE provisioning fails from the containerized UI, run the **frontend + backend on the host** for setup, or use advanced Linux device passthrough. Core chat and settings still work in Docker.

Files: [`Dockerfile`](Dockerfile) (multi-stage: build Vite, then Python), [`.dockerignore`](.dockerignore), [`docker-compose.yml`](docker-compose.yml).

## Architecture

```
[Pixel]  ──WebSocket /ws/stream──▶  [FastAPI backend]  ──▶  [Gemini + Google Search + optional Maps + tools]
                                           │
                                           ├── bot_settings.json, hub_secrets.json, hub_app_settings.json
                                           │
[React dashboard]  ◀──WebSocket /ws/monitor──┘
        │
        └── REST: setup, settings, text commands, runtime toggles
```

## Frontend (dashboard)

Modes from the sidebar:

- **Dashboard** — intelligence feed, typed commands to Gemini (`POST /api/text-command`), connection status.
- **Setup** — BLE scan (`GET /setup/scan`), Wi‑Fi list (`GET /setup/wifi-networks`), provision (`POST /setup/provision`).
- **Pixel bot settings** (gear on the Pixel card) — `GET`/`POST /api/settings/{device_id}` (default `default_bot`): model, system instructions, and vision only.
- **Hub settings** (sidebar) — `GET`/`POST /api/hub/settings` for API keys; `GET`/`POST /api/hub/app-settings` for timezone and Maps location; **`GET /api/hub/status`** reports whether Gemini is configured.

## Docker (preview)

Run the backend and frontend in containers or behind one reverse proxy; mount a volume for **`OMNIBOT_DATA_DIR`** so `bot_settings.json`, `hub_secrets.json`, and `hub_app_settings.json` persist. Pass secrets with **environment variables** rather than committing files. A multi-stage image can build the Vite app and serve static files while the FastAPI process handles `/api` and WebSockets.

## Backend API summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/ping` | Health check; also broadcasts an `esp32_connected`-style event to monitor clients |
| `GET` | `/setup/scan` | BLE scan; lists devices whose name contains `Pixel` |
| `GET` | `/setup/wifi-networks` | Wi‑Fi SSIDs (`networks`) plus optional `message` (scan hints / errors) |
| `GET` | `/api/hub/status` | `{ gemini_configured, data_dir }` |
| `GET` | `/api/hub/settings` | Masked hub secret status (never returns raw keys) |
| `POST` | `/api/hub/settings` | Update `hub_secrets.json` fields; env vars still win at runtime |
| `GET` | `/api/hub/app-settings` | Hub-wide timezone and Maps grounding / address fields |
| `POST` | `/api/hub/app-settings` | Save clock & location; geocodes when Maps grounding is on; clears all in-memory chat histories |
| `POST` | `/setup/provision` | Write JSON `{"ssid","password"}` to Pixel’s BLE Wi‑Fi characteristic |
| `GET` | `/api/settings/{device_id}` | Read merged view: Pixel fields from `bot_settings.json` plus hub timezone/Maps from `hub_app_settings.json` |
| `POST` | `/api/settings/{device_id}` | Save **Pixel-only** fields (model, system instruction, vision); pushes runtime vision to the bot if connected |
| `POST` | `/api/settings/{device_id}/reset` | Reset Pixel-only fields to defaults; push vision; does **not** change hub clock/Maps |
| `POST` | `/api/text-command` | Body: `{ "message", "device_id" }` — one Gemini turn (appends to in-memory history), stream to UI, reply to bot WebSocket |
| `POST` | `/api/text-command/reset/{device_id}` | Clear in-memory multi-turn history for that device |
| `GET` / `POST` | `/api/runtime/{device_id}/vision` | Get/set whether JPEG frames are assembled into video for the model |
| `GET` / `POST` | `/api/runtime/{device_id}/timezone` | Get/set POSIX TZ string for the bot’s clock sync |
| `GET` | `/api/hub-config` | Returns frontend-safe hub config values (currently Maps JS key) |
| WebSocket | `/ws/stream` | Binary AV protocol from ESP32; JSON control/response both ways |
| WebSocket | `/ws/monitor` | JSON events for the React dashboard |

### Bot binary protocol (`/ws/stream`)

Device -> backend binary messages: **1 byte type** + payload.

| Byte | Meaning |
|------|---------|
| `0x01` | PCM audio chunk (16-bit LE, mono, 16 kHz) |
| `0x02` | JPEG video frame |
| `0x03` | End of turn — assemble WAV (+ optional MP4), call Gemini, reply with JSON `{"status","reply"}` or error |

Backend -> device binary messages:

| Byte | Meaning |
|------|---------|
| `0x04` | JPEG map image for map overlay on Pixel |

### Monitor WebSocket message types (representative)

Examples the UI handles: `esp32_connected`, `esp32_disconnected`, `processing_started`, `audio_captured`, `video_captured`, `ai_response_stream_*`, `error`, `tool_call`, `vision_changed`, `timezone_changed`.

## Bots

| Bot | Board | Doc |
|-----|-------|-----|
| **Pixel** | Seeed XIAO ESP32S3 Sense + round display | [`bots/Pixel/README.md`](bots/Pixel/README.md) |

## Python dependencies

See `app/backend/requirements.txt` (FastAPI, uvicorn, `google-genai`, `semantic-router[fastembed]`, explicit **`fastembed`** on Python 3.13+ because that extra’s dependency marker skips 3.13, Bleak, OpenCV/imageio for video assembly, etc.). The router uses **FastEmbed** / ONNX (no `transformers`); **Pillow** is pinned below 12 for compatibility with current **fastembed**. Expect a one-time embedding model download on first classification.

Frontend also depends on **`i18n-iso-countries`** for the settings country list (`npm install` in `app/frontend`).

## License

[MIT](LICENSE). Contributions welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
