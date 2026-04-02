# OmniBot

A **hub + dashboard** for ESP32-based AI robots that talk to **Google Gemini** over Wi‑Fi. Run the backend and web UI on your PC, connect a bot (e.g. **Pixel**), and use the browser to chat, watch the feed, provision Wi‑Fi over Bluetooth, and tune behavior.

Licensed under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## What you can do

- **Talk to Gemini from the dashboard** — Send text commands, see streamed replies, and follow connection status on the main **Dashboard**.
- **Pair a Pixel bot** — Use **Setup** to scan for the device over **Bluetooth**, pick a Wi‑Fi network, and push credentials so the bot can reach your hub on the LAN.
- **Watch the pipeline** — Live logs, streamed AI text, and optional audio/video previews when a bot is connected.
- **Tune the bot** — Open **Pixel bot settings** (from the Pixel card) for model, system instructions, and vision. Use **Hub settings** for API keys, timezone, and optional Maps-related location (postal + country) when you want local/geo answers.
- **Run without hardware** — Configure the hub and use text chat from the UI; connect a physical bot when you are ready.
- **Deploy with Docker** — Single-container run with the built UI and API on one port (see [Docker](#docker) below).

## Prerequisites

- **Python 3** and **pip**
- **Node.js** (for the local dev dashboard)
- **Google AI API key** for Gemini ([Google AI Studio](https://aistudio.google.com/))
- **Bluetooth** on the PC (for provisioning real hardware over BLE)
- **Wi‑Fi scan (optional):** automatic SSID lists work on **Windows** (`netsh`), **Linux** with NetworkManager (`nmcli`), or **macOS** (`airport` when available). Otherwise use **Join Other Network** and type the SSID manually.

## Quick start

1. **Clone** the repo and open a terminal at the **repository root**.
2. **Install** dependencies once:
   - **Windows (PowerShell):**  
     `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` may be required the first time. Then:
     ```powershell
     .\scripts\install.ps1
     ```
   - **macOS / Linux:**
     ```bash
     chmod +x scripts/install.sh scripts/start.sh
     ./scripts/install.sh
     ```
3. **Start** the hub and dashboard:
   - **Windows:** `.\scripts\start.ps1` — backend opens in a **second** window; Vite runs in the current window and (by default) opens **http://127.0.0.1:5173**. To skip the browser: `$env:OMNIBOT_NO_BROWSER="1"; .\scripts\start.ps1`
   - **macOS / Linux:** `./scripts/start.sh` — same URL unless **`OMNIBOT_NO_BROWSER=1`**. **Ctrl+C** stops the dev server; stop the backend process separately if needed.
4. **First visit** — You do **not** need a `.env` file to begin. On first load, paste your **Gemini API key** on the welcome screen; it is saved under the hub data directory. (Optional: set `GEMINI_API_KEY` in `app/backend/.env` instead — see [`app/backend/.env.example`](app/backend/.env.example).)

**Local dashboard:** **http://127.0.0.1:5173** — Vite proxies API and WebSocket paths to the backend on port **8000**.

### Manual start (without scripts)

**Backend**

```bash
cd app/backend
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**Frontend**

```bash
cd app/frontend
npm install
npm run dev
```

Then open **http://127.0.0.1:5173**. For production-style single-port serving (built UI + API), use **Docker** below or set `OMNIBOT_STATIC_ROOT` as in `app/backend/app.py`.

### Pixel firmware

Open `bots/Pixel` in **PlatformIO**, set `backend_ip` / `backend_port` in `src/main.cpp`, build, and upload. Full hardware notes: [`bots/Pixel/README.md`](bots/Pixel/README.md).

## Docker

Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine (Linux).

From the repository root:

```bash
docker compose up --build
```

Open **http://localhost:8080** (host **8080** → container **8000**). Paste your Gemini key on the welcome screen if you did not set `GEMINI_API_KEY`.

- **Data:** A named volume keeps hub JSON (`bot_settings.json`, `hub_secrets.json`, etc.) across restarts. To wipe hub data: `docker compose down -v` (destructive).
- **Secrets:** Prefer environment variables (e.g. in `.env` next to `docker-compose.yml`) over committing keys.
- **Ports:** Edit the left side of `ports` in [`docker-compose.yml`](docker-compose.yml) if **8080** is in use. Point Pixel’s firmware at your **host LAN IP** and the mapped port (not `localhost` from the device).
- **BLE from Docker:** Bluetooth/Wi‑Fi setup from inside desktop containers is often limited; use the **host** install for provisioning if the UI cannot see adapters, then return to Docker for day-to-day use if you prefer.

Details: [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml).

## Optional configuration

Advanced environment variables (Nominatim user-agent, Maps API keys, debug flags, data directory) are documented in [`app/backend/.env.example`](app/backend/.env.example). Hub secrets can also be set from **Hub settings** in the UI; environment variables override file-based values at runtime.

## Repository layout

```
OmniBot/
├── app/
│   ├── backend/          # FastAPI hub (Gemini, provisioning, WebSockets)
│   └── frontend/         # React + Vite dashboard
├── scripts/              # install.ps1 / install.sh, start.ps1 / start.sh
├── Dockerfile
├── docker-compose.yml
└── bots/
    └── Pixel/            # Seeed XIAO ESP32S3 Sense (PlatformIO)
```

## For developers

The hub exposes REST routes under `/api` and `/setup`, WebSockets for bot streams (`/ws/stream`) and the dashboard monitor (`/ws/monitor`), and serves the built UI when configured. Internals (conversation memory, Search vs Maps tooling, semantic routing, binary frame types) are implemented in `app/backend/app.py` and dependencies listed in `app/backend/requirements.txt`.

| Bot | Board | Doc |
|-----|-------|-----|
| **Pixel** | Seeed XIAO ESP32S3 Sense + round display | [`bots/Pixel/README.md`](bots/Pixel/README.md) |

## License

[MIT](LICENSE). Contributions welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
