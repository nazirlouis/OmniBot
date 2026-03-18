import io
import os
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
import json
from pydantic import BaseModel
from bleak import BleakScanner, BleakClient
import subprocess

# ==========================================
#          LOAD SECRETS & SETUP
# ==========================================
load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

# Settings Configuration
SETTINGS_FILE = "bot_settings.json"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_SYSTEM_INSTRUCTION = "You are Pixel, a friendly and intelligent small desktop robot.\n\nContext:\n- If I provide audio, I am speaking directly to you through your microphone.\n- If I provide text, I am typing to you from the Prism Hub dashboard.\n- If I provide an image, it is a real-time live capture from your onboard camera.\n\nRules:\n- Keep your answers concise and conversational, as if we are speaking face-to-face.\n- Only discuss or analyze the live photos if they are relevant to my current prompt or question.\n- Do not greet me in every message.\n- You are helpful, slightly curious, and highly efficient."

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

client = genai.Client(api_key=API_KEY)
app = FastAPI(title="ESP32 Gemini Brain Monitor")

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
#          API ENDPOINTS (ESP32)
# ==========================================
@app.post("/process")
async def process_multimodal(
    audio: UploadFile = File(...), 
    image: UploadFile = File(...),
    device_id: str = Form(default="default_bot")
):
    """
    The ESP32 will send a POST request with WAV and JPG.
    We broadcast these events to the React UI.
    """
    print(f"\n--- RECEIVED DATA FROM ESP32 ---")
    
    # Broadcast to UI that processing started
    await manager.broadcast({
        "type": "processing_started",
        "data": "Data received from ESP32. Processing via Gemini..."
    })
    
    try:
        audio_bytes = await audio.read()
        image_bytes = await image.read()
        
        pil_image = Image.open(io.BytesIO(image_bytes))
        
        print("Sending to Gemini...", end=" ", flush=True)
        
        # Look up settings for this specific device
        bot_settings = get_bot_settings(device_id)
        model_to_use = bot_settings["model"]
        sys_instruction = bot_settings["system_instruction"]
        
        response = client.models.generate_content(
            model=model_to_use,
            contents=[
                "Listen to the audio and look at the image. Answer the user's question.",
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                pil_image
            ],
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction,
                thinking_config=types.ThinkingConfig(thinking_level="low")
            )
        )
        
        print(f"\n>>> GEMINI ({model_to_use}) SAYS: {response.text}")
        
        # Broadcast the Gemini response back to the React UI
        await manager.broadcast({
            "type": "ai_response",
            "data": response.text
        })
        
        # Return to ESP32
        return {"status": "success", "reply": response.text}
        
    except Exception as e:
        print(f"\nAPI Error: {e}")
        error_msg = str(e)
        await manager.broadcast({
            "type": "error",
            "data": error_msg
        })
        raise HTTPException(status_code=500, detail=error_msg)

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
    print("Waiting for ESP32 to POST data to /process ...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
