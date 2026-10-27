# OmniBot

Run the **hub** (API + dashboard) on your PC and connect **ESP32** robots such as **Pixel** over Wi‑Fi. You chat with **Google Gemini** from the browser (including **Gemini Live** for voice and optional video), provision Wi‑Fi over Bluetooth, and tune hub and bot behavior from the UI. Each bot gets an **OpenClaw-style persona**: markdown files on disk (soul, identity, memory, tools, heartbeat rules) that the model can update through declared tools, plus optional **heartbeat maintenance** that merges daily logs into long-term memory.

Licensed under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

---

## Prerequisites

- **Python 3.10+** and **Node.js** (LTS recommended)
- A **Google AI (Gemini) API key** from [Google AI Studio](https://aistudio.google.com/)
- **Bluetooth** on the PC if you will use **Setup** to provision real hardware over BLE
- **Wi‑Fi list during setup:** SSID scan works on **Windows** (`netsh`), **Linux** with NetworkManager (`nmcli`), or **macOS** (`airport` when available). Otherwise choose **Join Other Network** and type the SSID manually
- **LINK TO CAD -** https://cad.onshape.com/documents/36ef3639f0adcbd215c2da2e/w/06b991eb4c1f6f28b2fd677f/e/884c5e41b396d185bfcf0efe
---

## Quick setup

### 1. Download the repository

```bash
git clone https://github.com/nazirlouis/OmniBot.git
cd OmniBot
```

### 2. Install (once)

From the **repository root**:

**Windows (PowerShell)** — if scripts are blocked the first time:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

```powershell
.\scripts\install.ps1
```

**macOS / Linux**

```bash
chmod +x scripts/install.sh scripts/start.sh
./scripts/install.sh
```

This creates `app/backend/.venv`, installs Python dependencies, and runs `npm ci` in `app/frontend`.

### 3. Start the hub and dashboard

**Windows**

```powershell
.\scripts\start.ps1
```

Backend and Vite run in the **same** PowerShell window (logs may interleave). **Ctrl+C** stops the dev server and then the script stops the backend.

**macOS / Linux**

```bash
./scripts/start.sh
```

Backend and Vite run in the same terminal; **Ctrl+C** stops both.

- **Dashboard:** [http://127.0.0.1:5173](http://127.0.0.1:5173) (dev; Vite proxies API/WebSocket to the backend)
- **API:** [http://127.0.0.1:8000](http://127.0.0.1:8000)

To **skip auto-opening the browser**, set `OMNIBOT_NO_BROWSER` before `start.ps1` / `start.sh`:

**PowerShell**

```powershell
$env:OMNIBOT_NO_BROWSER = "1"
```

**macOS / Linux**

```bash
export OMNIBOT_NO_BROWSER=1
```

### 4. First launch

You do **not** need a `.env` file to begin. When the UI loads, paste your **Gemini API key** on the welcome screen; it is stored in the hub data directory.

Optional: set `GEMINI_API_KEY` in [`app/backend/.env`](app/backend/.env.example) instead of using the welcome screen.

---

## Using the application

### Sidebar

- **Bots** — Each connected or configured bot appears as a card. **Click a card** to open the **Dashboard** (Intelligence Feed) for that bot. **Offline** / **Ready** / **Processing** reflect connection and activity.
- **Gear on a bot card** — Opens **Settings** with the **Pixel bot** tab for that device (model, vision, wake word, persona markdown, face profiles, heartbeat, hub TTS, and more).
- **Hub settings** — API keys, timezone, optional location (postal + country), **browser live voice** (mic/speakers vs Pixel), and **Give me a soul** (persona bootstrap ritual).
- **Add New Bot** — Opens **Setup**: scan for a device over **Bluetooth**, pick a Wi‑Fi network, and send credentials so the bot can join your LAN and reach the hub.

### Dashboard (Intelligence Feed)

After you select a bot, the main view shows **live activity**: connection status, streamed Gemini replies, logs, live transcription when using voice, and an optional **~1 fps JPEG preview** of frames sent to the model when vision is on. Use the **text box** to send messages through the hub (works without hardware once the key is configured).

### Persona framework (per bot)

The hub composes the model’s **system instruction** from hub rules plus markdown files stored under **`DATA_DIR/persona/<device_id>/`** (default: `app/backend/persona/<device_id>/` when `OMNIBOT_DATA_DIR` is unset). That directory is **gitignored**; committed **templates** live in [`app/backend/persona_defaults/`](app/backend/persona_defaults/) (see [`app/backend/persona_defaults/README.md`](app/backend/persona_defaults/README.md)).

| File | Role |
|------|------|
| **AGENTS.md** | High-level behavior guide (injected; edit in **Pixel bot** settings). |
| **SOUL.md** | Voice, tone, boundaries. |
| **IDENTITY.md** | Who the bot is (name, nature, emoji, etc.). |
| **USER.md** | Profile of the human. |
| **TOOLS.md** | Reference for what tools exist (context for the model). |
| **MEMORY.md** | Curated long-term memory (trimmed if very large). |
| **HEARTBEAT.md** | Instructions for periodic **heartbeat** maintenance passes. |
| **BOOTSTRAP.md** | Written only during **Give me a soul**; the model follows it once, then calls **`bootstrap_complete`** to remove it. |
| **`logs/daily/YYYY-MM-DD.md`** | Raw daily notes; the model can append with **`daily_log_append`**. |

During chat, Gemini can call tools such as **`soul_replace`**, **`memory_replace`**, **`persona_replace`** (IDENTITY / USER / HEARTBEAT), **`daily_log_append`**, and **`bootstrap_complete`**, subject to the rules injected with the persona. **Heartbeat maintenance** (optional, on an interval you set) runs a separate pass that may update **MEMORY.md** only from recent daily logs and **HEARTBEAT.md** guidance.

**Give me a soul** (under **Hub settings**): resets persona markdown to the hub templates, clears that bot’s hub chat history, wipes daily logs and heartbeat state, writes **BOOTSTRAP.md**, and starts a streamed bootstrap conversation—follow it in the Intelligence Feed for the chosen bot.

**Reset to defaults** (in **Pixel bot** settings): restores numeric/boolean bot settings and **overwrites** the persona markdown files from templates; daily logs are kept unless you use the soul flow (which clears logs for a clean ritual).

### Other hub features worth knowing

- **Gemini Live** — Voice (and optional vision) turns use the Live API when enabled (default). Set environment variable **`OMNIBOT_USE_GEMINI_LIVE=0`** to disable Live and fall back to the non-Live path. REST/chat and heartbeat avoid “live” model IDs automatically where needed.
- **Live output voice selection** — Set **`OMNIBOT_LIVE_VOICE_NAME`** to a Gemini prebuilt voice name (for example `Puck`). If unset, or set to `default`, `auto`, or `system`, the hub uses **`Umbriel`**.
- **Browser live voice** — In **Hub settings**, set voice source to **browser** to use the PC microphone and speakers (OpenWakeWord on the hub + `/ws/voice-bridge`) instead of streaming from Pixel. Pixel wake streaming is suppressed in that mode so only one path is active.
- **Hub TTS** — After a **voice** turn, the hub can speak the assistant reply on the PC using **Gemini Live** audio or optional **ElevenLabs** (voice mode selectable per bot in Pixel settings). Typed chat messages are not spoken.
- **Wake word & follow-up** — OpenWakeWord on the hub; optional custom model as **`pixel.onnx`** under [`app/backend/models/wake/`](app/backend/models/wake/README.md). **Post-reply listen** seconds control VAD-only follow-up without repeating the wake phrase (`0` = wake required every turn).
- **Presence face scan** — Optional periodic snapshots from Pixel for on-LAN face matching and greetings; enroll people under **Pixel bot** settings.
- **Thinking level** — Per-bot **Gemini 3** thinking levels (including **minimal** and **auto**) map to the API’s thinking config; see the in-UI help link if a model rejects a level.

### Settings

Two tabs:

| Tab | Purpose |
|-----|--------|
| **Pixel bot** | Per-bot model & thinking, vision, wake word, post-reply listen, hub TTS, presence scan, sleep timeout, **persona editor** (AGENTS / SOUL / IDENTITY / USER / TOOLS / MEMORY / HEARTBEAT), face profiles, heartbeat interval and enable flag. |
| **Hub / application** | Hub-wide secrets and preferences (Gemini key, timezone, optional Maps-related settings), **browser live voice** devices, **Give me a soul** (bootstrap) per bot. |

Open **Hub settings** from the sidebar, or **Pixel bot** via the gear on a bot card.

### Setup (Wi‑Fi provisioning)

Use **Add New Bot** when the device shows **BLE SETUP** (first boot or after clearing Wi‑Fi). On the PC, allow Bluetooth when prompted, pick your **Pixel** (or supported device), choose the network, and send the password. After the bot joins Wi‑Fi and connects to the hub, it appears under **Bots**.

For provisioning, prefer running the hub **on the host** (not only inside Docker) if Bluetooth is unreliable in containers.

### Docker (single URL)

With [Docker](https://www.docker.com/products/docker-desktop/), from the repo root:

```bash
docker compose up --build
```

Open **[http://localhost:8080](http://localhost:8080)** (host **8080** maps to container **8000**). Paste your Gemini key in the UI if you did not set `GEMINI_API_KEY`.

- **Persisted data:** A Docker volume stores hub JSON (`bot_settings.json`, secrets, etc.). To wipe the volume (destructive):

  ```bash
  docker compose down -v
  ```

- **Bots on the LAN:** Point firmware at your **PC’s LAN IP** and the **mapped port** (e.g. **8080**), not `127.0.0.1` from the device.

More detail: [`Dockerfile`](Dockerfile), [`docker-compose.yml`](docker-compose.yml).

### Pixel firmware (optional)

Build and flash from [`bots/Pixel`](bots/Pixel) with **PlatformIO**. Set `backend_ip` and `backend_port` in firmware to match your hub (LAN IP; port **8000** for local dev, **8080** for default Docker). Full device usage (gestures, on-screen menus, Wi‑Fi recovery): [`bots/Pixel/README.md`](bots/Pixel/README.md).

---

## Optional configuration

Extra environment variables (Nominatim user-agent, Maps keys, data directory, debug, bind host/port, static root) are listed in [`app/backend/.env.example`](app/backend/.env.example). Values set in the environment override file-based settings at runtime where applicable.

- **`OMNIBOT_DATA_DIR`** — Moves all hub JSON and the **`persona/`** tree to another directory (useful for backups or Docker volumes).
- **`OMNIBOT_USE_GEMINI_LIVE`** — Set to **`0`** to disable Gemini Live for voice/video on the hub (see **Persona framework** above).
- **`OMNIBOT_LIVE_VOICE_NAME`** — Gemini Live output voice name for hub voice turns (default **`Umbriel`**). Values `default`, `auto`, and `system` are treated as the default to avoid invalid voice selection.
- **`OMNIBOT_ELEVENLABS_DEBUG`** — Set to **`1`** to log verbose ElevenLabs WebSocket steps (chunk previews, per-message audio sizes) on the hub.
- **`OMNIBOT_ELEVENLABS_CHUNK_SCHEDULE`** — Comma-separated integers for ElevenLabs `chunk_length_schedule` (buffer thresholds in characters). Default **`50,120,160,290`** (lower first-byte latency than **`120,160,250,290`**). Example: `80,120,160,290` for a middle ground.
- **Wake tuning** — **`OMNIBOT_WAKE_WORD_MODEL`**, **`OMNIBOT_WAKE_THRESHOLD`**, **`OMNIBOT_WAKE_SILENCE_MS`** (see [`app/backend/models/wake/README.md`](app/backend/models/wake/README.md)).

---

## Manual start (no scripts)

**Backend**

```bash
cd app/backend
python -m venv .venv
```

Activate the venv, then install and run:

**Windows (PowerShell)**

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

**Windows (Command Prompt)**

```bat
.venv\Scripts\activate.bat
pip install -r requirements.txt
python app.py
```

**macOS / Linux**

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**Frontend** (separate terminal)

```bash
cd app/frontend
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173).

---

## License

[MIT](LICENSE). Contributions welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

### Waveshare round AMOLED robot

The [Waveshare ESP32-S3-Touch-AMOLED-1.43C port](bots/Waveshare143C/README.md) adds ESP-IDF firmware with microphone/speaker audio, animated eyes-only display, an authenticated `/ws/board` transport, Pi-side ONNX wake detection, BOOT fallback/cancellation, and three-second spoken follow-ups. Its optional environment variables use the `OMNIBOT_BOARD_` prefix; existing Pixel/dashboard LAN behavior is preserved unless `OMNIBOT_BOARD_LOCAL_ADMIN=1` explicitly restricts it.

Gemini conversations can use the `voice_control` tool to list, inspect, change, or reset the requesting robot's prebuilt speaking voice. Changes persist as `live_voice_name` and take effect on the next reply, preserving the intelligence model and conversation history.
