# Contributing

Thanks for your interest in Omnibot.

## Development setup

1. **Backend** — Python 3.11+ recommended (3.13 supported; see `requirements.txt` notes for `fastembed`).

   ```bash
   cd app/backend
   python -m venv .venv
   # Windows: .venv\Scripts\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env   # then add keys
   python app.py
   ```

2. **Frontend** — Node.js 20+:

   ```bash
   cd app/frontend
   npm install
   npm run dev
   ```

   The Vite dev server proxies `/api`, `/setup`, `/ping`, and `/ws` to `http://localhost:8000` so the UI can use the same origin as the backend.

3. **Firmware** — See [`bots/Pixel/README.md`](bots/Pixel/README.md) and PlatformIO.

## Pull requests

- Keep changes focused on one concern.
- Match existing style (formatting, naming, component patterns).
- If you change behavior that operators rely on (env vars, API routes, file paths), update the main `README.md`.

## CI

GitHub Actions runs `python -m compileall` on the backend and `npm run build` on the frontend. Ensure both pass before opening a PR.
