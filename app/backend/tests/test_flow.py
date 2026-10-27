from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import os,asyncio,base64,struct
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import board_wake
from board_transport import install
RealGate=board_wake.BoardWake
class Model:
 score=0
 def predict(self,pcm):return {'Gilligan':self.score}
 def reset(self):pass
class Vad:
 def is_speech(self,pcm,rate):return any(pcm)
model=Model()
board_wake.BoardWake=lambda path,wake,complete:RealGate(None,wake,complete,model=model,vad=Vad())
os.environ['OMNIBOT_BOARD_TOKEN']='fixture'
os.environ['OMNIBOT_BOARD_WAKE_MODEL']='fixture.onnx'
class Coordinator:
 def __init__(self,hub):self.hub=hub;self._session=self;self._broadcast=self.ignore
 async def ignore(self,event):pass
 async def ensure_started(self):pass
 async def stop(self):pass
 def begin_user_turn(self,tid):self.tid=tid
 async def send_realtime_input(self,**kw):
  if kw.get('audio'):self.hub.provider_bytes+=len(kw['audio'].data)
  if kw.get('activity_end') is not None:
   self.hub.turns+=1
   await self._broadcast(dict(type='live_audio_chunk',sample_rate=24000,channels=1,b64=base64.b64encode(b'\0\0'*2400).decode()))
   self._on_wake_live_turn_done('waveshare-143c')
class Hub:
 def __init__(self):self.active_streams={};self.manager=SimpleNamespace(broadcast=self.broadcast);self.provider_bytes=0;self.turns=0
 async def broadcast(self,event):pass
 def record_bot_seen(self,did):pass
 def device_stream_is_live(self,did):return any(v['device_id']==did for v in self.active_streams.values())
 def get_gemini_api_key(self):return 'fixture'
 def _build_live_coordinator(self,did):return Coordinator(self)
hub=Hub();app=FastAPI();install(app,hub)
speech=b'\x10'+struct.pack('<h',1000)*1280;silence=b'\x10'+b'\0'*2560
with TestClient(app) as c, c.websocket_connect('/ws/board') as ws:
 ws.send_json(dict(device_id='waveshare-143c',token='fixture',wake_supported=True))
 assert ws.receive_json()['wake_enabled'] is True
 for _ in range(20):ws.send_bytes(speech)
 ws.send_json(dict(type='ping'));assert ws.receive_json()['type']=='pong';assert hub.provider_bytes==0
 model.score=.9;ws.send_bytes(speech);assert ws.receive_json()==dict(type='wake_start',turn_id=1)
 for _ in range(6):ws.send_bytes(speech)
 for _ in range(8):ws.send_bytes(silence)
 assert ws.receive_json()['type']=='thinking'
 assert ws.receive_json()['type']=='audio_start'
 output=0
 while True:
  msg=ws.receive()
  if msg.get('bytes'):output+=len(msg['bytes'])-4
  elif msg.get('text') and 'audio_end' in msg['text']:break
 assert hub.turns==1 and hub.provider_bytes>0 and output>0
 # Speaker output cannot self-trigger before the explicit playback-drained ACK.
 ws.send_bytes(speech);ws.send_json(dict(type='ping'));assert ws.receive_json()['type']=='pong'
 assert hub.turns==1
 ws.send_json(dict(type='playback_done',turn_id=1))
 model.score=0 # A follow-up needs speech, not another wake word.
 ws.send_bytes(speech);assert ws.receive_json()==dict(type='wake_start',turn_id=2)
 for _ in range(45):ws.send_bytes(speech)
 ws.send_json(dict(type='ping'));assert ws.receive_json()['type']=='pong';assert hub.turns==1
 for _ in range(8):ws.send_bytes(silence)
 assert ws.receive_json()['type']=='thinking'
 assert ws.receive_json()['type']=='audio_start'
 while True:
  msg=ws.receive()
  if msg.get('text') and 'audio_end' in msg['text']:break
 assert hub.turns==2
 ws.send_json(dict(type='playback_done',turn_id=2))
 for _ in range(40):ws.send_bytes(silence)
 # Duplicate ACK cannot reopen an expired window.
 ws.send_json(dict(type='playback_done',turn_id=2))
 ws.send_bytes(speech);ws.send_json(dict(type='ping'));assert ws.receive_json()['type']=='pong'
 assert hub.turns==2
 model.score=.9;ws.send_bytes(speech);assert ws.receive_json()['turn_id']==3
 ws.send_json(dict(type='cancel',turn_id=3));assert ws.receive_json()['type']=='cancelled'
 model.score=0;ws.send_json(dict(type='playback_done',turn_id=3))
 for _ in range(20):ws.send_bytes(speech)
 ws.send_json(dict(type='ping'));assert ws.receive_json()['type']=='pong';assert hub.turns==2
print('PASS: local pre-wake audio, wake/provider/speaker flow, playback ACK gate, spoken follow-up, silent expiry, duplicate ACK, wake fallback, cancellation (mock classifier/provider)')
