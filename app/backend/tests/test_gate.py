from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import asyncio,struct
from board_wake import BoardWake
class Model:
 score=0
 def predict(self,pcm):return {'Gilligan':self.score}
 def reset(self):pass
class Vad:
 def is_speech(self,pcm,rate):return any(pcm)
async def main():
 events=[]
 async def wake():events.append('wake')
 async def complete(pcm):events.append(('complete',len(pcm)))
 model=Model();gate=BoardWake(None,wake,complete,model=model,vad=Vad())
 speech=struct.pack('<h',1000)*1280;silence=b'\0'*2560
 for _ in range(40):await gate.feed(speech)
 assert not events # non-wake audio never forwarded as a turn
 model.score=.9;await gate.feed(speech);assert events==['wake']
 for _ in range(8):await gate.feed(speech)
 for _ in range(8):await gate.feed(silence)
 assert len(events)==2 and events[1][0]=='complete' and events[1][1]<=480000
 await gate.feed(speech);assert len(events)==2 # playback pause
 gate.resume()
 for _ in range(15):await gate.feed(speech)
 assert len(events)==2
 await gate.feed(speech);assert events[-1]=='wake'
 await gate.finish();assert events[-1][0]=='complete'
 try:BoardWake('/missing/Gilligan.onnx',wake,complete)
 except ValueError:pass
 else:raise AssertionError('missing custom model must not use fallback')
 print('PASS: local wake gate, VAD end, bounded clip, playback suppression, cooldown, BOOT finish, missing-model rejection')
asyncio.run(main())
