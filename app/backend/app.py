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
from typing import Optional

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
    "- When I ask about weather or current conditions, use Google Search for accurate details when needed.\n"
    "- Then call show_weather exactly once with condition matching the sky/state and temperature as the numeric "
    "value in degrees Fahrenheit (number only).\n"
    "- condition must be one of: sunny, cloudy, partially_cloudy, raining, snowing.\n"
    "- Still answer me naturally in your reply text."
)

WEATHER_DISPLAY_MS = 5000

# Event loop used to push WebSocket messages from sync Gemini tool invocations (worker threads).
_main_async_loop: Optional[asyncio.AbstractEventLoop] = None

# OpenAPI-style declaration matching Gemini function-calling docs
# (https://ai.google.dev/gemini-api/docs/function-calling — combine with Google Search).
# The executable implementation is `_make_show_weather_tool` (automatic function calling).
SHOW_WEATHER_FUNCTION_DECLARATION = {
    "name": "show_weather",
    "description": (
        "Shows weather on the robot round display for several seconds. "
        "Call when the user asks about weather or conditions; use Google Search grounding when you need live data."
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
        "system_instruction": bot_conf.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION)
    }

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


def _pixel_chat_generate_config(*, system_instruction, device_id: str) -> types.GenerateContentConfig:
    """Tools + server-side tool flag per Gemini 3 multi-tool docs.

    Docs show one types.Tool(google_search=..., function_declarations=[...]) plus
    include_server_side_tool_invocations on the config. The current google-genai
    client merges function declarations in a way that drops google_search if both
    are on the same Tool, so we pass separate Tool(google_search=...) and the
    show_weather callable (schema aligned with SHOW_WEATHER_FUNCTION_DECLARATION).
    """
    show_weather_fn = _make_show_weather_tool(device_id)
    tools = [
        types.Tool(google_search=_google_search_builtin()),
        show_weather_fn,
    ]
    fields = types.GenerateContentConfig.model_fields
    kwargs: dict = {
        "system_instruction": system_instruction,
        "thinking_config": types.ThinkingConfig(thinking_level="minimal"),
        "tools": tools,
    }
    if "include_server_side_tool_invocations" in fields:
        kwargs["include_server_side_tool_invocations"] = True
    else:
        kwargs["tool_config"] = types.ToolConfig(
            include_server_side_tool_invocations=True,
        )
    return types.GenerateContentConfig(**kwargs)


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

class TextCommandRequest(BaseModel):
    message: str
    device_id: str = "default_bot"

class VisionToggleRequest(BaseModel):
    enabled: bool

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
    settings[device_id] = {
        "model": new_settings.model,
        "system_instruction": new_settings.system_instruction
    }
    save_all_settings(settings)
    return {"status": "success", "settings": settings[device_id]}

# ==========================================
#          ESP32 WEBSOCKET STREAMING
# ==========================================
# Store active streaming sessions by ESP32 WebSocket connection
active_streams = {}
chat_sessions = {}
chat_session_locks = {}
vision_enabled_by_device = {}


def _make_show_weather_tool(device_id: str):
    """Builder so each chat session binds show_weather to the correct bot."""

    allowed = frozenset(SHOW_WEATHER_FUNCTION_DECLARATION["parameters"]["properties"]["condition"]["enum"])

    def show_weather(condition: str, temperature: float) -> dict:
        """Pushes a weather graphic to Pixel's round display for several seconds.

        Same contract as SHOW_WEATHER_FUNCTION_DECLARATION (enum + Fahrenheit).
        """
        c = (condition or "").strip().lower().replace(" ", "_").replace("-", "_")
        if c not in allowed:
            c = "cloudy"
        _schedule_weather_to_esp32(device_id, c, float(temperature))
        return {"ok": True, "display": "weather queued on robot"}

    return show_weather


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


def is_vision_enabled(device_id: str) -> bool:
    return vision_enabled_by_device.get(device_id, True)

def get_or_create_chat(device_id: str):
    """Creates or reuses a multi-turn chat session per bot device."""
    bot_settings = get_bot_settings(device_id)
    model = bot_settings["model"]
    system_instruction = bot_settings["system_instruction"]
    current = chat_sessions.get(device_id)

    if (
        current
        and current.get("model") == model
        and current.get("system_instruction") == system_instruction
    ):
        return current["chat"]

    chat = client.chats.create(
        model=model,
        config=_pixel_chat_generate_config(
            system_instruction=system_instruction,
            device_id=device_id,
        ),
    )

    chat_sessions[device_id] = {
        "chat": chat,
        "model": model,
        "system_instruction": system_instruction
    }
    return chat

async def stream_chat_turn_response(device_id: str, message_content):
    """Streams one turn (text or multimodal) over a persistent chat session."""
    global _main_async_loop
    _main_async_loop = asyncio.get_running_loop()

    stream_id = str(uuid.uuid4())
    full_text = ""
    loop = asyncio.get_running_loop()
    first_token_notified = False

    await manager.broadcast({
        "type": "ai_response_stream_start",
        "stream_id": stream_id
    })

    lock = chat_session_locks.setdefault(device_id, asyncio.Lock())
    async with lock:
        chat = get_or_create_chat(device_id)
        response_stream = chat.send_message_stream(message_content)

        while True:
            chunk = await loop.run_in_executor(
                None,
                partial(next, response_stream, None)
            )
            if chunk is None:
                break

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

    await manager.broadcast({
        "type": "ai_response_stream_end",
        "stream_id": stream_id,
        "data": full_text
    })

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
    active_streams[websocket] = {
        "audio_buffer": bytearray(),
        "video_frames": [],
        "device_id": "default_bot" # We'll just use default for now
    }
    
    try:
        while True:
            # Receive binary packet from ESP32
            data = await websocket.receive_bytes()
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

                    # 4. Call Gemini on the shared multi-turn chat session
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
            del active_streams[websocket]
    except Exception as e:
        print(f"Stream error: {e}")
        if websocket in active_streams:
            del active_streams[websocket]

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
    """Clears the in-memory multi-turn chat session for a bot."""
    chat_sessions.pop(device_id, None)
    return {"status": "success", "message": f"Chat session reset for {device_id}"}

@app.get("/api/runtime/{device_id}/vision")
async def get_vision_setting(device_id: str):
    """Gets whether video frames are included in model requests."""
    return {"device_id": device_id, "enabled": is_vision_enabled(device_id)}

@app.post("/api/runtime/{device_id}/vision")
async def set_vision_setting(device_id: str, payload: VisionToggleRequest):
    """Sets whether video frames are included in model requests."""
    vision_enabled_by_device[device_id] = payload.enabled
    return {"status": "success", "device_id": device_id, "enabled": payload.enabled}

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