from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,os,sys,struct
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient

from board_transport import install
os.environ['OMNIBOT_BOARD_TOKEN']='test-only-fixture'
class Hub:
 def __init__(self):
  self.active_streams={};self.manager=SimpleNamespace(broadcast=self.broadcast)
 async def broadcast(self,event):pass
 def record_bot_seen(self,did):pass
 def device_stream_is_live(self,did):return any(v['device_id']==did for v in self.active_streams.values())
hub=Hub();app=FastAPI();install(app,hub)
hello={'device_id':'waveshare-143c','token':'test-only-fixture'}
with TestClient(app) as client:
 with client.websocket_connect('/ws/board') as ws:
  ws.send_json({**hello,'token':'bad'})
  try:ws.receive_json();raise AssertionError('accepted bad credential')
  except Exception as e:assert type(e).__name__=='WebSocketDisconnect'
 with client.websocket_connect('/ws/board') as ws:
  ws.send_json(hello);assert ws.receive_json()['type']=='ready'
  with client.websocket_connect('/ws/board') as duplicate:
   duplicate.send_json(hello)
   try:duplicate.receive_json();raise AssertionError('accepted duplicate')
   except Exception as e:assert type(e).__name__=='WebSocketDisconnect'
  ws.send_json({'type':'start','turn_id':1})
  pcm=b'\0\0'*48000
  for off in range(0,len(pcm),4096):ws.send_bytes(pcm[off:off+4096])
  ws.send_json({'type':'end','turn_id':1,'mode':'echo'})
  assert ws.receive_json()['type']=='thinking';assert ws.receive_json()['type']=='audio_start'
  msg=ws.receive_bytes();assert struct.unpack('<I',msg[:4])[0]==1
  ws.send_json({'type':'cancel','turn_id':1})
  while True:
   msg=ws.receive()
   if msg.get('text') and json.loads(msg['text'])['type']=='cancelled':break
  ws.send_json({'type':'start','turn_id':2});ws.send_json({'type':'cancel','turn_id':2})
  assert ws.receive_json()['type']=='cancelled'
 assert not hub.active_streams
 with client.websocket_connect('/ws/board') as ws:
  ws.send_json(hello);assert ws.receive_json()['type']=='ready'
  ws.send_json({'type':'start','turn_id':1});ws.send_bytes(b'\0'*4098)
  try:ws.receive_json();raise AssertionError('accepted oversized chunk')
  except Exception as e:assert type(e).__name__=='WebSocketDisconnect'
 assert not hub.active_streams
print('PASS: bad auth, duplicate identity, active cancellation, next turn, reconnect cleanup, input bounds')
