# OmniBot

Run the **hub** (API + dashboard) on your PC and connect **ESP32** robots such as **Pixel** over Wi‑Fi. You chat with **Google Gemini** from the browser, provision Wi‑Fi over Bluetooth, and tune hub and bot behavior from the UI.

Licensed under the [MIT License](LICENSE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

---

## Prerequisites

- **Python 3.10+** and **Node.js** (LTS recommended)
- A **Google AI (Gemini) API key** from [Google AI Studio](https://aistudio.google.com/)
- **Bluetooth** on the PC if you will use **Setup** to provision real hardware over BLE
- **Wi‑Fi list during setup:** SSID scan works on **Windows** (`netsh`), **Linux** with NetworkManager (`nmcli`), or **macOS** (`airport` when available). Otherwise choose **Join Other Network** and type the SSID manually

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

The backend runs in a **second** PowerShell window. The current window runs Vite and usually opens the UI.

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
- **Gear on a bot card** — Opens **Settings** with the **Pixel bot** tab selected for that device (model, instructions, vision, etc.).
- **Hub settings** — API keys, timezone, and optional location (postal + country) for local/geo answers when you use those features.
- **Add New Bot** — Opens **Setup**: scan for a device over **Bluetooth**, pick a Wi‑Fi network, and send credentials so the bot can join your LAN and reach the hub.

### Dashboard (Intelligence Feed)

After you select a bot, the main view shows **live activity**: connection status, streamed Gemini replies, logs, and optional previews when a bot is connected. Use the **text box** to send messages to Gemini through the hub (works even without hardware once the key is configured).

### Settings

Two tabs:

| Tab | Purpose |
|-----|--------|
| **Pixel bot** | Per-bot options: model, system instructions, vision, and related behavior for the selected bot. |
| **Hub / application** | Hub-wide secrets and preferences (Gemini key, timezone, optional Maps-related settings). |

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

Extra environment variables (Nominatim user-agent, Maps keys, data directory, debug) are listed in [`app/backend/.env.example`](app/backend/.env.example). Values set in the environment override file-based settings at runtime where applicable.

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
