# 🔷 Prism

An open-source platform for building and managing ESP32-based robots connected to your PC.

## Structure

```
Prism/
├── app/           ← Central Brain Application
│   ├── backend/   ← Python (FastAPI) server with Gemini AI
│   └── frontend/  ← React dashboard (Vite)
│
└── bots/          ← ESP32 Robot Firmware
    └── pixel/     ← First bot: Pixel (Seeed XIAO ESP32-S3)
```

## Quick Start

### Brain App (Backend)
```bash
cd app/backend
conda activate desktop-bot
python app.py
```

### Brain App (Frontend)
```bash
cd app/frontend
npm install
npm run dev
```

### Bot Firmware (Pixel)
Open `bots/pixel/` in PlatformIO and upload to your ESP32.

## Bots
| Name | Board | Description |
|------|-------|-------------|
| **Pixel** | Seeed XIAO ESP32-S3 | Desktop companion with camera, mic, and round display |
