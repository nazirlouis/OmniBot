# OmniBot dashboard

This folder is the **web UI** for OmniBot: React + Vite. In development it talks to the FastAPI hub on port **8000** via the Vite dev proxy.

## Quick setup

From the **repository root** (recommended):

1. Run `.\scripts\install.ps1` (Windows) or `./scripts/install.sh` (macOS/Linux) once.
2. Run `.\scripts\start.ps1` or `./scripts/start.sh` — this starts the backend and `npm run dev` here.

Then open **[http://127.0.0.1:5173](http://127.0.0.1:5173)**.

## Using the app

- **First visit:** Paste your Gemini API key if prompted.
- **Sidebar:** Pick a **bot**, open **Hub settings**, or **Add New Bot** for Bluetooth Wi‑Fi setup.
- **Main area:** **Intelligence Feed** (chat and live activity) or **Setup** / **Settings** depending on what you chose.

See the [root README](../../README.md) for full usage.

## Development without the repo scripts

1. Start the backend: `cd ../backend`, activate the venv, `python app.py`.
2. In this folder: `npm ci` (or `npm install`), then `npm run dev`.

Same URL: **http://127.0.0.1:5173**.

## Production build

The Docker image runs `npm run build` and serves the static files from the backend. Locally:

```bash
npm run build
```

Output is in `dist/`. The backend serves it when `OMNIBOT_STATIC_ROOT` points at that folder (see root README and `Dockerfile`).
