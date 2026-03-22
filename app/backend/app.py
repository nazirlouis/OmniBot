import io
import os
import uvicorn
import tempfile
import base64
import cv2
import numpy as np
import imageio
import asyncio # Added for non-blocking operations
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
import json
import re
from typing import Optional
from urllib.parse import urlencode

import requests

from semantic_pixel_router import classify_retrieval

from pydantic import BaseModel
from bleak import BleakScanner, BleakClient
import subprocess
import uuid
from functools import partial

# ==========================================
#          LOAD SECRETS & SETUP
# ==========================================
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

# Settings Configuration
SETTINGS_FILE = "bot_settings.json"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_TIMEZONE_RULE = "EST5EDT,M3.2.0/2,M11.1.0/2"
DEFAULT_VISION_ENABLED = False
DEFAULT_SYSTEM_INSTRUCTION = (
    "You are Pixel, a friendly and intelligent small desktop robot.\n\n"
    "Context:\n"
    "- If I provide audio, I am speaking directly to you through your microphone.\n"
    "- If I provide text, I am typing to you from the OmniBot Hub dashboard.\n"
    "- If I provide a video, it is a real-time recording from your onboard camera.\n\n"
    "Rules:\n"
    "- Keep your answers concise and conversational, as if we are speaking face-to-face.\n"
    "- Only discuss or analyze the video content if it is relevant to my current prompt or question.\n"
    "- Do not greet me in every message.\n"
    "- You are helpful, slightly curious, and highly efficient.\n\n"
    "Weather:\n"
    "- When I ask about weather or current conditions, use Google Search for live details when that tool is available.\n"
    "- If only Google Maps grounding is available (no Search in this session), answer from general knowledge and pick a plausible condition and temperature for the display.\n"
    "- Then call face_animation exactly once with animation set to weather and words formatted as "
    "\"<condition> <temperature_f>\", e.g. \"cloudy 74\".\n"
    "- condition must be one of: sunny, cloudy, partially_cloudy, raining, snowing.\n"
    "- Do NOT use animation speaking for weather turns.\n"
    "- Do NOT pass JSON in words; words must be plain text in the exact 2-token format above.\n"
    "- Still answer me naturally in your reply text.\n\n"
    "Face Animation:\n"
    "- For most conversational replies, call face_animation exactly once before or during your text response.\n"
    "- Use animation speaking for conversational face reactions.\n"
    "- Use animation happy for a smiling half-cut eye look.\n"
    "- Use animation mad for an angry half-cut eye look (top of eyes hidden).\n"
    "- Use animation weather only for weather display mode.\n"
    "- For speaking/happy/mad, pass at most two words shown with the face (e.g. \"hello\" or \"got it\").\n"
    "- Never call face_animation more than once in the same response turn."
)

WEATHER_DISPLAY_MS = 5000
FACE_ANIMATION_DISPLAY_MS = 2500

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_NOMINATIM_USER_AGENT = (
    "OmnibotHub/1.0 (set NOMINATIM_USER_AGENT in .env - Nominatim usage policy)"
)


def _nominatim_user_agent_header() -> str:
    """HTTP headers must be latin-1; strip/replace non-ASCII (e.g. from .env)."""
    raw = (os.getenv("NOMINATIM_USER_AGENT") or "").strip() or DEFAULT_NOMINATIM_USER_AGENT
    ascii_only = raw.encode("ascii", "replace").decode("ascii").strip()
    return (ascii_only[:200] if ascii_only else None) or "OmnibotHub/1.0"


def _maps_debug_enabled() -> bool:
    return os.getenv("OMNIBOT_MAPS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def _maps_debug(msg: str) -> None:
    if _maps_debug_enabled():
        print(f"[Omnibot/maps-debug] {msg}")


def _route_debug_enabled() -> bool:
    return os.getenv("OMNIBOT_ROUTE_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ) or _maps_debug_enabled()


def _route_debug(msg: str) -> None:
    if _route_debug_enabled():
        print(f"[Omnibot/route-debug] {msg}")


def _routing_text_from_message_content(message_content) -> str:
    """Text used only for semantic Maps vs Search routing (not sent to the model)."""
    if isinstance(message_content, str):
        return message_content.strip()
    if isinstance(message_content, list):
        parts: list[str] = []
        for item in message_content:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    parts.append(s)
        return " ".join(parts).strip()
    return ""

# Event loop used to push WebSocket messages from sync Gemini tool invocations (worker threads).
_main_async_loop: Optional[asyncio.AbstractEventLoop] = None

# OpenAPI-style declaration matching Gemini function-calling docs
# (https://ai.google.dev/gemini-api/docs/function-calling — combine with Google Search).
# The executable implementation is `_make_show_weather_tool` (automatic function calling).
SHOW_WEATHER_FUNCTION_DECLARATION = {
    "name": "show_weather",
    "description": (
        "Shows weather on the robot round display for several seconds. "
        "Call when the user asks about weather or conditions; use available grounding (Search or Maps) or best judgment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "condition": {
                "type": "string",
                "enum": ["sunny", "cloudy", "partially_cloudy", "raining", "snowing"],
                "description": "Sky / precipitation style for the on-device icon.",
            },
            "temperature": {
                "type": "number",
                "description": "Air temperature in degrees Fahrenheit (numeric only, e.g. 72).",
            },
        },
        "required": ["condition", "temperature"],
    },
}

FACE_ANIMATION_FUNCTION_DECLARATION = {
    "name": "face_animation",
    "description": (
        "Animates Pixel's speaking face on the round display. "
        "Pass a short words caption (at most two words). Display duration is fixed on the device."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "animation": {
                "type": "string",
                "enum": ["speaking", "happy", "mad", "weather"],
                "description": "Display mode on Pixel: speaking, happy, mad, or weather overlay.",
            },
            "words": {
                "type": "string",
                "description": (
                    "For speaking/happy/mad: at most two words (e.g. \"hello\", \"nice one\"). "
                    "For weather: \"<condition> <temperature_f>\" (e.g. \"cloudy 74\")."
                ),
            },
        },
        "required": ["animation", "words"],
    },
}

# Per-device, per-turn tool usage guardrails.
turn_tool_state_by_device: dict[str, dict[str, bool]] = {}

if not API_KEY:
    print("ERROR: API Key not found in .env file!")
    exit()

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

def get_bot_settings(device_id: str):
    settings = get_all_settings()
    bot_conf = settings.get(device_id, {})
    return {
        "model": bot_conf.get("model", DEFAULT_MODEL),
        "system_instruction": bot_conf.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION),
        "timezone_rule": bot_conf.get("timezone_rule", DEFAULT_TIMEZONE_RULE),
        "vision_enabled": bool(bot_conf.get("vision_enabled", DEFAULT_VISION_ENABLED)),
        "maps_grounding_enabled": bool(bot_conf.get("maps_grounding_enabled", False)),
        "maps_postal_code": (bot_conf.get("maps_postal_code") or "").strip(),
        "maps_country": (bot_conf.get("maps_country") or "").strip(),
        "maps_latitude": bot_conf.get("maps_latitude"),
        "maps_longitude": bot_conf.get("maps_longitude"),
        "maps_display_name": bot_conf.get("maps_display_name"),
    }


def _nominatim_geocode_postal(postal: str, country: str) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Resolve postal/ZIP + country via public Nominatim. Returns (lat, lon, display_name, error)."""
    pc = (postal or "").strip()
    cc = (country or "").strip()
    if not pc:
        return None, None, None, "Enter a postal or ZIP code."
    if not cc:
        return None, None, None, "Select a country."
    # ISO 3166-1 alpha-2 (e.g. US) works well; longer strings treated as country name for Nominatim.
    if len(cc) == 2 and cc.isalpha():
        q = f"{pc}, {cc.upper()}"
    else:
        q = f"{pc}, {cc}"
    ua = _nominatim_user_agent_header()
    headers = {"User-Agent": ua, "Accept": "application/json"}
    try:
        params = urlencode({"q": q, "format": "json", "limit": "1"})
        url = f"{NOMINATIM_SEARCH_URL}?{params}"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None, None, None, f'No results found for "{q}".'
        hit = data[0]
        lat = float(hit["lat"])
        lon = float(hit["lon"])
        disp = (hit.get("display_name") or "").strip() or q
        return lat, lon, disp, None
    except Exception as e:
        return None, None, None, str(e)


def _extract_maps_sources_from_grounding_metadata(gm) -> list[dict]:
    """Build link list for hub UI (Google Maps grounding attribution)."""
    if gm is None:
        return []
    chunks = getattr(gm, "grounding_chunks", None) or []
    by_uri: dict[str, dict] = {}
    for ch in chunks:
        maps = getattr(ch, "maps", None)
        if maps is None:
            continue
        uri = getattr(maps, "uri", None) or getattr(maps, "google_maps_uri", None)
        if not uri:
            continue
        title = (getattr(maps, "title", None) or "").strip() or "Place"
        by_uri[str(uri)] = {"title": title, "uri": str(uri)}
    return list(by_uri.values())

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

client = genai.Client(api_key=API_KEY)
app = FastAPI(title="ESP32 Gemini Brain Monitor")


def _google_search_builtin():
    """Built-in Google Search tool; docs use types.ToolGoogleSearch() when available."""
    ctor = getattr(types, "ToolGoogleSearch", None)
    if ctor is not None:
        return ctor()
    return types.GoogleSearch()


def _google_maps_builtin():
    """Built-in Google Maps grounding tool (Gemini API)."""
    ctor = getattr(types, "ToolGoogleMaps", None)
    if ctor is not None:
        return ctor()
    return types.GoogleMaps()


def _pixel_chat_generate_config(
    *,
    system_instruction,
    device_id: str,
    prefer_maps: bool = False,
) -> types.GenerateContentConfig:
    """Tools + server-side tool flag per Gemini 3 multi-tool docs.

    The Gemini API does not allow ``google_maps`` and ``google_search`` in the same
    request (400 INVALID_ARGUMENT). When ``prefer_maps`` is true and Maps grounding
    is enabled with stored coordinates, we attach only ``google_maps`` +
    ``face_animation``; otherwise ``google_search`` + ``face_animation``.
    """
    bot = get_bot_settings(device_id)
    face_animation_fn = _make_face_animation_tool(device_id)
    use_maps = bool(bot.get("maps_grounding_enabled"))
    lat, lng = bot.get("maps_latitude"), bot.get("maps_longitude")
    maps_tool_active = prefer_maps and use_maps and lat is not None and lng is not None
    if prefer_maps and use_maps and (lat is None or lng is None) and _maps_debug_enabled():
        _maps_debug(
            "Semantic router chose Maps for this turn, but lat/lng are missing — using Google Search instead."
        )

    if maps_tool_active:
        tools = [
            types.Tool(google_maps=_google_maps_builtin()),
            face_animation_fn,
        ]
    else:
        tools = [
            types.Tool(google_search=_google_search_builtin()),
            face_animation_fn,
        ]

    tool_cfg_kwargs: dict = {"include_server_side_tool_invocations": True}
    if maps_tool_active:
        tool_cfg_kwargs["retrieval_config"] = types.RetrievalConfig(
            lat_lng=types.LatLng(latitude=float(lat), longitude=float(lng)),
        )
    if _maps_debug_enabled():
        tool_slots = []
        for i, t in enumerate(tools):
            if getattr(t, "google_maps", None) is not None:
                tool_slots.append(f"{i}:google_maps")
            elif getattr(t, "google_search", None) is not None:
                tool_slots.append(f"{i}:google_search")
            else:
                tool_slots.append(f"{i}:function_or_other")
        ll_info = "none"
        rc = tool_cfg_kwargs.get("retrieval_config")
        if rc is not None:
            ll = getattr(rc, "lat_lng", None)
            if ll is not None:
                ll_info = f"lat={getattr(ll, 'latitude', None)!r} lng={getattr(ll, 'longitude', None)!r}"
        _maps_debug(
            f"GenerateContentConfig device_id={device_id!r} "
            f"prefer_maps={prefer_maps} maps_grounding_enabled={use_maps} "
            f"postal={bot.get('maps_postal_code')!r} country={bot.get('maps_country')!r} "
            f"stored_lat={lat!r} stored_lng={lng!r} maps_tool_and_retrieval_active={maps_tool_active} "
            f"tools=[{', '.join(tool_slots)}] retrieval_lat_lng={ll_info}"
        )
        if use_maps and (lat is None or lng is None):
            _maps_debug(
                "Maps is ON in settings but lat/lng are missing — geocode may have failed on last save, "
                "or settings file has no coordinates. Fix postal/country and save, or check hub geocode error."
            )
        if maps_tool_active:
            _maps_debug("Built-in retrieval: google_maps only (google_search omitted — API forbids both).")
        else:
            _maps_debug("Built-in retrieval: google_search only (Maps off or no coordinates).")

    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        tools=tools,
        tool_config=types.ToolConfig(**tool_cfg_kwargs),
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
    try:
        while True:
            # We don't expect the frontend to send us anything, but we keep the loop open
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

class WifiCredentials(BaseModel):
    ssid: str
    password: str
    device_address: str

class BotSettingsSchema(BaseModel):
    model: str
    system_instruction: str
    timezone_rule: str = DEFAULT_TIMEZONE_RULE
    vision_enabled: bool = DEFAULT_VISION_ENABLED
    maps_grounding_enabled: bool = False
    maps_postal_code: str = ""
    maps_country: str = ""
    maps_latitude: Optional[float] = None
    maps_longitude: Optional[float] = None
    maps_display_name: Optional[str] = None

class TextCommandRequest(BaseModel):
    message: str
    device_id: str = "default_bot"

class VisionToggleRequest(BaseModel):
    enabled: bool


class TimezoneRuleRequest(BaseModel):
    timezone_rule: str


# Use a standard BLE UUID for our custom generic characteristic
WIFI_CREDS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef0"

# ==========================================
#          API ENDPOINTS (ESP32 SETUP)
# ==========================================
@app.get("/setup/scan")
async def setup_scan():
    """Scans for nearby BLE devices and returns those that might be the Desktop Bot."""
    print("BLE Scanning...")
    devices = await BleakScanner.discover(timeout=5.0)
    results = []
    
    for d in devices:
        # Look for a specific name prefix
        if d.name and "Pixel" in d.name:
            results.append({"name": d.name, "address": d.address})
            
    # For testing without the ESP32 code ready, return a fake device if none found
    if not results:
        results.append({"name": "DesktopBot_Setup (Simulated)", "address": "00:00:00:00:00:00"})
        
    return {"devices": results}

@app.post("/setup/provision")
async def setup_provision(creds: WifiCredentials):
    """Connects to the ESP32 via BLE and writes the WiFi SSID/Password."""
    payload = json.dumps({"ssid": creds.ssid, "password": creds.password}).encode('utf-8')
    print(f"Provisioning {creds.device_address} with SSID {creds.ssid}...")
    
    # Simulate success if using the fake device
    if creds.device_address == "00:00:00:00:00:00":
        return {"status": "success", "message": "Simulated provisioning complete."}
        
    try:
        async with BleakClient(creds.device_address) as client:
            await client.write_gatt_char(WIFI_CREDS_CHAR_UUID, payload)
        return {"status": "success", "message": "Credentials sent successfully."}
    except Exception as e:
        error_msg = str(e)
        print(f"BLE Provisioning Error: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/setup/wifi-networks")
async def setup_wifi_networks():
    """Scans for nearby Wi-Fi networks using the host OS (Windows)."""
    try:
        out = subprocess.run(["netsh", "wlan", "show", "networks"], capture_output=True, text=True).stdout
        networks = []
        for line in out.split('\n'):
            if 'SSID' in line and ':' in line:
                ssid = line.split(':', 1)[1].strip()
                if ssid and ssid not in networks:
                    networks.append(ssid)
        return {"networks": networks}
    except Exception as e:
        print(f"Wi-Fi Scan Error: {e}")
        return {"networks": []}

# ==========================================
#          API ENDPOINTS (SETTINGS)
# ==========================================
@app.get("/api/settings/{device_id}")
async def get_settings(device_id: str):
    """Gets the current settings for a specific bot."""
    return get_bot_settings(device_id)

@app.post("/api/settings/{device_id}")
async def update_settings(device_id: str, new_settings: BotSettingsSchema):
    """Updates the settings for a specific bot."""
    settings = get_all_settings()
    prev = settings.get(device_id, {})

    maps_geocode: dict = {"ok": True, "error": None}
    postal = (new_settings.maps_postal_code or "").strip()
    country = (new_settings.maps_country or "").strip()
    maps_enabled = bool(new_settings.maps_grounding_enabled)

    row = {
        "model": new_settings.model,
        "system_instruction": new_settings.system_instruction,
        "timezone_rule": new_settings.timezone_rule or DEFAULT_TIMEZONE_RULE,
        "vision_enabled": bool(new_settings.vision_enabled),
        "maps_grounding_enabled": maps_enabled,
        "maps_postal_code": postal,
        "maps_country": country,
    }

    if maps_enabled:
        lat, lon, disp, err = _nominatim_geocode_postal(postal, country)
        if err:
            maps_geocode = {"ok": False, "error": err}
            row["maps_latitude"] = None
            row["maps_longitude"] = None
            row["maps_display_name"] = None
        else:
            row["maps_latitude"] = lat
            row["maps_longitude"] = lon
            row["maps_display_name"] = disp
    else:
        row["maps_latitude"] = prev.get("maps_latitude")
        row["maps_longitude"] = prev.get("maps_longitude")
        row["maps_display_name"] = prev.get("maps_display_name")

    settings[device_id] = row
    save_all_settings(settings)
    history_by_device.pop(device_id, None)
    vision_enabled_by_device[device_id] = settings[device_id]["vision_enabled"]
    timezone_rule_by_device[device_id] = settings[device_id]["timezone_rule"]
    await _send_runtime_vision_to_esp32(device_id, settings[device_id]["vision_enabled"])
    await _send_runtime_timezone_to_esp32(device_id, settings[device_id]["timezone_rule"])
    if _maps_debug_enabled():
        sd = settings[device_id]
        _maps_debug(
            f"settings_saved device_id={device_id!r} maps_enabled={maps_enabled} maps_geocode={maps_geocode!r} "
            f"stored lat/lng=({sd.get('maps_latitude')!r}, {sd.get('maps_longitude')!r}) "
            f"display_name={sd.get('maps_display_name')!r} in_memory_history_cleared=True"
        )
    return {"status": "success", "settings": settings[device_id], "maps_geocode": maps_geocode}

# ==========================================
#          ESP32 WEBSOCKET STREAMING
# ==========================================
# Store active streaming sessions by ESP32 WebSocket connection
active_streams = {}
# Curated multi-turn history per bot (types.Content list); rebuilt into each one-shot Chat per turn.
history_by_device: dict[str, list] = {}
gemini_turn_locks: dict[str, asyncio.Lock] = {}
vision_enabled_by_device = {}
timezone_rule_by_device = {}


def _make_show_weather_tool(device_id: str):
    """Builder so show_weather is bound to the correct bot device."""

    allowed = frozenset(SHOW_WEATHER_FUNCTION_DECLARATION["parameters"]["properties"]["condition"]["enum"])

    def show_weather(condition: str, temperature: float) -> dict:
        """Pushes a weather graphic to Pixel's round display for several seconds.

        Same contract as SHOW_WEATHER_FUNCTION_DECLARATION (enum + Fahrenheit).
        """
        c = (condition or "").strip().lower().replace(" ", "_").replace("-", "_")
        if c not in allowed:
            c = "cloudy"
        tool_state = turn_tool_state_by_device.setdefault(device_id, {"show_weather": False, "face_animation": False})
        if tool_state.get("face_animation", False):
            return {"ok": False, "skipped": True, "reason": "face_animation already called this turn"}
        tool_state["show_weather"] = True
        _schedule_weather_to_esp32(device_id, c, float(temperature))
        _broadcast_tool_call_to_frontend(
            "show_weather",
            {
                "condition": c,
                "temperature": float(temperature),
            },
        )
        return {"ok": True, "display": "weather queued on robot"}

    return show_weather


def _clamp_face_animation_words(raw: str) -> str:
    parts = (raw or "").strip().split()
    return " ".join(parts[:2])


def _normalize_weather_words(raw: str) -> str:
    allowed = frozenset(SHOW_WEATHER_FUNCTION_DECLARATION["parameters"]["properties"]["condition"]["enum"])
    text = (raw or "").strip().lower().replace("-", "_")
    condition_match = re.search(r"\b(sunny|cloudy|partially_cloudy|raining|snowing)\b", text)
    temp_match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    condition = condition_match.group(1) if condition_match else "cloudy"
    if condition not in allowed:
        condition = "cloudy"
    temp = int(float(temp_match.group(1))) if temp_match else 72
    return f"{condition} {temp}"


def _make_face_animation_tool(device_id: str):
    """Builder so face_animation is bound to the correct bot device."""

    allowed = frozenset(
        FACE_ANIMATION_FUNCTION_DECLARATION["parameters"]["properties"]["animation"]["enum"]
    )

    def face_animation(animation: str, words: str = "") -> dict:
        a = (animation or "").strip().lower()
        if a not in allowed:
            a = "speaking"
        raw_words = words or ""

        # Safety: if weather-like payload arrives while animation is speaking, reroute.
        if a == "speaking":
            weather_guess = _normalize_weather_words(raw_words)
            if weather_guess != "cloudy 72" or any(
                token in raw_words.lower() for token in ["condition", "temperature", "sunny", "cloudy", "raining", "snowing"]
            ):
                if any(t in raw_words.lower() for t in ["sunny", "cloudy", "partially", "raining", "snowing", "condition", "temperature"]):
                    a = "weather"

        w = _normalize_weather_words(raw_words) if a == "weather" else _clamp_face_animation_words(raw_words)

        tool_state = turn_tool_state_by_device.setdefault(device_id, {"show_weather": False, "face_animation": False})
        if tool_state.get("face_animation", False):
            return {"ok": False, "skipped": True, "reason": "face_animation already called this turn"}
        tool_state["face_animation"] = True

        _schedule_face_animation_to_esp32(device_id, a, w)
        _broadcast_tool_call_to_frontend(
            "face_animation",
            {
                "animation": a,
                "words": w,
                "duration_ms": WEATHER_DISPLAY_MS if a == "weather" else FACE_ANIMATION_DISPLAY_MS,
            },
        )
        return {"ok": True, "display": "face animation queued on robot"}

    return face_animation


async def _send_weather_json_to_esp32(device_id: str, condition: str, temperature: float) -> None:
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    payload = {
        "type": "show_weather",
        "condition": condition,
        "temperature": temperature,
        "duration_ms": WEATHER_DISPLAY_MS,
    }
    try:
        await ws.send_text(json.dumps(payload))
    except Exception as e:
        print(f"Failed to send show_weather to ESP32: {e}")


def _schedule_weather_to_esp32(device_id: str, condition: str, temperature: float) -> None:
    loop = _main_async_loop
    if loop is None:
        print("show_weather: no async loop yet; skipping robot display")
        return
    asyncio.run_coroutine_threadsafe(
        _send_weather_json_to_esp32(device_id, condition, temperature),
        loop,
    )


async def _send_face_animation_json_to_esp32(device_id: str, animation: str, words: str) -> None:
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    payload = {
        "type": "face_animation",
        "animation": animation,
        "duration_ms": WEATHER_DISPLAY_MS if animation == "weather" else FACE_ANIMATION_DISPLAY_MS,
        "words": words,
    }
    try:
        await ws.send_text(json.dumps(payload))
    except Exception as e:
        print(f"Failed to send face_animation to ESP32: {e}")


def _schedule_face_animation_to_esp32(device_id: str, animation: str, words: str) -> None:
    loop = _main_async_loop
    if loop is None:
        print("face_animation: no async loop yet; skipping robot display")
        return
    asyncio.run_coroutine_threadsafe(
        _send_face_animation_json_to_esp32(device_id, animation, words),
        loop,
    )

def _broadcast_tool_call_to_frontend(function_name: str, arguments: dict) -> None:
    """Broadcast tool-call telemetry to the dashboard websocket monitor.

    This runs from Gemini tool execution threads, so we use the shared
    `_main_async_loop` to schedule the async broadcast.
    """
    loop = _main_async_loop
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({
                "type": "tool_call",
                "function_name": function_name,
                "arguments": arguments,
            }),
            loop,
        )
    except Exception as e:
        print(f"Failed to broadcast tool_call to frontend: {e}")


def is_vision_enabled(device_id: str) -> bool:
    if device_id in vision_enabled_by_device:
        return bool(vision_enabled_by_device[device_id])
    return bool(get_bot_settings(device_id).get("vision_enabled", DEFAULT_VISION_ENABLED))


async def _send_runtime_vision_to_esp32(device_id: str, enabled: bool) -> None:
    """Pushes vision/capture setting to Pixel so firmware can skip camera work when off."""
    ws = get_active_esp32_socket(device_id)
    if not ws:
        return
    try:
        await ws.send_text(json.dumps({"type": "runtime_vision", "enabled": bool(enabled)}))
    except Exception as e:
        print(f"Failed to send runtime_vision to ESP32: {e}")


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




async def stream_chat_turn_response(device_id: str, message_content):
    """Streams one Gemini turn using local history and a fresh config each request (one-shot Chat)."""
    global _main_async_loop
    _main_async_loop = asyncio.get_running_loop()

    stream_id = str(uuid.uuid4())
    full_text = ""
    loop = asyncio.get_running_loop()
    first_token_notified = False
    last_maps_sources: list[dict] = []

    await manager.broadcast({
        "type": "ai_response_stream_start",
        "stream_id": stream_id
    })

    lock = gemini_turn_locks.setdefault(device_id, asyncio.Lock())
    async with lock:
        turn_tool_state_by_device[device_id] = {"show_weather": False, "face_animation": False}
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
        routing_text = _routing_text_from_message_content(message_content)
        prefer_maps = False
        if routing_text:
            retrieval = await asyncio.to_thread(classify_retrieval, routing_text)
            prefer_maps = retrieval == "maps"
        if _route_debug_enabled():
            snippet = routing_text if len(routing_text) <= 200 else routing_text[:200] + "..."
            bot = bot_settings
            lat_ok = bot.get("maps_latitude") is not None and bot.get("maps_longitude") is not None
            will_maps = (
                prefer_maps
                and bool(bot.get("maps_grounding_enabled"))
                and lat_ok
            )
            _route_debug(
                f"device_id={device_id!r} routing_text={snippet!r} "
                f"classified_prefer_maps={prefer_maps} effective_builtin_retrieval="
                f"{'google_maps' if will_maps else 'google_search'}"
            )
        config = _pixel_chat_generate_config(
            system_instruction=system_instruction,
            device_id=device_id,
            prefer_maps=prefer_maps,
        )
        chat = client.chats.create(
            model=model,
            config=config,
            history=list(prior_history),
        )
        response_stream = chat.send_message_stream(message_content)
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
                    if chunk.candidates:
                        for candidate in chunk.candidates:
                            gm = getattr(candidate, "grounding_metadata", None)
                            src = _extract_maps_sources_from_grounding_metadata(gm)
                            if src:
                                last_maps_sources = src
                            if candidate.content and candidate.content.parts:
                                for part in candidate.content.parts:
                                    fc = getattr(part, "function_call", None)
                                    if fc:
                                        await manager.broadcast({
                                            "type": "tool_call",
                                            "stream_id": stream_id,
                                            "function_name": fc.name,
                                            "arguments": dict(fc.args) if fc.args else {}
                                        })
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

            history_by_device[device_id] = list(chat.get_history(curated=True))
        finally:
            turn_tool_state_by_device.pop(device_id, None)

    if _maps_debug_enabled():
        _maps_debug(
            f"turn_end stream_id={stream_id!r} maps_grounding_chunks={len(last_maps_sources)} "
            f"(0 = model did not return Maps sources this turn; it may skip the tool for non-local prompts)"
        )
        if last_maps_sources:
            _maps_debug(f"maps source titles: {[s.get('title') for s in last_maps_sources[:5]]}")

    end_msg = {
        "type": "ai_response_stream_end",
        "stream_id": stream_id,
        "data": full_text,
    }
    if last_maps_sources:
        end_msg["maps_sources"] = last_maps_sources
    await manager.broadcast(end_msg)

    return full_text

def get_active_esp32_socket(device_id: str):
    """Returns an active ESP32 WebSocket for the requested bot, if available."""
    for ws, session in active_streams.items():
        if session.get("device_id") == device_id:
            return ws
    return next(iter(active_streams), None)

@app.websocket("/ws/stream")
async def esp32_stream_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("ESP32 connected to streaming endpoint!")
    
    # Initialize session buffers
    stream_device_id = "default_bot"
    active_streams[websocket] = {
        "audio_buffer": bytearray(),
        "video_frames": [],
        "device_id": stream_device_id,
    }
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
    except Exception as e:
        print(f"Could not sync runtime settings to ESP32 on connect: {e}")

    try:
        while True:
            # Receive data from ESP32 (binary or text)
            msg = await websocket.receive()

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
                        await manager.broadcast({"type": "timezone_changed", "device_id": dev_id, "timezone_rule": tz})
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
            
            if packet_type == 0x01:
                # Audio chunk
                session["audio_buffer"].extend(payload)
                
            elif packet_type == 0x02:
                # Video frame (JPEG)
                session["video_frames"].append(payload)
                
            elif packet_type == 0x03:
                # Stop Recording & Process Command
                print(f"\n--- STOP RECORDING RECEIVED ---")
                print(f"Collected {len(session['audio_buffer'])} bytes of audio and {len(session['video_frames'])} video frames.")
                
                # Broadcast to UI that processing started
                await manager.broadcast({
                    "type": "processing_started",
                    "data": "Stream finished. Processing via Gemini..."
                })
                
                try:
                    use_vision = is_vision_enabled(session["device_id"])

                    # 1. Create Audio Bytes with WAV Header
                    raw_audio = bytes(session["audio_buffer"])
                    wav_header = create_wav_header(len(raw_audio), sample_rate=16000)
                    audio_bytes = wav_header + raw_audio

                    # 2. Broadcast Audio to React UI
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    audio_data_url = f"data:audio/wav;base64,{audio_b64}"
                    await manager.broadcast({
                        "type": "audio_captured",
                        "data": audio_data_url
                    })

                    # 3. Optionally assemble video and send to UI/model
                    video_bytes = b""
                    if use_vision:
                        print("Assembling video in background...", end=" ", flush=True)
                        video_bytes = await asyncio.to_thread(assemble_video, session["video_frames"], 10)
                        print(f"Done! ({len(video_bytes)} bytes)")

                        if video_bytes:
                            video_b64 = base64.b64encode(video_bytes).decode('utf-8')
                            video_data_url = f"data:video/mp4;base64,{video_b64}"
                            await manager.broadcast({
                                "type": "video_captured",
                                "data": video_data_url
                            })

                    # 4. Call Gemini (local history + fresh config each turn)
                    print("Sending to Gemini...", end=" ", flush=True)
                    turn_content = [
                        "Listen to the audio and answer the user's question.",
                        types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                    ]
                    if use_vision and video_bytes:
                        turn_content[0] = "Listen to the audio and watch the video. Answer the user's question."
                        turn_content.append(types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"))

                    full_text = await stream_chat_turn_response(
                        session["device_id"],
                        turn_content
                    )

                    print(f"\n>>> GEMINI SAYS: {full_text}")

                    # Send Response back to ESP32 via same WebSocket
                    await websocket.send_text(json.dumps({
                        "status": "success",
                        "reply": full_text
                    }))
                    
                except Exception as e:
                    print(f"\nAPI Error: {e}")
                    error_msg = str(e)
                    await manager.broadcast({"type": "error", "data": error_msg})
                    await websocket.send_text(json.dumps({"status": "error", "message": error_msg}))
                
                # Clear buffers for next interaction
                session["audio_buffer"].clear()
                session["video_frames"].clear()
                
    except WebSocketDisconnect:
        print("ESP32 disconnected from streaming endpoint.")
        if websocket in active_streams:
            dev_id = active_streams[websocket].get("device_id", "default_bot")
            del active_streams[websocket]
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
            del active_streams[websocket]
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

    await manager.broadcast({
        "type": "processing_started",
        "data": "Text command received. Processing via Gemini..."
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
        await manager.broadcast({"type": "error", "data": error_msg})
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
    await _send_runtime_timezone_to_esp32(device_id, tz)
    return {"status": "success", "device_id": device_id, "timezone_rule": tz}

@app.get("/ping")
async def ping():
    """A simple health check. Using this also notifies the UI that ESP32 is online."""
    await manager.broadcast({
        "type": "esp32_connected",
        "data": "ESP32 is online"
    })
    return {"status": "ready"}

# ==========================================
#          MAIN RUNNER
# ==========================================
if __name__ == "__main__":
    print(f"--- AUDIO/VISUAL BRAIN SERVER STARTED ---")
    print("Listening on http://0.0.0.0:8000")
    print("Waiting for ESP32 to connect to /ws/stream ...")
    uvicorn.run(app, host="0.0.0.0", port=8000)