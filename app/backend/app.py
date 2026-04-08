import io
import os
import socket
import uvicorn
from pathlib import Path
import tempfile
import base64
import cv2
import numpy as np
import imageio
import asyncio # Added for non-blocking operations
from collections import deque
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from google.genai import types
import json
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from wifi_scan import scan_wifi_ssids

from hub_config import (
    DEFAULT_NOMINATIM_USER_AGENT,
    KNOWN_BOTS_FILE,
    SETTINGS_FILE,
    get_genai_client,
    get_gemini_api_key,
    get_nominatim_user_agent_raw,
    hub_public_status,
    hub_settings_public_view,
    load_hub_app_settings,
    merge_hub_secrets,
    save_hub_app_settings,
)

from pydantic import BaseModel, ConfigDict, Field
from bleak import BleakScanner, BleakClient
import uuid
import time
from functools import partial

from face_matching import (
    add_reference_jpeg,
    create_profile,
    delete_profile,
    list_profiles,
    match_probe_jpeg,
)
from wake_listen import MAX_UTTERANCE_SEC, WakeListenProcessor

# ==========================================
#          LOAD SECRETS & SETUP (see hub_config: OMNIBOT_DATA_DIR, .env, hub_secrets.json)
# ==========================================

# Settings file path is resolved in hub_config (not cwd-dependent).
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_TIMEZONE_RULE = "EST5EDT,M3.2.0/2,M11.1.0/2"
DEFAULT_VISION_ENABLED = False
DEFAULT_WAKE_WORD_ENABLED = True
DEFAULT_PRESENCE_SCAN_ENABLED = False
DEFAULT_PRESENCE_SCAN_INTERVAL_SEC = 5
DEFAULT_GREETING_COOLDOWN_MINUTES = 30
DEFAULT_SYSTEM_INSTRUCTION = (
    "You control a small desktop robot named Pixel with a round face display.\n\n"
    "Animation tools:\n"
    "- Use the `show_face_animation` / `face_animation` tool for conversational or emotional states only. "
    "Pass a single argument: `animation` = speaking, happy, or mad.\n"
    "At most one animation tool should be used per turn. Do not call more than one of: "
    "`face_animation` or `show_face_animation` in the same turn.\n\n"
    "When asked about food, activities, or places near me, provide exactly ONE recommendation and do not list multiple options."
)

FACE_ANIMATION_DISPLAY_MS = 2500
MAP_DISPLAY_MS = 9000
# Legacy calling-card constants retained to keep residual helper code import-safe.
CALLING_CARD_PHOTO_W = 240
CALLING_CARD_PHOTO_H = 240
CALLING_CARD_NAME_MAX = 80
CALLING_CARD_ADDRESS_MAX = 120
CALLING_CARD_CATEGORY_MAX = 48
CALLING_CARD_JPEG_MAX_BYTES = 14 * 1024
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
# Rolling JPEG buffer from Pixel (0x02) during wake streaming — only the last ~1s before a wake
# is sent to Gemini / dashboard (10fps × 1s ≈ 10 frames; matches Pixel WAKE_VIDEO_FRAME_MS).
WAKE_VIDEO_PRE_WAKE_SECONDS = 1.0
WAKE_VIDEO_STREAM_FPS = 10
WAKE_VIDEO_FRAME_BUFFER_MAX = max(1, int(WAKE_VIDEO_PRE_WAKE_SECONDS * WAKE_VIDEO_STREAM_FPS))
# During speech (after wake, until VAD silence): bound JPEG count to max audio utterance length.
WAKE_VIDEO_UTTERANCE_MAX_FRAMES = max(1, int(MAX_UTTERANCE_SEC * WAKE_VIDEO_STREAM_FPS))


def _nominatim_user_agent_header() -> str:
    """HTTP headers must be latin-1; strip/replace non-ASCII (e.g. from .env)."""
    raw = get_nominatim_user_agent_raw() or DEFAULT_NOMINATIM_USER_AGENT
    ascii_only = raw.encode("ascii", "replace").decode("ascii").strip()
    return (ascii_only[:200] if ascii_only else None) or "OmnibotHub/1.0"


def _maps_debug_enabled() -> bool:
    return False


def _maps_debug(msg: str) -> None:
    if _maps_debug_enabled():
        print(f"[Omnibot/maps-debug] {msg}")


def _route_debug_enabled() -> bool:
    return os.getenv("OMNIBOT_ROUTE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _route_debug(msg: str) -> None:
    if _route_debug_enabled():
        print(f"[Omnibot/route-debug] {msg}")


# Event loop used to push WebSocket messages from sync Gemini tool invocations (worker threads).
_main_async_loop: Optional[asyncio.AbstractEventLoop] = None

# OpenAPI-style declarations matching Gemini function-calling docs
# (https://ai.google.dev/gemini-api/docs/function-calling — combine with Google Search).
# The executable implementations are `_make_face_animation_tool`
# (automatic function calling).
FACE_ANIMATION_FUNCTION_DECLARATION = {
    "name": "face_animation",
    "description": (
        "Animates Pixel's face on the round display for conversational or emotional states only. "
        "Pass only which face to show: speaking, happy, or mad."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "animation": {
                "type": "string",
                "enum": ["speaking", "happy", "mad"],
                "description": "Which face animation to display: speaking, happy, or mad.",
            },
        },
        "required": ["animation"],
    },
}

# Per-device, per-turn tool usage guardrails.
turn_tool_state_by_device: dict[str, dict[str, bool]] = {}
map_jpeg_sent_this_turn_by_device: dict[str, bool] = {}
map_location_override_by_device: dict[str, str] = {}
map_display_style_by_device: dict[str, str] = {}

def get_all_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading settings: {e}")
        return {}

def save_all_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        print(f"Error saving settings: {e}")


def _default_display_name_for_device_id(device_id: str) -> str:
    did = (device_id or "").strip()
    if did == "default_bot":
        return "Pixel"
    return did.replace("_", " ") or "Bot"


def load_known_bots() -> dict[str, dict[str, Any]]:
    if not os.path.exists(KNOWN_BOTS_FILE):
        return {}
    try:
        with open(KNOWN_BOTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Error loading known bots: {e}")
        return {}


def save_known_bots(data: dict[str, dict[str, Any]]) -> None:
    try:
        Path(KNOWN_BOTS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(KNOWN_BOTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving known bots: {e}")


def record_bot_seen(device_id: str) -> None:
    """Persist that a stream device has connected (sidebar / hub memory)."""
    did = (device_id or "").strip() or "default_bot"
    kb = load_known_bots()
    row = dict(kb.get(did) or {})
    row["last_seen"] = datetime.now(timezone.utc).isoformat()
    if not row.get("display_name"):
        row["display_name"] = _default_display_name_for_device_id(did)
    kb[did] = row
    save_known_bots(kb)


def list_registered_bots() -> list[dict[str, Any]]:
    """Union of bot_settings.json keys and known_bots.json (seen stream devices)."""
    settings = get_all_settings()
    kb = load_known_bots()
    ids = sorted(set(settings.keys()) | set(kb.keys()))
    out: list[dict[str, Any]] = []
    for did in ids:
        meta = kb.get(did) or {}
        display = (meta.get("display_name") or "").strip() or _default_display_name_for_device_id(did)
        online = device_stream_is_live(did)
        out.append(
            {
                "device_id": did,
                "display_name": display,
                "last_seen": meta.get("last_seen"),
                "online": online,
            }
        )
    return out


def _clamp_int(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        x = int(v)
        return max(lo, min(hi, x))
    except Exception:
        return default


def get_bot_settings(device_id: str):
    """Merge per-device Pixel fields from bot_settings.json with hub-wide clock settings."""
    settings = get_all_settings()
    bot_conf = settings.get(device_id, {})
    hub = load_hub_app_settings()
    return {
        "model": bot_conf.get("model", DEFAULT_MODEL),
        "system_instruction": bot_conf.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION),
        "vision_enabled": bool(bot_conf.get("vision_enabled", DEFAULT_VISION_ENABLED)),
        "timezone_rule": hub.get("timezone_rule", DEFAULT_TIMEZONE_RULE),
        "presence_scan_enabled": bool(bot_conf.get("presence_scan_enabled", DEFAULT_PRESENCE_SCAN_ENABLED)),
        "presence_scan_interval_sec": _clamp_int(
            bot_conf.get("presence_scan_interval_sec"),
            DEFAULT_PRESENCE_SCAN_INTERVAL_SEC,
            3,
            300,
        ),
        "greeting_cooldown_minutes": _clamp_int(
            bot_conf.get("greeting_cooldown_minutes"),
            DEFAULT_GREETING_COOLDOWN_MINUTES,
            1,
            720,
        ),
        "wake_word_enabled": bool(bot_conf.get("wake_word_enabled", DEFAULT_WAKE_WORD_ENABLED)),
    }


def _default_stored_bot_settings() -> dict:
    """Persisted row for a bot after reset-to-default (model, system prompt, vision, presence)."""
    return {
        "model": DEFAULT_MODEL,
        "system_instruction": DEFAULT_SYSTEM_INSTRUCTION,
        "vision_enabled": DEFAULT_VISION_ENABLED,
        "presence_scan_enabled": DEFAULT_PRESENCE_SCAN_ENABLED,
        "presence_scan_interval_sec": DEFAULT_PRESENCE_SCAN_INTERVAL_SEC,
        "greeting_cooldown_minutes": DEFAULT_GREETING_COOLDOWN_MINUTES,
        "wake_word_enabled": DEFAULT_WAKE_WORD_ENABLED,
    }


def _extract_search_sources_from_grounding_metadata(gm) -> tuple[list[dict], list[str]]:
    """Build link list + query list for hub UI (Google Search grounding attribution)."""
    if gm is None:
        return [], []
    chunks = getattr(gm, "grounding_chunks", None) or []
    by_uri: dict[str, dict] = {}
    for ch in chunks:
        web = getattr(ch, "web", None)
        if web is None:
            continue
        uri = getattr(web, "uri", None)
        if not uri:
            continue
        title = (getattr(web, "title", None) or "").strip() or "Web source"
        by_uri[str(uri)] = {"title": title, "uri": str(uri)}
    queries = []
    for q in (getattr(gm, "web_search_queries", None) or []):
        s = str(q).strip()
        if s:
            queries.append(s)
    return list(by_uri.values()), queries


def assemble_video(jpeg_frames: list[bytes], fps: int = 10) -> bytes:
    """Takes a list of JPEG byte arrays and assembles them into an MP4 (H.264) video in memory."""
    if not jpeg_frames:
        return b""
        
    tmp_path = os.path.join(tempfile.gettempdir(), "pixel_clip.mp4")
    
    # Write frames using imageio's ffmpeg plugin with libx264 codec 
    # This guarantees cross-browser HTML5 <video> compatibility
    with imageio.get_writer(tmp_path, fps=fps, codec='libx264', format='FFMPEG') as writer:
        for jpg_bytes in jpeg_frames:
            frame = cv2.imdecode(np.frombuffer(jpg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                # cv2 imdecode returns BGR, imageio expects RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                writer.append_data(frame_rgb)
    
    with open(tmp_path, 'rb') as f:
        video_bytes = f.read()
    os.remove(tmp_path)
    
    return video_bytes

def create_wav_header(data_size: int, sample_rate: int = 16000, num_channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Creates a 44-byte standard RIFF WAV header for raw PCM audio."""
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    chunk_size = data_size + 36

    header = bytearray()
    header.extend(b'RIFF')
    header.extend(chunk_size.to_bytes(4, byteorder='little'))
    header.extend(b'WAVE')
    header.extend(b'fmt ')
    header.extend((16).to_bytes(4, byteorder='little')) # Subchunk1Size (16 for PCM)
    header.extend((1).to_bytes(2, byteorder='little'))  # AudioFormat (1 for PCM)
    header.extend(num_channels.to_bytes(2, byteorder='little'))
    header.extend(sample_rate.to_bytes(4, byteorder='little'))
    header.extend(byte_rate.to_bytes(4, byteorder='little'))
    header.extend(block_align.to_bytes(2, byteorder='little'))
    header.extend(bits_per_sample.to_bytes(2, byteorder='little'))
    header.extend(b'data')
    header.extend(data_size.to_bytes(4, byteorder='little'))
    return bytes(header)


app = FastAPI(title="ESP32 Gemini Brain Monitor")

if not get_gemini_api_key():
    print(
        "WARNING: No Gemini API key — set GEMINI_API_KEY or configure keys in Hub settings (UI / POST /api/hub/settings)."
    )


def _google_search_builtin():
    """Built-in Google Search tool; docs use types.ToolGoogleSearch() when available."""
    ctor = getattr(types, "ToolGoogleSearch", None)
    if ctor is not None:
        return ctor()
    return types.GoogleSearch()


def _pixel_chat_generate_config(
    *,
    system_instruction,
    device_id: str,
) -> types.GenerateContentConfig:
    """Tools + server-side tool flag per Gemini 3 multi-tool docs.

    Maps functionality is removed; use Search + local function tools.
    """
    custom_declarations = [
        types.FunctionDeclaration(**FACE_ANIMATION_FUNCTION_DECLARATION),
    ]
    tools = [
        types.Tool(google_search=_google_search_builtin()),
        types.Tool(function_declarations=custom_declarations),
    ]

    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        tools=tools,
        tool_config=types.ToolConfig(include_server_side_tool_invocations=True),
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
#          WEBSOCKET MANAGER
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                print(f"Failed to send to WS: {e}")

manager = ConnectionManager()

@app.websocket("/ws/monitor")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    online_ids = sorted(
        {active_streams[ws].get("device_id", "default_bot") for ws in active_streams}
    )
    try:
        await websocket.send_text(
            json.dumps({"type": "stream_snapshot", "online_device_ids": online_ids})
        )
    except Exception as e:
        print(f"Failed to send stream snapshot to monitor: {e}")
    try:
        while True:
            # We don't expect the frontend to send us anything, but we keep the loop open
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def _detect_lan_ipv4() -> Optional[str]:
    """IPv4 of the interface used for outbound traffic — Pixel must use this to reach the hub (not 127.0.0.1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.25)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not str(ip).startswith("127."):
            return str(ip)
    except Exception:
        pass
    return None


class WifiCredentials(BaseModel):
    ssid: str
    password: str = ""
    device_address: str
    hub_ip: Optional[str] = None
    hub_port: int = 8000

class BotSettingsSchema(BaseModel):
    """Pixel-only fields stored per device_id in bot_settings.json."""

    model: str
    system_instruction: str
    vision_enabled: bool = DEFAULT_VISION_ENABLED
    wake_word_enabled: bool = DEFAULT_WAKE_WORD_ENABLED
    presence_scan_enabled: bool = DEFAULT_PRESENCE_SCAN_ENABLED
    presence_scan_interval_sec: int = Field(default=DEFAULT_PRESENCE_SCAN_INTERVAL_SEC, ge=3, le=300)
    greeting_cooldown_minutes: int = Field(default=DEFAULT_GREETING_COOLDOWN_MINUTES, ge=1, le=720)


class HubAppSettingsSchema(BaseModel):
    """Hub-wide clock settings (hub_app_settings.json)."""

    timezone_rule: str = DEFAULT_TIMEZONE_RULE

class TextCommandRequest(BaseModel):
    message: str
    device_id: str = "default_bot"

class VisionToggleRequest(BaseModel):
    enabled: bool


class TimezoneRuleRequest(BaseModel):
    timezone_rule: str


class HubSettingsUpdate(BaseModel):
    gemini_api_key: Optional[str] = None
    nominatim_user_agent: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class CreateFaceProfileBody(BaseModel):
    display_name: str = "Person"


# Use a standard BLE UUID for our custom generic characteristic
WIFI_CREDS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef0"
# Must match bots/Pixel/src/main.cpp SERVICE_UUID (provisioning service in advertisements).
OMNIBOT_BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"


def _normalize_uuid_for_compare(u: str) -> str:
    return (u or "").strip().lower().replace("-", "")


def _ble_device_advertises_omnibot_service(device) -> bool:
    """True if scan metadata lists Pixel's provisioning service (some OSes omit the local name)."""
    target = _normalize_uuid_for_compare(OMNIBOT_BLE_SERVICE_UUID)
    md = getattr(device, "metadata", None) or {}
    uuids = md.get("uuids")
    if not uuids:
        return False
    for u in uuids:
        if _normalize_uuid_for_compare(str(u)) == target:
            return True
    return False


def _ble_device_is_pixel_setup(device) -> bool:
    """Pixel in BLE provisioning: local name contains Pixel, or our service UUID appears in the advertisement."""
    name = (device.name or "").strip()
    if name and "Pixel" in name:
        return True
    return _ble_device_advertises_omnibot_service(device)


def _pixel_display_name_for_scan(device) -> str:
    n = (device.name or "").strip()
    return n if n else "Pixel"


# ==========================================
#          API ENDPOINTS (ESP32 SETUP)
# ==========================================
@app.get("/setup/scan")
async def setup_scan():
    """Scans for nearby BLE devices and returns those that might be the Desktop Bot."""
    print("BLE Scanning...")
    try:
        # Slightly longer timeout helps Windows Bluetooth stacks complete a full pass.
        devices = await BleakScanner.discover(timeout=8.0)
    except Exception as e:
        print(f"BLE scan unavailable: {e}")
        return {
            "devices": [],
            "ble_available": False,
            "message": (
                "Bluetooth scanning is not available in this environment (typical in Docker: no "
                "host Bluetooth/D-Bus). Run the OmniBot hub on your PC (not in a container) to "
                "scan for Pixel over BLE, or configure Wi‑Fi on the device manually."
            ),
        }

    results = []
    for d in devices:
        if _ble_device_is_pixel_setup(d):
            results.append({"name": _pixel_display_name_for_scan(d), "address": d.address})

    # Optional dev-only fake device when no hardware is found (set OMNIBOT_SETUP_SIMULATE_DEVICE=1)
    sim = (os.getenv("OMNIBOT_SETUP_SIMULATE_DEVICE") or "").strip().lower()
    if not results and sim in ("1", "true", "yes"):
        results.append({"name": "DesktopBot_Setup (Simulated)", "address": "00:00:00:00:00:00"})

    empty_hint = (
        "No Pixel found in Bluetooth setup mode. On the device, open SETTINGS (swipe down) and tap BT SETUP, then scan again."
    )
    return {
        "devices": results,
        "ble_available": True,
        "message": empty_hint if not results else None,
    }

@app.get("/setup/hub-endpoint")
async def setup_hub_endpoint():
    """LAN IP and port Pixel should use for the hub WebSocket (ESP32 cannot use localhost)."""
    port = 8000
    try:
        port = int(os.environ.get("OMNIBOT_PORT", "8000") or 8000)
    except ValueError:
        port = 8000
    return {"hub_ip": _detect_lan_ipv4(), "hub_port": port}


@app.post("/setup/provision")
async def setup_provision(creds: WifiCredentials):
    """Connects to the ESP32 via BLE: Wi‑Fi first, then optional hub IP/port (second write for small stacks / old firmware)."""
    wifi_payload = json.dumps({"ssid": creds.ssid, "password": creds.password}).encode("utf-8")
    hub_ip = (creds.hub_ip or "").strip()
    hp = int(creds.hub_port) if creds.hub_port else 8000
    if not (1 <= hp <= 65535):
        hp = 8000
    hub_payload = (
        json.dumps({"hub_ip": hub_ip, "hub_port": hp}).encode("utf-8") if hub_ip else None
    )
    print(f"Provisioning {creds.device_address} with SSID {creds.ssid} (hub follow-up: {bool(hub_ip)})...")
    
    # Simulate success if using the fake device
    if creds.device_address == "00:00:00:00:00:00":
        return {"status": "success", "message": "Simulated provisioning complete."}
        
    try:
        async with BleakClient(creds.device_address) as client:
            await client.write_gatt_char(WIFI_CREDS_CHAR_UUID, wifi_payload)
            if hub_payload:
                await asyncio.sleep(0.2)
                await client.write_gatt_char(WIFI_CREDS_CHAR_UUID, hub_payload)
        return {"status": "success", "message": "Credentials sent successfully."}
    except Exception as e:
        error_msg = str(e)
        print(f"BLE Provisioning Error: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/setup/wifi-networks")
async def setup_wifi_networks():
    """Scans for nearby Wi-Fi networks when the host OS supports it (Windows netsh, Linux nmcli, macOS airport)."""
    result = scan_wifi_ssids()
    if result.get("message"):
        print(f"[Omnibot/wifi-scan] {result['message']}")
    return {"networks": result["networks"], "message": result.get("message")}


# ==========================================
#          API ENDPOINTS (SETTINGS)
# ==========================================
@app.get("/api/bots")
async def api_list_bots():
    """Bots known to the hub (saved settings and/or stream connects)."""
    return {"bots": list_registered_bots()}


@app.get("/api/settings/{device_id}")
async def get_settings(device_id: str):
    """Gets the current settings for a specific bot."""
    return get_bot_settings(device_id)

@app.post("/api/settings/{device_id}")
async def update_settings(device_id: str, new_settings: BotSettingsSchema):
    """Updates Pixel-only settings (model, system instruction, vision, presence) for a bot."""
    settings = get_all_settings()
    row = {
        "model": new_settings.model,
        "system_instruction": new_settings.system_instruction,
        "vision_enabled": bool(new_settings.vision_enabled),
        "wake_word_enabled": bool(new_settings.wake_word_enabled),
        "presence_scan_enabled": bool(new_settings.presence_scan_enabled),
        "presence_scan_interval_sec": int(new_settings.presence_scan_interval_sec),
        "greeting_cooldown_minutes": int(new_settings.greeting_cooldown_minutes),
    }
    settings[device_id] = row
    save_all_settings(settings)
    history_by_device.pop(device_id, None)
    vision_enabled_by_device[device_id] = row["vision_enabled"]
    wake_word_enabled_by_device[device_id] = row["wake_word_enabled"]
    await _send_runtime_vision_to_esp32(device_id, row["vision_enabled"])
    await _send_runtime_wake_word_to_esp32(device_id, row["wake_word_enabled"])
    await _send_runtime_presence_scan_to_esp32(device_id)
    if _maps_debug_enabled():
        _maps_debug(
            f"pixel_settings_saved device_id={device_id!r} in_memory_history_cleared=True"
        )
    return {
        "status": "success",
        "settings": get_bot_settings(device_id),
        "maps_geocode": {"ok": True, "error": None},
    }


@app.post("/api/settings/{device_id}/reset")
async def reset_settings_to_default(device_id: str):
    """Restore Pixel-only stored settings to defaults; hub clock/Maps are unchanged."""
    row = _default_stored_bot_settings()
    settings = get_all_settings()
    settings[device_id] = row
    save_all_settings(settings)
    history_by_device.pop(device_id, None)
    vision_enabled_by_device[device_id] = row["vision_enabled"]
    wake_word_enabled_by_device[device_id] = row["wake_word_enabled"]
    await _send_runtime_vision_to_esp32(device_id, row["vision_enabled"])
    await _send_runtime_wake_word_to_esp32(device_id, row["wake_word_enabled"])
    await _send_runtime_presence_scan_to_esp32(device_id)
    return {"status": "success", "settings": get_bot_settings(device_id)}


@app.get("/api/bots/{device_id}/face-profiles")
async def api_list_face_profiles(device_id: str):
    """List enrolled face profiles for a bot (reference embeddings on disk)."""
    return {"profiles": list_profiles(device_id)}


@app.post("/api/bots/{device_id}/face-profiles")
async def api_create_face_profile(device_id: str, body: CreateFaceProfileBody):
    row = create_profile(device_id, body.display_name)
    return {"status": "success", "profile": row}


@app.delete("/api/bots/{device_id}/face-profiles/{profile_id}")
async def api_delete_face_profile(device_id: str, profile_id: str):
    if not delete_profile(device_id, profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}


@app.post("/api/bots/{device_id}/face-profiles/{profile_id}/reference")
async def api_upload_face_reference(device_id: str, profile_id: str, file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large")
    rows = list_profiles(device_id)
    if not any(r.get("profile_id") == profile_id for r in rows):
        raise HTTPException(status_code=404, detail="Unknown profile")
    name = add_reference_jpeg(device_id, profile_id, data)
    if not name:
        raise HTTPException(
            status_code=400,
            detail="Could not extract a face embedding from this image (try another photo).",
        )
    return {"status": "success", "filename": name}


@app.post("/api/bots/{device_id}/face-profiles/{profile_id}/capture-from-pixel")
async def api_capture_face_reference_from_pixel(device_id: str, profile_id: str):
    if not device_stream_is_live(device_id):
        raise HTTPException(status_code=503, detail="Pixel is not connected to the hub")
    rows = list_profiles(device_id)
    if not any(r.get("profile_id") == profile_id for r in rows):
        raise HTTPException(status_code=404, detail="Unknown profile")
    ws = get_active_esp32_socket(device_id)
    if not ws:
        raise HTTPException(status_code=503, detail="No active WebSocket for this bot")
    request_id = uuid.uuid4().hex
    pending_reference_capture[device_id] = {
        "request_id": request_id,
        "profile_id": profile_id,
        "deadline": time.monotonic() + 45.0,
    }
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "request_reference_capture",
                    "request_id": request_id,
                    "profile_id": profile_id,
                }
            )
        )
    except Exception as e:
        pending_reference_capture.pop(device_id, None)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"status": "accepted", "request_id": request_id}


# ==========================================
#          ESP32 WEBSOCKET STREAMING
# ==========================================
# Store active streaming sessions by ESP32 WebSocket connection
active_streams = {}
# Curated multi-turn history per bot (types.Content list); rebuilt into each one-shot Chat per turn.
history_by_device: dict[str, list] = {}
gemini_turn_locks: dict[str, asyncio.Lock] = {}
vision_enabled_by_device = {}
wake_word_enabled_by_device = {}
timezone_rule_by_device = {}
# (device_id, profile_id) -> unix time of last presence-triggered greeting
last_presence_greeting_at: dict[tuple[str, str], float] = {}
# device_id -> pending enrollment capture from hub UI
pending_reference_capture: dict[str, dict[str, Any]] = {}


def _purge_bot_runtime_state(device_id: str) -> None:
    """Clear per-device in-memory hub state (chat history, locks, tool turn state)."""
    history_by_device.pop(device_id, None)
    vision_enabled_by_device.pop(device_id, None)
    wake_word_enabled_by_device.pop(device_id, None)
    timezone_rule_by_device.pop(device_id, None)
    gemini_turn_locks.pop(device_id, None)
    turn_tool_state_by_device.pop(device_id, None)


async def _disconnect_websockets_for_device(device_id: str) -> None:
    """Close active ESP32 WebSocket streams for this device_id (e.g. after hub-side removal)."""
    for ws, session in list(active_streams.items()):
        if session.get("device_id") != device_id:
            continue
        try:
            await ws.close(code=1001)
        except Exception as e:
            print(f"[Omnibot] Error closing stream for {device_id!r}: {e}")


async def _push_timezone_to_all_streams(timezone_rule: str) -> None:
    """Notify all connected ESP32 streams of a hub timezone update."""
    tr = str(timezone_rule or DEFAULT_TIMEZONE_RULE)
    payload = json.dumps({"type": "runtime_timezone", "timezone_rule": tr})
    for ws in list(active_streams.keys()):
        try:
            await ws.send_text(payload)
        except Exception as e:
            print(f"Failed to push timezone to stream: {e}")


def _make_face_animation_tool(device_id: str):
    """Builder so face_animation is bound to the correct bot device."""

    allowed = frozenset(
        FACE_ANIMATION_FUNCTION_DECLARATION["parameters"]["properties"]["animation"]["enum"]
    )

    def face_animation(animation: str) -> dict:
        a = (animation or "").strip().lower()
        if a not in allowed:
            a = "speaking"
        tool_state = turn_tool_state_by_device.setdefault(
            device_id, {"face_animation": False}
        )
        if tool_state.get("face_animation", False):
            return {
                "ok": False,
                "skipped": True,
                "reason": "another animation tool was already called this turn",
            }
        tool_state["face_animation"] = True

        _schedule_face_animation_to_esp32(device_id, a)
        anim_ms = FACE_ANIMATION_DISPLAY_MS
        return {"ok": True, "display": "face animation queued on robot"}

    return face_animation


def show_face_animation(device_id: str, animation: str) -> dict:
    """Convenience wrapper to trigger an expressive face animation (speaking/happy/mad).

    Uses the same semantics, guardrails, and broadcast behavior as the Gemini `face_animation` tool.
    """
    face_animation_fn = _make_face_animation_tool(device_id)
    return face_animation_fn(animation=animation)


async def _send_face_animation_json_to_esp32(device_id: str, animation: str) -> None:
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    dur = FACE_ANIMATION_DISPLAY_MS
    payload = {
        "type": "face_animation",
        "animation": animation,
        "duration_ms": dur,
    }
    try:
        await ws.send_text(json.dumps(payload))
    except Exception as e:
        print(f"Failed to send face_animation to ESP32: {e}")


def _schedule_face_animation_to_esp32(device_id: str, animation: str) -> None:
    loop = _main_async_loop
    if loop is None:
        print("face_animation: no async loop yet; skipping robot display")
        return
    asyncio.run_coroutine_threadsafe(
        _send_face_animation_json_to_esp32(device_id, animation),
        loop,
    )


def is_vision_enabled(device_id: str) -> bool:
    if device_id in vision_enabled_by_device:
        return bool(vision_enabled_by_device[device_id])
    return bool(get_bot_settings(device_id).get("vision_enabled", DEFAULT_VISION_ENABLED))


def is_wake_word_enabled(device_id: str) -> bool:
    if device_id in wake_word_enabled_by_device:
        return bool(wake_word_enabled_by_device[device_id])
    return bool(get_bot_settings(device_id).get("wake_word_enabled", DEFAULT_WAKE_WORD_ENABLED))


async def _send_runtime_vision_to_esp32(device_id: str, enabled: bool) -> None:
    """Pushes vision/capture setting to Pixel so firmware can skip camera work when off."""
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    try:
        await ws.send_text(json.dumps({"type": "runtime_vision", "enabled": bool(enabled)}))
    except Exception as e:
        print(f"Failed to send runtime_vision to ESP32: {e}")


async def _send_runtime_wake_word_to_esp32(device_id: str, enabled: bool) -> None:
    """Push wake-word streaming on/off to Pixel firmware."""
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    try:
        await ws.send_text(json.dumps({"type": "runtime_wake_word", "enabled": bool(enabled)}))
    except Exception as e:
        print(f"Failed to send runtime_wake_word to ESP32: {e}")


def get_timezone_rule(device_id: str) -> str:
    if device_id in timezone_rule_by_device:
        return timezone_rule_by_device[device_id]
    return get_bot_settings(device_id).get("timezone_rule", DEFAULT_TIMEZONE_RULE)


async def _send_runtime_timezone_to_esp32(device_id: str, timezone_rule: str) -> None:
    """Pushes timezone rule to Pixel so RTC/NTP sync uses local time rules."""
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "runtime_timezone",
                    "timezone_rule": str(timezone_rule or DEFAULT_TIMEZONE_RULE),
                }
            )
        )
    except Exception as e:
        print(f"Failed to send runtime_timezone to ESP32: {e}")


async def _send_runtime_presence_scan_to_esp32(device_id: str) -> None:
    """Pushes presence-scan interval/cooldown/enabled to Pixel firmware."""
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    bs = get_bot_settings(device_id)
    try:
        await ws.send_text(
            json.dumps(
                {
                    "type": "runtime_presence_scan",
                    "enabled": bool(bs.get("presence_scan_enabled", DEFAULT_PRESENCE_SCAN_ENABLED)),
                    "interval_sec": int(bs.get("presence_scan_interval_sec", DEFAULT_PRESENCE_SCAN_INTERVAL_SEC)),
                    "cooldown_minutes": int(bs.get("greeting_cooldown_minutes", DEFAULT_GREETING_COOLDOWN_MINUTES)),
                }
            )
        )
    except Exception as e:
        print(f"Failed to send runtime_presence_scan to ESP32: {e}")




def _extract_query_from_maps_uri(uri: str) -> str:
    raw = (uri or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query or "")
        q = (qs.get("q", [""])[0] or "").strip()
        if q:
            return q
        daddr = (qs.get("daddr", [""])[0] or "").strip()
        if daddr:
            return daddr
        path_tail = (parsed.path or "").strip("/").split("/")[-1]
        return path_tail.replace("+", " ").strip()
    except Exception:
        return ""


def _best_maps_location_query(maps_sources: list[dict]) -> str:
    for s in maps_sources or []:
        title = str(s.get("title") or "").strip()
        if title:
            return title
    for s in maps_sources or []:
        q = _extract_query_from_maps_uri(str(s.get("uri") or ""))
        if q:
            return q
    return ""


# links2004/WebSockets defaults to ~15KB RX unless WEBSOCKETS_MAX_DATA_SIZE is raised in firmware.
# Keep under this so a single binary frame never drops the connection on stock builds.
_MAP_JPEG_MAX_BYTES = 14000


def _normalize_map_jpeg(
    im: "Image.Image", size: int = 240, max_bytes: int = _MAP_JPEG_MAX_BYTES
) -> Optional[bytes]:
    """Resize to size×size and re-encode as baseline JPEG ready for ESP32.

    Lowers JPEG quality as needed so the buffer stays under ``max_bytes`` (long routes
    produce busier images that exceed ~15KB at quality 85 and can disconnect the client).
    """
    try:
        if im.size != (size, size):
            im = im.resize((size, size), Image.Resampling.LANCZOS)
        smallest: Optional[bytes] = None
        for quality in (85, 78, 70, 62, 55, 48, 40):
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=quality, optimize=True, progressive=False)
            data = out.getvalue()
            if len(data) <= max_bytes:
                if quality < 85:
                    print(
                        f"[Omnibot] Map JPEG encoded at quality={quality} ({len(data)} bytes, cap={max_bytes})"
                    )
                return data
            if smallest is None or len(data) < len(smallest):
                smallest = data
        if smallest is not None:
            print(
                f"[Omnibot] Map JPEG still {len(smallest)} bytes > {max_bytes}; sending smallest attempt"
            )
            return smallest
        return None
    except Exception as e:
        print(f"[Omnibot] Map JPEG normalization failed: {e}")
        return None


def _detect_error_tile(data: bytes) -> bool:
    """Return True if `data` looks like a Google Maps error tile (uniform gray)."""
    if len(data) < 256:
        return True
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        arr = np.array(im)
        std_dev = float(arr.std())
        if std_dev < 15.0:
            print(f"[Omnibot] Map looks like an error tile (std_dev={std_dev:.1f}), skipping.")
            return True
    except Exception:
        pass
    return False


def _fetch_plain_static_map(
    *,
    api_key: str,
    location_query: str,
    center: str,
    size: int = 240,
) -> Optional[bytes]:
    """Fetch a plain roadmap JPEG from the Static Maps API. Returns None on any failure."""
    params = {
        "size": f"{size}x{size}",
        "scale": "1",
        "format": "jpg-baseline",
        "maptype": "roadmap",
        "key": api_key,
    }
    if location_query:
        params["markers"] = f"size:mid|color:red|{location_query}"
        params["center"] = location_query
        params["zoom"] = "15"
    elif center:
        params["center"] = center
        params["zoom"] = "14"
        params["markers"] = f"size:mid|color:red|{center}"
    else:
        return None
    styles = [
        "feature:poi|element:labels|visibility:off",
        "feature:transit|visibility:off",
        "feature:road|element:labels|visibility:off",
    ]
    for s in styles:
        params.setdefault("style", []).append(s)
    log_center = location_query or center
    print(f"[Omnibot] Static map request: center={log_center!r}")
    try:
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[Omnibot] Static map request failed: {e}")
        return None
    api_warning = r.headers.get("X-Staticmap-API-Warning", "")
    if api_warning:
        print(f"[Omnibot] Static Maps API warning: {api_warning}")
        return None
    data = r.content or b""
    if _detect_error_tile(data):
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        print(f"[Omnibot] Static map image open failed: {e}")
        return None
    return _normalize_map_jpeg(im, size)


def _truncate_calling_card_str(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


def _cover_crop_square(im: "Image.Image", dim: int) -> "Image.Image":
    """Scale and center-crop to dim×dim (cover)."""
    w, h = im.size
    if w <= 0 or h <= 0:
        return im
    scale = max(dim / w, dim / h)
    new_w = max(1, int(w * scale + 0.5))
    new_h = max(1, int(h * scale + 0.5))
    im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - dim) // 2
    top = (new_h - dim) // 2
    return im.crop((left, top, left + dim, top + dim))


def _calling_card_photo_to_jpeg(place_photo: "Image.Image", size: int = 240) -> Optional[bytes]:
    """Cover-crop to a square, encode JPEG under ESP32 WebSocket ~15KB frame limit."""
    try:
        w, h = place_photo.size
        if w <= 0 or h <= 0:
            return None
        max_b = CALLING_CARD_JPEG_MAX_BYTES
        # Try full resolution down to ~128px until a quality step fits under max_b.
        for dim in (size, 220, 200, 185, 170, 160, 148, 136, 128):
            square = _cover_crop_square(place_photo, dim)
            for q in range(88, 9, -3):
                out = io.BytesIO()
                square.save(
                    out,
                    format="JPEG",
                    quality=q,
                    optimize=True,
                    progressive=False,
                    subsampling=2,
                )
                data = out.getvalue()
                if 256 < len(data) <= max_b:
                    print(
                        f"[Omnibot] Calling card JPEG: {len(data)} bytes "
                        f"(square={dim} quality={q}, fits WebSocket limit)"
                    )
                    return data
        print(
            f"[Omnibot] Calling card JPEG: could not encode under {max_b} bytes; "
            "ESP32 would drop frame — no photo"
        )
        return None
    except Exception as e:
        print(f"[Omnibot] Calling card photo JPEG encode failed: {e}")
        return None


def _fetch_calling_card_place_sync(
    *,
    api_key: str,
    location_query: str,
    photo_size: int = CALLING_CARD_PHOTO_W,
) -> dict[str, Any]:
    """Places Text Search + optional photo / Street View; returns metadata and photo_jpeg for ESP32.

    Does not composite a full-screen card (Pixel renders text). If no imagery is available,
    ``photo_jpeg`` is None.
    """
    name: Optional[str] = None
    address: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    place_type: Optional[str] = None
    place_lat: Optional[float] = None
    place_lng: Optional[float] = None
    photo_refs: list[str] = []

    q = (location_query or "").strip()
    if not q:
        return {
            "name": "",
            "address": "",
            "rating": None,
            "review_count": None,
            "category": "",
            "photo_jpeg": None,
        }

    try:
        ts_r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": q, "key": api_key},
            timeout=12,
        )
        ts_r.raise_for_status()
        ts_json = ts_r.json()
        status = ts_json.get("status")
        results = ts_json.get("results", [])
        print(f"[Omnibot] Places TextSearch status={status!r} results={len(results)}")
        if results:
            hit = results[0]
            name = hit.get("name", "") or ""
            address = hit.get("formatted_address", "") or ""
            r = hit.get("rating")
            if r is not None:
                try:
                    rating = float(r)
                except (TypeError, ValueError):
                    rating = None
            urt = hit.get("user_ratings_total")
            if urt is not None:
                try:
                    review_count = int(urt)
                except (TypeError, ValueError):
                    review_count = None
            skip = {"point_of_interest", "establishment", "premise", "food"}
            for t in hit.get("types", []):
                if t not in skip:
                    place_type = t.replace("_", " ").title()
                    break
            photo_refs = [p["photo_reference"] for p in hit.get("photos", []) if p.get("photo_reference")]
            loc = hit.get("geometry", {}).get("location", {})
            place_lat = loc.get("lat")
            place_lng = loc.get("lng")
            print(
                f"[Omnibot] TextSearch hit: {name!r}  rating={rating}  photos={len(photo_refs)}  lat={place_lat}"
            )
    except Exception as e:
        print(f"[Omnibot] Places TextSearch failed: {e}")

    place_photo = None
    if photo_refs:
        try:
            ph_r = requests.get(
                "https://maps.googleapis.com/maps/api/place/photo",
                params={"maxwidth": "600", "photo_reference": photo_refs[0], "key": api_key},
                timeout=15,
                allow_redirects=True,
            )
            ph_r.raise_for_status()
            print(
                f"[Omnibot] Place photo: status={ph_r.status_code} "
                f"size={len(ph_r.content or b'')}"
            )
            if len(ph_r.content) > 256:
                try:
                    place_photo = Image.open(io.BytesIO(ph_r.content)).convert("RGB")
                except Exception as e2:
                    print(f"[Omnibot] Place photo open failed: {e2}")
        except Exception as e:
            print(f"[Omnibot] Place photo fetch failed: {e}")

    if place_photo is None:
        sv_loc = f"{place_lat},{place_lng}" if place_lat and place_lng else q
        print(f"[Omnibot] No Places photo — trying Street View at {sv_loc!r}")
        try:
            sv_r = requests.get(
                "https://maps.googleapis.com/maps/api/streetview",
                params={
                    "size": f"{photo_size}x{photo_size}",
                    "location": sv_loc,
                    "fov": "80",
                    "pitch": "5",
                    "key": api_key,
                },
                timeout=15,
            )
            sv_r.raise_for_status()
            print(
                f"[Omnibot] Street View: status={sv_r.status_code} size={len(sv_r.content or b'')}"
            )
            if len(sv_r.content) > 1024:
                place_photo = Image.open(io.BytesIO(sv_r.content)).convert("RGB")
        except Exception as e:
            print(f"[Omnibot] Street View fetch failed: {e}")

    photo_jpeg: Optional[bytes] = None
    if place_photo is not None:
        photo_jpeg = _calling_card_photo_to_jpeg(place_photo, photo_size)

    display_name = (name or q).strip()
    return {
        "name": display_name,
        "address": (address or "").strip(),
        "rating": rating,
        "review_count": review_count,
        "category": (place_type or "").strip(),
        "photo_jpeg": photo_jpeg,
    }


def _capture_directions_map_jpeg_sync(
    *,
    api_key: str,
    origin_lat: float,
    origin_lng: float,
    destination: str,
    fallback_plain_params: dict,
    size: int = 240,
) -> Tuple[Optional[bytes], Optional[float], Optional[int]]:
    """Build a 240x240 route map JPEG using the Directions + Static Maps APIs.

    Returns ``(jpeg_bytes, distance_miles, duration_minutes)``. Miles/minutes come from the
    first leg when routing succeeds; otherwise ``(fallback_jpeg, None, None)``.
    """
    polyline_str: Optional[str] = None
    distance_miles: Optional[float] = None
    duration_minutes: Optional[int] = None
    try:
        dir_params = {
            "origin": f"{origin_lat:.6f},{origin_lng:.6f}",
            "destination": destination,
            "key": api_key,
        }
        dir_r = requests.get(
            "https://maps.googleapis.com/maps/api/directions/json",
            params=dir_params, timeout=15)
        dir_r.raise_for_status()
        dir_data = dir_r.json()
        routes = dir_data.get("routes", [])
        if routes:
            overview = routes[0].get("overview_polyline", {})
            polyline_str = overview.get("points", "")
            print(f"[Omnibot] Directions polyline length: {len(polyline_str)} chars")
            legs = routes[0].get("legs") or []
            if legs:
                leg0 = legs[0]
                dist_obj = leg0.get("distance") or {}
                dur_obj = leg0.get("duration") or {}
                if isinstance(dist_obj, dict) and dist_obj.get("value") is not None:
                    try:
                        meters = float(dist_obj["value"])
                        distance_miles = round(meters / 1609.344, 2)
                    except (TypeError, ValueError):
                        pass
                if isinstance(dur_obj, dict) and dur_obj.get("value") is not None:
                    try:
                        secs = float(dur_obj["value"])
                        duration_minutes = max(1, int(round(secs / 60.0)))
                    except (TypeError, ValueError):
                        pass
                print(
                    f"[Omnibot] Directions leg: {distance_miles!r} mi, {duration_minutes!r} min"
                )
        else:
            status = dir_data.get("status", "UNKNOWN")
            print(f"[Omnibot] Directions API returned no routes (status={status!r}); falling back.")
    except Exception as e:
        print(f"[Omnibot] Directions API request failed: {e}")

    if not polyline_str:
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)

    # Build a Static Map with the route polyline + origin/destination markers
    # scale=1 keeps the JPEG small enough for the ESP32 JPEG decoder.
    # format=jpg (NOT jpg-baseline) is required for polylines to render.
    # color:green|size:mid / color:red|size:mid give clean pin markers.
    sm_params = [
        ("size", f"{size}x{size}"),
        ("scale", "1"),
        ("format", "jpg"),
        ("maptype", "roadmap"),
        ("key", api_key),
        ("path", f"color:0x1A73E8FF|weight:5|enc:{polyline_str}"),
        ("markers", f"color:green|size:mid|{origin_lat:.6f},{origin_lng:.6f}"),
        ("markers", f"color:red|size:mid|{destination}"),
        ("style", "feature:poi|element:labels|visibility:off"),
        ("style", "feature:transit|visibility:off"),
    ]
    print(f"[Omnibot] Directions map request: dest={destination!r}")
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/staticmap",
            params=sm_params, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[Omnibot] Directions static map request failed: {e}")
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)

    api_warning = r.headers.get("X-Staticmap-API-Warning", "")
    if api_warning:
        print(f"[Omnibot] Directions map API warning: {api_warning}")
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)

    data = r.content or b""
    if _detect_error_tile(data):
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)

    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        print(f"[Omnibot] Directions map image open failed: {e}")
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)
    jpg = _normalize_map_jpeg(im, size)
    return (jpg, distance_miles, duration_minutes)


def _capture_static_map_jpeg_sync(
    *,
    api_key: str,
    maps_sources: list[dict],
    fallback_lat: Optional[float],
    fallback_lng: Optional[float],
    location_override: str = "",
    display_style: str = "calling_card",
    size: int = 240,
) -> Tuple[Optional[bytes], Optional[float], Optional[int]]:
    """Route to the correct map renderer. Returns ``(jpeg, distance_miles, duration_min)``."""
    key = (api_key or "").strip()
    if not key:
        return (None, None, None)

    # Priority: explicit location from tool call > grounding title > fallback coords
    location_query = (location_override or "").strip() or _best_maps_location_query(maps_sources)
    center = (
        f"{float(fallback_lat):.6f},{float(fallback_lng):.6f}"
        if (fallback_lat is not None and fallback_lng is not None)
        else ""
    )

    # Common fallback params for the plain static map (used by both helpers)
    fallback_plain_params = dict(
        api_key=key,
        location_query=location_query,
        center=center,
        size=size,
    )

    style = (display_style or "directions").strip().lower()

    if style == "directions":
        if fallback_lat is not None and fallback_lng is not None and location_query:
            print(f"[Omnibot] Rendering directions map: home->'{location_query}'")
            return _capture_directions_map_jpeg_sync(
                api_key=key,
                origin_lat=float(fallback_lat),
                origin_lng=float(fallback_lng),
                destination=location_query,
                fallback_plain_params=fallback_plain_params,
                size=size,
            )
        print("[Omnibot] Directions requested but no home coordinates; using plain static map.")
        fb = _fetch_plain_static_map(**fallback_plain_params)
        return (fb, None, None)

    # Non-directions callers should use _send_calling_card_to_esp32 instead.
    fb = _fetch_plain_static_map(**fallback_plain_params)
    return (fb, None, None)


async def _send_calling_card_to_esp32(
    device_id: str,
    maps_sources: list[dict],
    api_key: str,
    fallback_lat: Optional[float],
    fallback_lng: Optional[float],
    location_override: str = "",
) -> bool:
    """Send `show_calling_card` JSON plus optional photo JPEG (0x05) for Pixel to render the card."""
    key = (api_key or "").strip()
    if not key:
        return False

    location_query = (location_override or "").strip() or _best_maps_location_query(maps_sources)

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: _fetch_calling_card_place_sync(api_key=key, location_query=location_query),
        )
    except Exception as e:
        print(f"[Omnibot] Calling card fetch failed: {e}")
        return False

    if not isinstance(data, dict):
        return False

    name_raw = str(data.get("name") or location_query or "Place")
    addr_raw = str(data.get("address") or "")
    cat_raw = str(data.get("category") or "")
    rating = data.get("rating")
    review_count = data.get("review_count")
    photo_jpeg = data.get("photo_jpeg")

    payload: dict[str, Any] = {
        "type": "show_calling_card",
        "name": _truncate_calling_card_str(name_raw, CALLING_CARD_NAME_MAX),
        "address": _truncate_calling_card_str(addr_raw, CALLING_CARD_ADDRESS_MAX),
        "category": _truncate_calling_card_str(cat_raw, CALLING_CARD_CATEGORY_MAX),
        "duration_ms": MAP_DISPLAY_MS,
        "photo_w": CALLING_CARD_PHOTO_W,
        "photo_h": CALLING_CARD_PHOTO_H,
    }
    if rating is not None:
        try:
            payload["rating"] = float(rating)
        except (TypeError, ValueError):
            pass
    if review_count is not None:
        try:
            payload["review_count"] = int(review_count)
        except (TypeError, ValueError):
            pass

    ws = get_active_esp32_socket(device_id)
    if not ws:
        return False
    try:
        await ws.send_text(json.dumps(payload))
        if isinstance(photo_jpeg, (bytes, bytearray)) and len(photo_jpeg) > 32:
            await ws.send_bytes(bytes([0x05]) + bytes(photo_jpeg))
        print(
            f"[Omnibot] Sent calling card to ESP32 (photo={'yes' if photo_jpeg else 'no'})"
        )
    except Exception as e:
        print(f"[Omnibot] Failed to send calling card to ESP32: {e}")
        return False
    return True


async def _send_static_map_jpeg_to_esp32(
    device_id: str,
    maps_sources: list[dict],
    api_key: str,
    fallback_lat: Optional[float],
    fallback_lng: Optional[float],
    location_override: str = "",
) -> bool:
    """Send optional ``show_directions`` JSON (distance/duration) then full-frame map binary 0x04."""
    loop = asyncio.get_running_loop()
    try:
        jpeg, distance_miles, duration_minutes = await loop.run_in_executor(
            None,
            lambda: _capture_static_map_jpeg_sync(
                api_key=api_key,
                maps_sources=maps_sources,
                fallback_lat=fallback_lat,
                fallback_lng=fallback_lng,
                location_override=location_override,
                display_style="directions",
            ),
        )
    except Exception as e:
        print(f"[Omnibot] Static map render failed: {e}")
        return False
    if not jpeg:
        return False
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return False
    try:
        dir_payload: dict[str, Any] = {
            "type": "show_directions",
            "duration_ms": MAP_DISPLAY_MS,
        }
        if distance_miles is not None:
            dir_payload["distance_miles"] = float(distance_miles)
        if duration_minutes is not None:
            dir_payload["duration_minutes"] = int(duration_minutes)
        await ws.send_text(json.dumps(dir_payload))
        packet = bytes([0x04]) + bytes(jpeg)
        await ws.send_bytes(packet)
        print(
            f"[Omnibot] Sent directions map JPEG to ESP32 ({len(jpeg)} bytes, "
            f"mi={distance_miles!r} min={duration_minutes!r})"
        )
    except Exception as e:
        print(f"[Omnibot] Failed to send map JPEG to ESP32: {e}")
        return False
    return True


async def _try_send_directions_map_early(device_id: str) -> None:
    """Fetch and send directions map as soon as the tool runs.

    Without this, the hub waits until the Gemini stream ends before calling Directions +
    Static Maps, so the robot shows a placeholder for the entire reply plus API latency.
    """
    if map_jpeg_sent_this_turn_by_device.get(device_id):
        return
    loc = (map_location_override_by_device.get(device_id) or "").strip()
    if not loc:
        return
    style = map_display_style_by_device.get(device_id, "calling_card")
    if style != "directions":
        return
    bot = get_bot_settings(device_id)
    lat, lng = bot.get("maps_latitude"), bot.get("maps_longitude")
    if lat is None or lng is None:
        return
    maps_key = ""
    if not maps_key:
        return
    try:
        ok = await _send_static_map_jpeg_to_esp32(
            device_id=device_id,
            maps_sources=[],
            api_key=maps_key,
            fallback_lat=lat,
            fallback_lng=lng,
            location_override=loc,
        )
        if ok:
            map_jpeg_sent_this_turn_by_device[device_id] = True
    except Exception as e:
        print(f"[Omnibot] Early directions map send failed: {e}")


async def _send_map_jpeg_to_esp32_after_turn(
    device_id: str,
    maps_sources: list[dict],
    api_key: str,
    fallback_lat: Optional[float],
    fallback_lng: Optional[float],
    location_override: str = "",
    display_style: str = "calling_card",
) -> None:
    """End-of-turn: calling card (JSON + 0x05) or directions map JPEG (0x04)."""
    if map_jpeg_sent_this_turn_by_device.get(device_id):
        return
    style = (display_style or "calling_card").strip().lower()
    if style == "directions" and (fallback_lat is None or fallback_lng is None):
        style = "calling_card"
    if style == "calling_card":
        ok = await _send_calling_card_to_esp32(
            device_id=device_id,
            maps_sources=maps_sources,
            api_key=api_key,
            fallback_lat=fallback_lat,
            fallback_lng=fallback_lng,
            location_override=location_override,
        )
    else:
        ok = await _send_static_map_jpeg_to_esp32(
            device_id=device_id,
            maps_sources=maps_sources,
            api_key=api_key,
            fallback_lat=fallback_lat,
            fallback_lng=fallback_lng,
            location_override=location_override,
        )
        # Pixel may reconnect just after stream end; one retry helps dropped links.
        if not ok:
            await asyncio.sleep(1.5)
            ok = await _send_static_map_jpeg_to_esp32(
                device_id=device_id,
                maps_sources=maps_sources,
                api_key=api_key,
                fallback_lat=fallback_lat,
                fallback_lng=fallback_lng,
                location_override=location_override,
            )
    if ok:
        map_jpeg_sent_this_turn_by_device[device_id] = True


async def _send_map_jpeg_after_face_animation_map(device_id: str, api_key: str) -> None:
    """Best-effort calling card when model uses face_animation(map) before end-of-turn metadata."""
    if map_jpeg_sent_this_turn_by_device.get(device_id):
        return
    bot = get_bot_settings(device_id)
    ok = await _send_calling_card_to_esp32(
        device_id=device_id,
        maps_sources=[],
        api_key=api_key,
        fallback_lat=bot.get("maps_latitude"),
        fallback_lng=bot.get("maps_longitude"),
        location_override="",
    )
    if ok:
        map_jpeg_sent_this_turn_by_device[device_id] = True


def _schedule_map_jpeg_after_face_animation_map(device_id: str) -> None:
    """Schedule map screenshot from Gemini tool thread when model calls face_animation(map)."""
    loop = _main_async_loop
    if loop is None:
        print("face_animation(map): no async loop yet; skipping map screenshot")
        return
    maps_key = ""
    if not maps_key:
        print(
            "[Omnibot] face_animation(map): Google Maps API key missing; cannot screenshot map for ESP32."
        )
        return
    asyncio.run_coroutine_threadsafe(
        _send_map_jpeg_after_face_animation_map(device_id, maps_key),
        loop,
    )


async def stream_chat_turn_response(device_id: str, message_content):
    """Streams one Gemini turn using local history and a fresh config each request (one-shot Chat)."""
    global _main_async_loop
    _main_async_loop = asyncio.get_running_loop()

    stream_id = str(uuid.uuid4())
    full_text = ""
    loop = asyncio.get_running_loop()
    first_token_notified = False
    last_search_sources: list[dict] = []
    last_search_queries: list[str] = []

    await manager.broadcast({
        "type": "ai_response_stream_start",
        "stream_id": stream_id,
        "device_id": device_id,
    })

    lock = gemini_turn_locks.setdefault(device_id, asyncio.Lock())
    async with lock:
        turn_tool_state_by_device[device_id] = {"face_animation": False}
        bot_settings = get_bot_settings(device_id)
        model = bot_settings["model"]
        system_instruction = bot_settings["system_instruction"]
        prior_history = history_by_device.setdefault(device_id, [])
        if _maps_debug_enabled():
            bs = bot_settings
            _maps_debug(
                f"turn_start device_id={device_id!r} history_len={len(prior_history)} "
                f"(fresh tools/config each turn via generate_content_stream)"
            )
            _maps_debug(
                f"maps_grounding_enabled={bs.get('maps_grounding_enabled')} "
                f"postal={bs.get('maps_postal_code')!r} country={bs.get('maps_country')!r} "
                f"lat={bs.get('maps_latitude')!r} lng={bs.get('maps_longitude')!r} "
                f"display_name={bs.get('maps_display_name')!r}"
            )
            _maps_debug(
                "If Maps is on but replies ignore location, POST "
                f"/api/text-command/reset/{device_id} clears in-memory history."
            )
        if _route_debug_enabled():
            _route_debug(f"device_id={device_id!r} effective_builtin_retrieval=google_search route_reason=maps_removed")
        config = _pixel_chat_generate_config(
            system_instruction=system_instruction,
            device_id=device_id,
        )
        gc = get_genai_client()
        if gc is None:
            raise RuntimeError(
                "Gemini API key not configured. Set GEMINI_API_KEY or add a key in Hub settings."
            )
        chat = gc.chats.create(
            model=model,
            config=config,
            history=list(prior_history),
        )
        
        message_to_send = message_content
        MAX_TURNS = 3
        
        try:
            for turn in range(MAX_TURNS):
                response_stream = chat.send_message_stream(message_to_send)
                functions_to_execute = []
                
                try:
                    while True:
                        chunk = await loop.run_in_executor(
                            None,
                            partial(next, response_stream, None)
                        )
                        if chunk is None:
                            break

                        # Detect tool/function calls in this chunk and broadcast them
                        try:
                            if getattr(chunk, "candidates", None):
                                for candidate in chunk.candidates:
                                    gm = getattr(candidate, "grounding_metadata", None)
                                    web_src, web_queries = _extract_search_sources_from_grounding_metadata(gm)
                                    if web_src:
                                        last_search_sources = web_src
                                    if web_queries:
                                        last_search_queries = web_queries
                                        
                                    if candidate.content and getattr(candidate.content, "parts", None):
                                        for part in candidate.content.parts:
                                            fc = getattr(part, "function_call", None)
                                            if fc:
                                                functions_to_execute.append(fc)
                        except Exception as e:
                            print(f"Error inspecting chunk for tool calls: {e}")

                        chunk_text = (chunk.text or "")
                        if not chunk_text:
                            continue

                        if chunk_text.startswith(full_text):
                            delta = chunk_text[len(full_text):]
                        else:
                            delta = chunk_text

                        if delta:
                            full_text += delta

                            if not first_token_notified:
                                esp32_ws = get_active_esp32_socket(device_id)
                                if esp32_ws:
                                    try:
                                        await esp32_ws.send_text(json.dumps({
                                            "type": "gemini_first_token"
                                        }))
                                    except Exception as e:
                                        print(f"Failed to notify ESP32 first token: {e}")
                                first_token_notified = True

                            await manager.broadcast({
                                "type": "ai_response_stream_delta",
                                "stream_id": stream_id,
                                "data": delta
                            })
                except Exception as e:
                    print(f"Stream interrupted: {e}")
                    
                if not functions_to_execute:
                    break
                    
                # Execute manually
                func_responses = []
                for fc in functions_to_execute:
                    args = dict(fc.args) if fc.args else {}
                    fname = fc.name
                    
                    await manager.broadcast({
                        "type": "tool_call",
                        "stream_id": stream_id,
                        "function_name": fname,
                        "arguments": args
                    })
                    
                    result = {"error": "Unknown function"}
                    
                    if fname == "face_animation":
                        result = _make_face_animation_tool(device_id)(
                            animation=str(args.get("animation") or "speaking")
                        )
                    func_responses.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=fname,
                                response={"result": result},
                                id=fc.id
                            )
                        )
                    )
                
                message_to_send = func_responses

            history_by_device[device_id] = list(chat.get_history(curated=True))
            
        finally:
            turn_tool_state_by_device.pop(device_id, None)

    if _maps_debug_enabled():
        _maps_debug(f"turn_end stream_id={stream_id!r} search_grounding_chunks={len(last_search_sources)} search_queries={len(last_search_queries)}")

    end_msg = {
        "type": "ai_response_stream_end",
        "stream_id": stream_id,
        "device_id": device_id,
        "data": full_text,
    }
    if last_search_sources:
        end_msg["search_sources"] = last_search_sources
    if last_search_queries:
        end_msg["search_queries"] = last_search_queries
    end_msg["device_stream_connected"] = device_stream_is_live(device_id)
    await manager.broadcast(end_msg)

    return full_text

def device_stream_is_live(device_id: str) -> bool:
    """True if this device_id has an active /ws/stream session."""
    did = (device_id or "").strip() or "default_bot"
    return any(s.get("device_id") == did for s in active_streams.values())


def get_active_esp32_socket(device_id: str):
    """Returns an active ESP32 WebSocket for the requested bot, if available."""
    for ws, session in active_streams.items():
        if session.get("device_id") == device_id:
            return ws
    return next(iter(active_streams), None)


async def _process_presence_jpeg(device_id: str, jpeg_bytes: bytes) -> None:
    """Match face on hub; optionally run a short Gemini greeting (cooldown + lock aware)."""
    bs = get_bot_settings(device_id)
    if not bs.get("presence_scan_enabled"):
        return
    if not jpeg_bytes:
        return
    loop = asyncio.get_running_loop()
    try:
        match = await loop.run_in_executor(
            None, lambda: match_probe_jpeg(device_id, jpeg_bytes)
        )
    except Exception as e:
        print(f"[face] presence match error: {e}")
        return
    if not match:
        return
    profile_id, display_name, _score = match
    cool_min = int(bs.get("greeting_cooldown_minutes", DEFAULT_GREETING_COOLDOWN_MINUTES))
    key = (device_id, profile_id)
    now = time.time()
    last = last_presence_greeting_at.get(key, 0)
    if now - last < cool_min * 60:
        return
    lock = gemini_turn_locks.setdefault(device_id, asyncio.Lock())
    if lock.locked():
        return
    if not get_gemini_api_key():
        return

    last_presence_greeting_at[key] = now
    msg = (
        f"The person named {display_name} is visible in front of you. "
        "Give a very brief, warm greeting using their name (one or two short sentences)."
    )
    try:
        full_text = await stream_chat_turn_response(device_id, msg)
        esp32_ws = get_active_esp32_socket(device_id)
        if esp32_ws:
            try:
                await esp32_ws.send_text(
                    json.dumps({"status": "success", "reply": full_text})
                )
            except Exception as e:
                print(f"[face] failed to send presence reply to ESP32: {e}")
        _schedule_face_animation_to_esp32(device_id, "happy")
    except Exception as e:
        print(f"[face] presence greeting failed: {e}")
        last_presence_greeting_at.pop(key, None)


async def _process_enrollment_jpeg(device_id: str, jpeg_bytes: bytes) -> None:
    pend = pending_reference_capture.pop(device_id, None)
    if not pend:
        print("[face] 0x07 JPEG received but no pending enrollment")
        return
    if time.monotonic() > float(pend.get("deadline", 0)):
        print("[face] enrollment capture expired")
        return
    pid = str(pend.get("profile_id") or "")
    if not pid or not jpeg_bytes:
        return
    loop = asyncio.get_running_loop()

    def _add() -> Optional[str]:
        return add_reference_jpeg(device_id, pid, jpeg_bytes)

    try:
        name = await loop.run_in_executor(None, _add)
    except Exception as e:
        print(f"[face] enrollment save error: {e}")
        return
    if name:
        print(f"[face] saved enrollment ref {name} for {device_id}/{pid}")
    else:
        print(f"[face] enrollment failed (no face or represent error) for {device_id}/{pid}")


async def _run_gemini_audio_turn(
    websocket: WebSocket,
    device_id: str,
    raw_pcm: bytes,
    video_frames: list,
) -> str:
    """Build WAV (+ optional MP4), stream one Gemini turn, send JSON reply on same ESP32 socket."""
    use_vision = is_vision_enabled(device_id) and bool(video_frames)
    wav_header = create_wav_header(len(raw_pcm), sample_rate=16000)
    audio_bytes = wav_header + raw_pcm

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    audio_data_url = f"data:audio/wav;base64,{audio_b64}"
    await manager.broadcast({"type": "audio_captured", "data": audio_data_url})

    video_bytes = b""
    if use_vision:
        print("Assembling video in background...", end=" ", flush=True)
        video_bytes = await asyncio.to_thread(assemble_video, video_frames, WAKE_VIDEO_STREAM_FPS)
        print(f"Done! ({len(video_bytes)} bytes)")

        if video_bytes:
            video_b64 = base64.b64encode(video_bytes).decode("utf-8")
            video_data_url = f"data:video/mp4;base64,{video_b64}"
            await manager.broadcast({"type": "video_captured", "data": video_data_url})

    print("Sending to Gemini...", end=" ", flush=True)
    turn_content = [
        "Listen to the audio and answer the user's question.",
        types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
    ]
    if use_vision and video_bytes:
        turn_content[0] = "Listen to the audio and watch the video. Answer the user's question."
        turn_content.append(types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"))

    full_text = await stream_chat_turn_response(device_id, turn_content)
    print(f"\n>>> GEMINI SAYS: {full_text}")

    await websocket.send_text(json.dumps({"status": "success", "reply": full_text}))
    return full_text


@app.websocket("/ws/stream")
async def esp32_stream_endpoint(websocket: WebSocket):
    await websocket.accept()
    stream_device_id = "default_bot"

    print("ESP32 connected to streaming endpoint!")

    # Initialize session (wake streaming + face enrollment / presence binary packets)
    active_streams[websocket] = {
        "device_id": stream_device_id,
        "wake_processor": None,
        "video_frame_buffer": deque(maxlen=WAKE_VIDEO_FRAME_BUFFER_MAX),
    }
    record_bot_seen(stream_device_id)
    await manager.broadcast(
        {
            "type": "esp32_connected",
            "device_id": stream_device_id,
            "data": "ESP32 stream connected",
        }
    )

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "runtime_vision",
                    "enabled": is_vision_enabled(stream_device_id),
                }
            )
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "runtime_timezone",
                    "timezone_rule": get_timezone_rule(stream_device_id),
                }
            )
        )
        await _send_runtime_presence_scan_to_esp32(stream_device_id)
        await _send_runtime_wake_word_to_esp32(stream_device_id, is_wake_word_enabled(stream_device_id))
    except Exception as e:
        print(f"Could not sync runtime settings to ESP32 on connect: {e}")

    def _abort_wake_video() -> None:
        sess = active_streams.get(websocket)
        if not sess:
            return
        sess["video_pre_roll"] = None
        sess["video_utterance"] = None
        sess.pop("video_wake_clip", None)

    async def _on_wake_video_capture_start() -> None:
        sess = active_streams.get(websocket)
        if not sess:
            return
        sess.pop("video_wake_clip", None)
        buf = sess.get("video_frame_buffer")
        sess["video_pre_roll"] = list(buf) if buf else []
        sess["video_utterance"] = []

    async def _on_wake_video_capture_end() -> None:
        sess = active_streams.get(websocket)
        if not sess:
            return
        pre = sess.pop("video_pre_roll", None) or []
        extra = sess.pop("video_utterance", None)
        sess["video_wake_clip"] = pre + (extra if isinstance(extra, list) else [])

    async def _handle_wake_utterance(raw_pcm: bytes) -> None:
        sess = active_streams.get(websocket)
        if not sess:
            return
        did = sess["device_id"]
        wp = sess.get("wake_processor")
        if wp:
            wp.set_paused(True)
        try:
            await websocket.send_text(json.dumps({"type": "wake_processing"}))
            await manager.broadcast(
                {
                    "type": "processing_started",
                    "device_id": did,
                    "data": "Wake utterance captured. Processing via Gemini...",
                }
            )
            video_frames = sess.pop("video_wake_clip", None) or []
            await _run_gemini_audio_turn(websocket, did, raw_pcm, video_frames)
            wp_open = sess.get("wake_processor")
            if wp_open:
                wp_open.begin_follow_up_window()
        except Exception as e:
            print(f"\nAPI Error (wake): {e}")
            error_msg = str(e)
            await manager.broadcast(
                {
                    "type": "error",
                    "device_id": did,
                    "data": error_msg,
                    "device_stream_connected": device_stream_is_live(did),
                }
            )
            try:
                await websocket.send_text(json.dumps({"status": "error", "message": error_msg}))
            except Exception:
                pass
        finally:
            if wp:
                wp.set_paused(False)

    try:
        while True:
            # Receive data from ESP32 (binary or text)
            msg = await websocket.receive()
            # Starlette: after a disconnect, further receive() calls raise — exit cleanly.
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()

            # Handle text messages from ESP32 (settings sync)
            if "text" in msg:
                try:
                    j = json.loads(msg["text"])
                    msg_type = j.get("type", "")
                    dev_id = active_streams[websocket].get("device_id", "default_bot")
                    if msg_type == "vision_changed":
                        enabled = bool(j.get("enabled", DEFAULT_VISION_ENABLED))
                        vision_enabled_by_device[dev_id] = enabled
                        settings = get_all_settings()
                        bot_conf = dict(settings.get(dev_id) or {})
                        bot_conf["vision_enabled"] = enabled
                        settings[dev_id] = bot_conf
                        save_all_settings(settings)
                        await manager.broadcast({"type": "vision_changed", "device_id": dev_id, "enabled": enabled})
                    elif msg_type == "timezone_changed":
                        tz = str(j.get("timezone_rule", DEFAULT_TIMEZONE_RULE) or DEFAULT_TIMEZONE_RULE)
                        timezone_rule_by_device[dev_id] = tz
                        hub_tz = load_hub_app_settings()
                        hub_tz["timezone_rule"] = tz
                        save_hub_app_settings(hub_tz)
                        await manager.broadcast({"type": "timezone_changed", "device_id": dev_id, "timezone_rule": tz})
                    elif msg_type == "presence_settings_changed":
                        settings = get_all_settings()
                        bot_conf = dict(settings.get(dev_id) or {})
                        bot_conf["presence_scan_enabled"] = bool(j.get("presence_scan_enabled", DEFAULT_PRESENCE_SCAN_ENABLED))
                        bot_conf["presence_scan_interval_sec"] = _clamp_int(
                            j.get("presence_scan_interval_sec"),
                            DEFAULT_PRESENCE_SCAN_INTERVAL_SEC,
                            3,
                            300,
                        )
                        bot_conf["greeting_cooldown_minutes"] = _clamp_int(
                            j.get("greeting_cooldown_minutes"),
                            DEFAULT_GREETING_COOLDOWN_MINUTES,
                            1,
                            720,
                        )
                        settings[dev_id] = bot_conf
                        save_all_settings(settings)
                        await manager.broadcast(
                            {
                                "type": "presence_settings_changed",
                                "device_id": dev_id,
                                "presence_scan_enabled": bot_conf["presence_scan_enabled"],
                                "presence_scan_interval_sec": bot_conf["presence_scan_interval_sec"],
                                "greeting_cooldown_minutes": bot_conf["greeting_cooldown_minutes"],
                            }
                        )
                except Exception as e:
                    print(f"Error parsing ESP32 text message: {e}")
                continue

            # Binary packet handling
            data = msg.get("bytes", b"")
            if not data:
                continue
                
            packet_type = data[0]
            payload = data[1:]
            
            session = active_streams[websocket]

            if packet_type == 0x10:
                # Continuous PCM for hub-side wake word + VAD (tap-to-record removed)
                if not is_wake_word_enabled(session["device_id"]):
                    continue
                wp = session.get("wake_processor")
                if wp is None:
                    try:
                        session["wake_processor"] = WakeListenProcessor(
                            on_utterance=_handle_wake_utterance,
                            on_capture_start=_on_wake_video_capture_start,
                            on_capture_end=_on_wake_video_capture_end,
                            on_capture_abort=_abort_wake_video,
                        )
                        wp = session["wake_processor"]
                        print(f"[wake] WakeListenProcessor ready for {session['device_id']!r} (model={getattr(wp, 'model_label', '?')})")
                    except Exception as ex:
                        print(f"[wake] Failed to start wake listener: {ex}")
                        continue
                await wp.feed_pcm(payload)

            elif packet_type == 0x02:
                # Idle: rolling ~1s pre-wake buffer. During utterance (wake → VAD end): append until audio stops.
                if not is_vision_enabled(session["device_id"]):
                    continue
                utter = session.get("video_utterance")
                if utter is not None:
                    if len(utter) < WAKE_VIDEO_UTTERANCE_MAX_FRAMES:
                        utter.append(payload)
                else:
                    session["video_frame_buffer"].append(payload)

            elif packet_type == 0x06:
                # Presence / face-scan snapshot (not part of record upload)
                did = session["device_id"]
                asyncio.create_task(_process_presence_jpeg(did, payload))

            elif packet_type == 0x07:
                # Enrollment reference shot from Pixel (response to request_reference_capture)
                did = session["device_id"]
                asyncio.create_task(_process_enrollment_jpeg(did, payload))

    except WebSocketDisconnect:
        print("ESP32 disconnected from streaming endpoint.")
        if websocket in active_streams:
            dev_id = active_streams[websocket].get("device_id", "default_bot")
            pending_reference_capture.pop(dev_id, None)
            del active_streams[websocket]
            # Early map send may have ACK'd on the server while Pixel never decoded it.
            # Clear so end-of-turn can push again after reconnect.
            if not active_streams:
                map_jpeg_sent_this_turn_by_device.clear()
            else:
                map_jpeg_sent_this_turn_by_device.pop(dev_id, None)
            await manager.broadcast(
                {
                    "type": "esp32_disconnected",
                    "device_id": dev_id,
                    "data": "ESP32 stream disconnected",
                }
            )
    except Exception as e:
        print(f"Stream error: {e}")
        if websocket in active_streams:
            dev_id = active_streams[websocket].get("device_id", "default_bot")
            pending_reference_capture.pop(dev_id, None)
            del active_streams[websocket]
            if not active_streams:
                map_jpeg_sent_this_turn_by_device.clear()
            else:
                map_jpeg_sent_this_turn_by_device.pop(dev_id, None)
            await manager.broadcast(
                {
                    "type": "esp32_disconnected",
                    "device_id": dev_id,
                    "data": "ESP32 stream disconnected",
                }
            )

@app.post("/api/text-command")
async def text_command(req: TextCommandRequest):
    """Receives a typed command from dashboard, streams AI reply, and forwards to ESP32."""
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not get_gemini_api_key():
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Set GEMINI_API_KEY or configure in Hub settings.",
        )

    await manager.broadcast({
        "type": "processing_started",
        "device_id": req.device_id,
        "data": "Text command received. Processing via Gemini...",
    })

    try:
        full_text = await stream_chat_turn_response(req.device_id, message)
        print(f"\n>>> GEMINI (TEXT) SAYS: {full_text}")

        esp32_ws = get_active_esp32_socket(req.device_id)
        if esp32_ws:
            await esp32_ws.send_text(json.dumps({
                "status": "success",
                "reply": full_text
            }))

        return {"status": "success", "reply": full_text}
    except Exception as e:
        error_msg = str(e)
        print(f"\nText command API Error: {error_msg}")
        await manager.broadcast(
            {
                "type": "error",
                "device_id": req.device_id,
                "data": error_msg,
                "device_stream_connected": device_stream_is_live(req.device_id),
            }
        )
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/api/text-command/reset/{device_id}")
async def reset_text_chat(device_id: str):
    """Clears the in-memory multi-turn history for a bot."""
    history_by_device.pop(device_id, None)
    return {"status": "success", "message": f"Chat history reset for {device_id}"}

@app.get("/api/runtime/{device_id}/vision")
async def get_vision_setting(device_id: str):
    """Gets whether video frames are included in model requests."""
    return {"device_id": device_id, "enabled": is_vision_enabled(device_id)}

@app.post("/api/runtime/{device_id}/vision")
async def set_vision_setting(device_id: str, payload: VisionToggleRequest):  # noqa: F811
    """Sets whether video frames are included in model requests."""
    vision_enabled_by_device[device_id] = payload.enabled
    settings = get_all_settings()
    bot_conf = dict(settings.get(device_id) or {})
    bot_conf["vision_enabled"] = bool(payload.enabled)
    settings[device_id] = bot_conf
    save_all_settings(settings)
    await _send_runtime_vision_to_esp32(device_id, payload.enabled)
    return {"status": "success", "device_id": device_id, "enabled": payload.enabled}


@app.get("/api/runtime/{device_id}/timezone")
async def get_timezone_setting(device_id: str):
    """Gets timezone rule used by the device for RTC/NTP sync."""
    return {"device_id": device_id, "timezone_rule": get_timezone_rule(device_id)}


@app.post("/api/runtime/{device_id}/timezone")
async def set_timezone_setting(device_id: str, payload: TimezoneRuleRequest):
    """Sets timezone rule used by the device for RTC/NTP sync."""
    tz = str(payload.timezone_rule or DEFAULT_TIMEZONE_RULE)
    timezone_rule_by_device[device_id] = tz
    hub = load_hub_app_settings()
    hub["timezone_rule"] = tz
    save_hub_app_settings(hub)
    await _send_runtime_timezone_to_esp32(device_id, tz)
    return {"status": "success", "device_id": device_id, "timezone_rule": tz}


@app.get("/api/hub/app-settings")
async def get_hub_app_settings_endpoint():
    """Hub-wide timezone and Maps grounding location (not per-Pixel)."""
    return load_hub_app_settings()


@app.post("/api/hub/app-settings")
async def post_hub_app_settings_endpoint(new_settings: HubAppSettingsSchema):
    """Save hub clock settings; clears all in-memory Gemini histories."""
    tz = str(new_settings.timezone_rule or DEFAULT_TIMEZONE_RULE)
    row = {"timezone_rule": tz}

    save_hub_app_settings(row)
    history_by_device.clear()
    for did in list(get_all_settings().keys()):
        timezone_rule_by_device[did] = row["timezone_rule"]
    await _push_timezone_to_all_streams(row["timezone_rule"])
    return {"status": "success", "settings": row}


@app.get("/api/hub/status")
async def api_hub_status():
    return hub_public_status()


@app.get("/api/hub/settings")
async def api_hub_settings_get():
    return hub_settings_public_view()


@app.post("/api/hub/settings")
async def api_hub_settings_post(body: HubSettingsUpdate):
    payload = body.model_dump(exclude_unset=True)
    merge_hub_secrets(payload)
    return {"status": "success", "settings": hub_settings_public_view()}


@app.get("/ping")
async def ping():
    """Health check for load balancers / Docker. Does not signal ESP32 connection (avoid false positives from periodic health probes)."""
    return {"status": "ready"}


# Optional: serve production Vite build (Docker). Register after all API/WebSocket routes.
_omnibot_static_root = (os.getenv("OMNIBOT_STATIC_ROOT") or "").strip()
if _omnibot_static_root and os.path.isdir(_omnibot_static_root):
    app.mount(
        "/",
        StaticFiles(directory=_omnibot_static_root, html=True),
        name="static",
    )

# ==========================================
#          MAIN RUNNER
# ==========================================
if __name__ == "__main__":
    _host = os.getenv("HOST", "0.0.0.0")
    _port = int(os.getenv("PORT", "8000"))
    print("--- AUDIO/VISUAL BRAIN SERVER STARTED ---")
    print(f"Listening on http://{_host}:{_port}")
    print("Waiting for ESP32 to connect to /ws/stream ...")
    uvicorn.run(app, host=_host, port=_port)