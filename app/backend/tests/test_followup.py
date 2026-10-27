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
    events=[];now=[0.0];model=Model()
    async def wake():events.append('wake')
    async def complete(pcm):events.append(pcm)
    gate=BoardWake(None,wake,complete,model=model,vad=Vad(),clock=lambda:now[0])
    voice=struct.pack('<h',1000)*320;quiet=bytes(640)
    async def frames(sequence):
        for frame in sequence:await gate.feed(frame)

    gate.resume(followup=True)
    await frames([quiet]*152)
    assert not events and gate.followup_until is None
    await frames([voice]*8)
    assert not events # Speech after the silent window needs the wake word.

    gate.resume(followup=True)
    await frames([voice]*4)
    assert events==['wake'] # No classifier match required, no initial cooldown.
    await frames([voice]*200) # Continue speaking beyond the three-second start window.
    assert len(events)==1 and gate.capture
    await frames([quiet]*32)
    assert len(events)==2 and isinstance(events[1],bytes)
    assert voice in events[1] and len(events[1])>16000*2*3
    assert gate.paused

    events.clear();gate.resume(followup=True)
    await frames([quiet]*149+[voice]*3) # Speech starts in the final 20 ms.
    assert events==['wake']
    gate.pause();events.clear();gate.resume(followup=True)
    await frames([quiet]*149+[voice]+[quiet]*10)
    assert not events # A transient at the boundary is not a response.

    gate.resume(followup=True);now[0]=4.0
    await frames([voice]*4)
    assert not events and gate.followup_until is None # Wall-clock expiry after a stalled stream.
    model.score=.9
    await frames([voice]*4)
    assert events==['wake'] # Ordinary wake detection resumes.
    gate.pause();events.clear();model.score=0;gate.resume(followup=True)
    await frames([voice]*800)
    assert len(events)==2 and len(events[1])==480000 # Existing 15-second bound.
    print('PASS: silent expiry, immediate speech, continuous speech beyond 3s, late start, transient rejection, stalled stream, wake fallback, 15s bound')

asyncio.run(main())
