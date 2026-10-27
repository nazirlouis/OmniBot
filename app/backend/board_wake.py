"""Local-only wake/VAD gate. Nothing reaches Gemini until an utterance completes."""
import time
from pathlib import Path
import numpy as np

class BoardWake:
    def __init__(self,model_path,on_wake,on_utterance,threshold=.55,*,model=None,vad=None,clock=time.monotonic):
        if model is None:
            path=Path(model_path)
            if path.suffix.lower()!='.onnx' or not path.is_file():
                raise ValueError('Gilligan ONNX model is not installed')
            from openwakeword.model import Model
            model=Model(wakeword_models=[str(path)],inference_framework='onnx')
        if vad is None:
            import webrtcvad
            vad=webrtcvad.Vad(2)
        self.model=model;self.vad=vad;self.on_wake=on_wake;self.on_utterance=on_utterance
        self.threshold=threshold;self.paused=False;self.capture=False
        self.ring=bytearray();self.carry=bytearray();self.clip=bytearray()
        self.silent=0;self.post_frames=0;self.cooldown=0
        self.clock=clock;self.followup_until=None;self.followup_samples=0;self.speech_frames=0
    def pause(self):
        self.paused=True;self.capture=False;self.ring.clear();self.carry.clear();self.clip.clear();self.model.reset()
        self.followup_until=None;self.followup_samples=0;self.speech_frames=0
    def resume(self,*,followup=False):
        self.pause();self.paused=False
        self.cooldown=0 if followup else 15
        if followup:
            self.followup_until=self.clock()+3.0
            self.followup_samples=16000*3
    async def _begin_capture(self):
        self.capture=True;self.clip=bytearray(self.ring);self.ring.clear()
        self.post_frames=0;self.silent=0;self.followup_until=None;self.model.reset()
        await self.on_wake()
    async def finish(self):
        if not self.capture:return
        pcm=bytes(self.clip[:480000]);self.pause()
        if len(pcm)>=640:await self.on_utterance(pcm)
    async def feed(self,pcm):
        if self.paused:return
        if len(pcm)>4096 or len(pcm)%2:raise ValueError('invalid wake PCM')
        self.carry.extend(pcm)
        while len(self.carry)>=2560:
            block=bytes(self.carry[:2560]);del self.carry[:2560]
            if self.capture:
                self.clip.extend(block);self.post_frames+=1
                for off in range(0,len(block),640):
                    self.silent=0 if self.vad.is_speech(block[off:off+640],16000) else self.silent+20
                if len(self.clip)>=480000 or (self.post_frames>=5 and self.silent>=600):
                    await self.finish();return
            else:
                self.ring.extend(block)
                if len(self.ring)>38400:del self.ring[:-38400]
                if self.cooldown>0:self.cooldown-=1;continue
                if self.followup_until is not None:
                    # Frames arrive in 80 ms packets. Permit that delivery latency,
                    # but never accept queued audio after a stalled microphone.
                    if self.clock()>self.followup_until+.08:
                        self.followup_until=None;self.speech_frames=0
                    else:
                        for off in range(0,2560,640):
                            if self.followup_samples<=0 and not self.speech_frames:
                                self.followup_until=None;break
                            speech=self.vad.is_speech(block[off:off+640],16000)
                            self.followup_samples=max(0,self.followup_samples-320)
                            self.speech_frames=self.speech_frames+1 if speech else 0
                            # A brief click is insufficient; retain pre-roll so the
                            # first syllable survives this 60 ms confirmation.
                            if self.speech_frames>=3:
                                await self._begin_capture();break
                        if self.capture:continue
                        if self.followup_until is not None:continue
                scores=self.model.predict(np.frombuffer(block,dtype='<i2'))
                confident=max((float(np.max(v)) for v in scores.values()),default=0)>=self.threshold
                speech=any(self.vad.is_speech(block[o:o+640],16000) for o in range(0,2560,640))
                if confident and speech:
                    await self._begin_capture()
