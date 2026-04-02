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
from typing import Any, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from semantic_pixel_router import classify_retrieval_with_reason

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
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_TIMEZONE_RULE = "EST5EDT,M3.2.0/2,M11.1.0/2"
DEFAULT_VISION_ENABLED = False
DEFAULT_SYSTEM_INSTRUCTION = (
    "You control a small desktop robot named Pixel with a round face display.\n\n"
    "Animation tools:\n"
    "- Use the `show_face_animation` / `face_animation` tool for conversational or emotional states only "
    "(animation = speaking, happy, or mad). For these, pass at most two short words that summarize your "
    "overall reply (e.g. \"thanks\", \"nice one\").\n"
    "- Use the `show_weather_animation` tool only when the user asks about weather or conditions. Represent "
    "the weather as a condition plus numeric Fahrenheit temperature.\n"
    "- ALWAYS call `show_map_animation` whenever you answer any question about directions, navigation, "
    "nearby places, or specific businesses — regardless of whether you used Google Maps grounding.\n"
    "  * Use display_style='directions' when the user asks how to get somewhere or needs navigation. "
    "Pass the destination street address in the location field.\n"
    "  * Use display_style='calling_card' when the user asks about a specific place or business. "
    "Pass the BUSINESS NAME and city (e.g. 'AMC Parkway Pointe 15, Atlanta GA') NOT a street address.\n\n"
    "At most one animation tool should be used per turn. Do not call more than one of: "
    "`face_animation`, `show_face_animation`, `show_weather_animation`, or `show_map_animation` in the same turn.\n\n"
    "When asked about food, activities, or places near me, provide exactly ONE recommendation and do not list multiple options."
)

WEATHER_DISPLAY_MS = 5000
FACE_ANIMATION_DISPLAY_MS = 2500
MAP_DISPLAY_MS = 9000

# Calling card full-bleed square for round display (must match Pixel defaults / decode size)
CALLING_CARD_PHOTO_W = 240
CALLING_CARD_PHOTO_H = 240
CALLING_CARD_NAME_MAX = 80
CALLING_CARD_ADDRESS_MAX = 120
CALLING_CARD_CATEGORY_MAX = 48

# links2004/WebSockets defaults to WEBSOCKETS_MAX_DATA_SIZE = 15KB on ESP32; binary frames must fit.
CALLING_CARD_JPEG_MAX_BYTES = 14 * 1024

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

# OpenAPI-style declarations matching Gemini function-calling docs
# (https://ai.google.dev/gemini-api/docs/function-calling — combine with Google Search).
# The executable implementations are `_make_show_weather_tool` and `_make_show_map_animation_tool`
# (automatic function calling).
SHOW_WEATHER_FUNCTION_DECLARATION = {
    "name": "show_weather_animation",
    "description": (
        "Shows weather on the robot round display for several seconds. "
        "You MUST call this function EVERY TIME after you use Google Search to retrieve the current weather. Do NOT answer a weather question without calling this function immediately after checking the search results."
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

SHOW_MAP_ANIMATION_FUNCTION_DECLARATION = {
    "name": "show_map_animation",
    "description": (
        "Shows a map on the robot round display for several seconds. "
        "You MUST call this function EVERY TIME after you use Google Maps to lookup locations, directions, or places. "
        "Do NOT answer a location or navigation question without calling this function immediately after checking the map results. "
        "Always pass the specific address or place name in the 'location' parameter so the correct location is shown on the robot display. "
        "Choose display_style='directions' for navigation/routing questions, or display_style='calling_card' for general place/business lookups."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "For display_style='calling_card': pass the BUSINESS NAME and city (e.g. 'AMC Parkway Pointe 15, Atlanta GA' or 'Publix Paces Ferry Center, Atlanta'), NOT a raw street address — the business name allows the photo to be fetched correctly. For display_style='directions': a street address or place name is fine (e.g. '3101 Cobb Pkwy SE, Atlanta, GA').",
            },
            "display_style": {
                "type": "string",
                "enum": ["calling_card", "directions"],
                "description": (
                    "How to render the map on the robot display. "
                    "Use 'directions' when the user asks how to get somewhere or needs turn-by-turn navigation — shows a route from their home to the destination. "
                    "Use 'calling_card' when the user asks generally about a place, business, or landmark — shows the place name, address, photo, and review score."
                ),
            },
        },
        "required": ["location", "display_style"],
    },
}

FACE_ANIMATION_FUNCTION_DECLARATION = {
    "name": "face_animation",
    "description": (
        "Animates Pixel's face on the round display for conversational or emotional states only. "
        "For speaking/happy/mad, pass at most two short words that summarize the overall reply "
        "(e.g. \"thanks\", \"nice one\")."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "animation": {
                "type": "string",
                "enum": ["speaking", "happy", "mad"],
                "description": (
                    "Display mode: speaking, happy, or mad. Do not use this tool for weather or maps."
                ),
            },
            "words": {
                "type": "string",
                "description": (
                    "For speaking/happy/mad: at most two words (e.g. \"hello\", \"nice one\")."
                ),
            },
        },
        "required": ["animation", "words"],
    },
}

# Per-device, per-turn tool usage guardrails.
turn_tool_state_by_device: dict[str, dict[str, bool]] = {}

# Latest Maps widget context token seen while streaming (for face_animation(map) -> screenshot).
maps_widget_token_latest_by_device: dict[str, str] = {}
# Avoid sending duplicate map JPEGs in the same turn (face_animation path vs end-of-turn fallback).
map_jpeg_sent_this_turn_by_device: dict[str, bool] = {}
# Store explicit location from show_map_animation tool call for end-of-turn map rendering.
map_location_override_by_device: dict[str, str] = {}
# Store display_style from show_map_animation tool call for end-of-turn rendering.
map_display_style_by_device: dict[str, str] = {}

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
        "maps_street": (bot_conf.get("maps_street") or "").strip(),
        "maps_state": (bot_conf.get("maps_state") or "").strip(),
        "maps_postal_code": (bot_conf.get("maps_postal_code") or "").strip(),
        "maps_country": (bot_conf.get("maps_country") or "").strip(),
        "maps_latitude": bot_conf.get("maps_latitude"),
        "maps_longitude": bot_conf.get("maps_longitude"),
        "maps_display_name": bot_conf.get("maps_display_name"),
    }


def _default_stored_bot_settings() -> dict:
    """Persisted row for a bot after reset-to-default (matches app defaults, clears maps)."""
    return {
        "model": DEFAULT_MODEL,
        "system_instruction": DEFAULT_SYSTEM_INSTRUCTION,
        "timezone_rule": DEFAULT_TIMEZONE_RULE,
        "vision_enabled": DEFAULT_VISION_ENABLED,
        "maps_grounding_enabled": False,
        "maps_street": "",
        "maps_state": "",
        "maps_postal_code": "",
        "maps_country": "",
        "maps_latitude": None,
        "maps_longitude": None,
        "maps_display_name": None,
    }


def _geocode_address(street: str, state: str, postal: str, country: str) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """Resolve location via Google Maps Geocoding or fallback to public Nominatim. Returns (lat, lon, display_name, error)."""
    st = (street or "").strip()
    sa = (state or "").strip()
    pc = (postal or "").strip()
    cc = (country or "").strip()
    
    if not (st or pc):
        return None, None, None, "Enter a postal code, ZIP, or full address."
        
    parts = []
    if st:
        parts.append(st)
    if sa:
        parts.append(sa)
    if pc:
        parts.append(pc)
        
    if cc:
        if len(cc) == 2 and cc.isalpha():
            parts.append(cc.upper())
        else:
            parts.append(cc)
        
    q = ", ".join(parts)
    
    # Try Google Geocoding first if key available
    g_key = os.getenv("GOOGLE_MAPS_JS_API_KEY") or os.getenv("GOOGLE_MAPS_STATIC_API_KEY")
    if g_key:
        try:
            params = urlencode({"address": q, "key": g_key.strip()})
            url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "OK" and data.get("results"):
                hit = data["results"][0]
                lat = float(hit["geometry"]["location"]["lat"])
                lon = float(hit["geometry"]["location"]["lng"])
                disp = hit.get("formatted_address") or q
                return lat, lon, disp, None
        except Exception as e:
            print(f"Google Maps Geocoding fallback to Nominatim due to error: {e}")

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


def _extract_maps_widget_token_from_grounding_metadata(gm) -> Optional[str]:
    """Context token for the Maps JavaScript contextual Places widget (Gemini Maps grounding)."""
    if gm is None:
        return None
    tok = getattr(gm, "google_maps_widget_context_token", None)
    if tok is None:
        return None
    s = str(tok).strip()
    return s or None


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
        return ctor(enable_widget=True)
    return types.GoogleMaps(enable_widget=True)


def _pixel_chat_generate_config(
    *,
    system_instruction,
    device_id: str,
    prefer_maps: bool = False,
) -> types.GenerateContentConfig:
    """Tools + server-side tool flag per Gemini 3 multi-tool docs.

    The Gemini API does not allow ``google_maps`` and ``google_search`` in the same
    request (400 INVALID_ARGUMENT).     When ``prefer_maps`` is true and Maps grounding
    is enabled with stored coordinates, we attach only ``google_maps`` +
    ``face_animation``; otherwise ``google_search`` + ``face_animation``.
    On maps turns the model should call ``face_animation(animation=map)`` so the hub can
    screenshot the grounded map and push JPEG to the ESP32.
    """
    bot = get_bot_settings(device_id)

    custom_declarations = [
        types.FunctionDeclaration(**FACE_ANIMATION_FUNCTION_DECLARATION),
        types.FunctionDeclaration(**SHOW_WEATHER_FUNCTION_DECLARATION),
        types.FunctionDeclaration(**SHOW_MAP_ANIMATION_FUNCTION_DECLARATION),
    ]

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
            types.Tool(function_declarations=custom_declarations)
        ]
    else:
        tools = [
            types.Tool(google_search=_google_search_builtin()),
            types.Tool(function_declarations=custom_declarations)
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
    maps_street: str = ""
    maps_state: str = ""
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
    street = (new_settings.maps_street or "").strip()
    state = (new_settings.maps_state or "").strip()
    postal = (new_settings.maps_postal_code or "").strip()
    country = (new_settings.maps_country or "").strip()
    maps_enabled = bool(new_settings.maps_grounding_enabled)

    row = {
        "model": new_settings.model,
        "system_instruction": new_settings.system_instruction,
        "timezone_rule": new_settings.timezone_rule or DEFAULT_TIMEZONE_RULE,
        "vision_enabled": bool(new_settings.vision_enabled),
        "maps_grounding_enabled": maps_enabled,
        "maps_street": street,
        "maps_state": state,
        "maps_postal_code": postal,
        "maps_country": country,
    }

    if maps_enabled:
        lat, lon, disp, err = _geocode_address(street, state, postal, country)
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


@app.post("/api/settings/{device_id}/reset")
async def reset_settings_to_default(device_id: str):
    """Restore all stored settings for this bot to application defaults and sync runtime state."""
    row = _default_stored_bot_settings()
    settings = get_all_settings()
    settings[device_id] = row
    save_all_settings(settings)
    history_by_device.pop(device_id, None)
    vision_enabled_by_device[device_id] = row["vision_enabled"]
    timezone_rule_by_device[device_id] = row["timezone_rule"]
    await _send_runtime_vision_to_esp32(device_id, row["vision_enabled"])
    await _send_runtime_timezone_to_esp32(device_id, row["timezone_rule"])
    return {"status": "success", "settings": row, "maps_geocode": {"ok": True, "error": None}}


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
    """Builder so show_weather_animation is bound to the correct bot device."""

    allowed = frozenset(SHOW_WEATHER_FUNCTION_DECLARATION["parameters"]["properties"]["condition"]["enum"])

    def show_weather_animation(condition: str, temperature: float) -> dict:
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
            "show_weather_animation",
            {
                "condition": c,
                "temperature": float(temperature),
            },
        )
        return {"ok": True, "display": "weather queued on robot"}

    return show_weather_animation


def _make_show_map_animation_tool(device_id: str):
    """Builder so show_map_animation is bound to the correct bot device."""

    def show_map_animation(location: str = "", display_style: str = "calling_card") -> dict:
        tool_state = turn_tool_state_by_device.setdefault(
            device_id, {"show_weather": False, "face_animation": False, "show_map": False}
        )
        if tool_state.get("face_animation", False) or tool_state.get("show_weather", False) or tool_state.get("show_map", False):
            return {
                "ok": False,
                "skipped": True,
                "reason": "another animation tool was already called this turn",
            }
        tool_state["show_map"] = True

        # Validate and store display_style
        style = (display_style or "calling_card").strip().lower()
        if style not in ("calling_card", "directions"):
            style = "calling_card"
        map_display_style_by_device[device_id] = style

        # Store the explicit location so the end-of-turn handler uses it
        loc = (location or "").strip()
        if loc:
            map_location_override_by_device[device_id] = loc
            print(f"[Omnibot] show_map_animation: location={loc!r} display_style={style!r}")

        # Teal "map mode" placeholder only for directions; calling card is drawn on-device when data arrives.
        if style == "directions":
            _schedule_face_animation_to_esp32(device_id, "map", "")
            # Start Directions + Static Map now while the model is still streaming (see _try_send_directions_map_early).
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_try_send_directions_map_early(device_id))
            except RuntimeError:
                pass
        # NOTE: Map JPEG / calling card payload is also sent in the end-of-turn handler if not early-sent.
        _broadcast_tool_call_to_frontend("show_map_animation", {"location": loc, "display_style": style})
        return {"ok": True, "display": "map animation queued on robot", "display_style": style}

    return show_map_animation


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
        w = _clamp_face_animation_words(words or "")
        tool_state = turn_tool_state_by_device.setdefault(
            device_id, {"show_weather": False, "face_animation": False, "show_map": False}
        )
        if tool_state.get("face_animation", False) or tool_state.get("show_weather", False) or tool_state.get("show_map", False):
            return {
                "ok": False,
                "skipped": True,
                "reason": "another animation tool was already called this turn",
            }
        tool_state["face_animation"] = True

        _schedule_face_animation_to_esp32(device_id, a, w)
        anim_ms = FACE_ANIMATION_DISPLAY_MS
        return {"ok": True, "display": "face animation queued on robot"}

    return face_animation


def show_face_animation(device_id: str, animation: str, words: str = "") -> dict:
    """Convenience wrapper to trigger an expressive face animation (speaking/happy/mad).

    Uses the same semantics, clamping, guardrails, and broadcast behavior
    as the Gemini `face_animation` tool.
    """
    face_animation_fn = _make_face_animation_tool(device_id)
    return face_animation_fn(animation=animation, words=words)


def show_weather_animation(device_id: str, condition_text: str) -> dict:
    """Convenience wrapper to trigger a weather overlay on the face display.

    `condition_text` can be any free-form weather description (e.g. "cloudy 74",
    "light rain and 65F"); it will be normalized to the canonical
    "<condition> <temperature_f>" form used by the ESP32 firmware.
    """
    normalized = _normalize_weather_words(condition_text)
    parts = normalized.split()
    condition = parts[0] if parts else "cloudy"
    temperature = float(parts[1]) if len(parts) > 1 else 72.0
    show_weather_tool = _make_show_weather_tool(device_id)
    return show_weather_tool(condition=condition, temperature=temperature)


def show_map_animation(device_id: str) -> dict:
    """Convenience wrapper to trigger map mode on the face display.

    This uses the same behavior as the Gemini `show_map_animation` tool, which
    schedules a map face animation and a static map JPEG to be captured and
    pushed to the robot display.
    """
    map_tool = _make_show_map_animation_tool(device_id)
    return map_tool()


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
    if animation == "weather":
        dur = WEATHER_DISPLAY_MS
    elif animation == "map":
        dur = MAP_DISPLAY_MS
    else:
        dur = FACE_ANIMATION_DISPLAY_MS
    payload = {
        "type": "face_animation",
        "animation": animation,
        "duration_ms": dur,
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
    maps_key = (os.getenv("GOOGLE_MAPS_STATIC_API_KEY") or "").strip() or (
        os.getenv("GOOGLE_MAPS_JS_API_KEY") or ""
    ).strip()
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
    maps_key = (os.getenv("GOOGLE_MAPS_JS_API_KEY") or "").strip()
    if not maps_key:
        print(
            "[Omnibot] face_animation(map): GOOGLE_MAPS_JS_API_KEY missing; cannot screenshot map for ESP32."
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
    last_maps_sources: list[dict] = []
    last_maps_widget_token: Optional[str] = None
    last_search_sources: list[dict] = []
    last_search_queries: list[str] = []

    await manager.broadcast({
        "type": "ai_response_stream_start",
        "stream_id": stream_id
    })

    lock = gemini_turn_locks.setdefault(device_id, asyncio.Lock())
    async with lock:
        turn_tool_state_by_device[device_id] = {"show_weather": False, "face_animation": False}
        map_jpeg_sent_this_turn_by_device[device_id] = False
        map_location_override_by_device.pop(device_id, None)
        maps_widget_token_latest_by_device[device_id] = ""
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
        route_reason = "fallback_search"
        if routing_text:
            retrieval, route_reason = await asyncio.to_thread(
                classify_retrieval_with_reason, routing_text
            )
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
                f"{'google_maps' if will_maps else 'google_search'} "
                f"route_reason={route_reason}"
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
                                    src = _extract_maps_sources_from_grounding_metadata(gm)
                                    if src:
                                        last_maps_sources = src
                                    web_src, web_queries = _extract_search_sources_from_grounding_metadata(gm)
                                    if web_src:
                                        last_search_sources = web_src
                                    if web_queries:
                                        last_search_queries = web_queries
                                    wtok = _extract_maps_widget_token_from_grounding_metadata(gm)
                                    if wtok:
                                        last_maps_widget_token = wtok
                                        maps_widget_token_latest_by_device[device_id] = wtok
                                        
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
                        result = _make_face_animation_tool(device_id)(**args)
                    elif fname == "show_weather_animation":
                        result = _make_show_weather_tool(device_id)(**args)
                    elif fname == "show_map_animation":
                        result = _make_show_map_animation_tool(device_id)(**args)
                        
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
        _maps_debug(
            f"turn_end stream_id={stream_id!r} maps_grounding_chunks={len(last_maps_sources)} "
            f"(0 = model did not return Maps sources this turn; it may skip the tool for non-local prompts)"
        )
        if last_maps_sources:
            _maps_debug(f"maps source titles: {[s.get('title') for s in last_maps_sources[:5]]}")
        _maps_debug(
            f"search_grounding_chunks={len(last_search_sources)} search_queries={len(last_search_queries)}"
        )

    end_msg = {
        "type": "ai_response_stream_end",
        "stream_id": stream_id,
        "data": full_text,
    }
    if last_maps_sources:
        end_msg["maps_sources"] = last_maps_sources
    if last_search_sources:
        end_msg["search_sources"] = last_search_sources
    if last_search_queries:
        end_msg["search_queries"] = last_search_queries
    if last_maps_widget_token:
        end_msg["maps_widget_context_token"] = last_maps_widget_token
    await manager.broadcast(end_msg)

    tok = (last_maps_widget_token or "").strip()
    # Pop the tool-call overrides regardless of grounding path, so they are
    # always consumed even when the model skips the built-in Maps tool.
    loc_override = map_location_override_by_device.pop(device_id, "")
    disp_style   = map_display_style_by_device.pop(device_id, "calling_card")
    # Trigger map rendering if the model used grounding OR explicitly called
    # show_map_animation with a location (no grounding needed in that case).
    if last_maps_sources or tok or loc_override:
        maps_key = (
            (os.getenv("GOOGLE_MAPS_STATIC_API_KEY") or "").strip()
            or (os.getenv("GOOGLE_MAPS_JS_API_KEY") or "").strip()
        )
        if maps_key:
            bot = get_bot_settings(device_id)
            asyncio.create_task(
                _send_map_jpeg_to_esp32_after_turn(
                    device_id=device_id,
                    maps_sources=last_maps_sources,
                    api_key=maps_key,
                    fallback_lat=bot.get("maps_latitude"),
                    fallback_lng=bot.get("maps_longitude"),
                    location_override=loc_override,
                    display_style=disp_style,
                )
            )
        else:
            print(
                "[Omnibot] Map animation called but no GOOGLE_MAPS_STATIC_API_KEY/GOOGLE_MAPS_JS_API_KEY found; "
                "skipping ESP32 map image."
            )

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

@app.get("/api/hub-config")
async def hub_config():
    """Public values the dashboard needs (Maps JS key for contextual widget is browser-exposed)."""
    key = (os.getenv("GOOGLE_MAPS_JS_API_KEY") or "").strip()
    return {"maps_js_api_key": key}


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