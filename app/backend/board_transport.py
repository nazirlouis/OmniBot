"""Authenticated, bounded 1.43C transport. No audio is written to disk."""
import asyncio, base64, hmac, json, os, struct, time
from pathlib import Path
import numpy as np
import soxr
from fastapi import WebSocket, WebSocketDisconnect

MAX_INPUT=16000*2*15
MAX_OUTPUT=24000*2*90

class LocalAdmin:
    """Only the paired device socket is reachable from LAN; admin UI stays local."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        remote=(scope.get('client') or ('',0))[0]
        if scope['type'] in ('http','websocket') and remote not in ('127.0.0.1','::1','testclient') and scope.get('path')!='/ws/board':
            if scope['type']=='websocket':await send({'type':'websocket.close','code':1008})
            else:
                await send({'type':'http.response.start','status':403,'headers':[]})
                await send({'type':'http.response.body','body':b'Local administration only'})
            return
        await self.app(scope,receive,send)

def install(app, hub):
    if os.getenv("OMNIBOT_BOARD_LOCAL_ADMIN", "0") == "1":
        app.add_middleware(LocalAdmin)
    @app.websocket('/ws/board')
    async def board(ws: WebSocket):
        await ws.accept()
        coord=None;turn_task=None;turn=0;clip=bytearray();state='idle'
        send_lock=asyncio.Lock()
        wake=None
        async def text(kind, **extra):
            async with send_lock:
                await ws.send_json(dict(type=kind,turn_id=turn,**extra))
        async def stop_turn():
            nonlocal turn_task,coord,state
            if turn_task:
                turn_task.cancel()
                try: await turn_task
                except (asyncio.CancelledError,Exception): pass
                turn_task=None
            if coord:
                await coord.stop();coord=None
            state='idle'
        try:
            hello=await asyncio.wait_for(ws.receive_json(),10)
            expected=os.getenv('OMNIBOT_BOARD_TOKEN','')
            if not expected or not hmac.compare_digest(str(hello.get('token','')),expected):
                print('[board] rejected: pairing credential',flush=True);await ws.close(code=1008);return
            did=hello.get('device_id')
            if did!='waveshare-143c':
                await ws.close(code=1008);return
            # Explicit association; never use upstream single-device inference.
            if hub.device_stream_is_live(did):
                print('[board] rejected: existing session',flush=True);await ws.close(code=1008);return
            hub.active_streams[ws]={'device_id':did,'wake_processor':None,'live_coordinator':None}
            hub.record_bot_seen(did)
            await hub.manager.broadcast({'type':'esp32_connected','device_id':did})
            model_path=os.getenv('OMNIBOT_BOARD_WAKE_MODEL','')
            async def wake_start():
                nonlocal turn,state
                if state!='idle':return
                turn+=1;state='capturing';await text('wake_start')
            async def wake_complete(pcm):
                nonlocal turn_task,state
                state='thinking';await text('thinking')
                turn_task=asyncio.create_task(run_turn(pcm,turn,'live'))
            if hello.get('wake_supported') and model_path:
                from board_wake import BoardWake
                try:
                    wake=BoardWake(model_path,wake_start,wake_complete)
                except Exception as exc:
                    print('[board] wake disabled='+type(exc).__name__,flush=True)
            await text('ready',device_id=did,protocol=1,sample_rate=16000,wake_enabled=wake is not None,wake_phrase=os.getenv('OMNIBOT_BOARD_WAKE_PHRASE','Gilligan') if wake else '')
            print('[board] connected authenticated device=waveshare-143c',flush=True)

            async def run_turn(pcm,tid,mode):
                nonlocal coord,state
                started=time.monotonic();output_bytes=0;sent=False;done=asyncio.Event()
                audio_clock=None;sent_bytes=0
                converter=soxr.ResampleStream(24000,16000,1,dtype='int16',quality='HQ')
                async def send_audio(pcm16):
                    nonlocal sent,output_bytes,audio_clock,sent_bytes
                    if turn!=tid:return
                    raw=pcm16.tobytes();output_bytes+=len(raw)
                    if output_bytes>16000*2*90:raise ValueError('reply too long')
                    if raw and not sent:
                        sent=True;state='playing';await text('audio_start',sample_rate=16000,channels=1,encoding='pcm_s16le')
                    if raw and audio_clock is None:audio_clock=time.monotonic()
                    for off in range(0,len(raw),640):
                        if turn!=tid:return
                        packet=raw[off:off+640]
                        # Absolute schedule with 800 ms lead. Unlike per-packet sleeps,
                        # socket and scheduling overhead do not accumulate into gaps.
                        delay=(sent_bytes+len(packet))/32000-.8-(time.monotonic()-audio_clock)
                        if delay>0:await asyncio.sleep(delay)
                        async with send_lock:await ws.send_bytes(struct.pack('<I',tid)+packet)
                        sent_bytes+=len(packet)
                try:
                    if mode=='echo':
                        await send_audio(np.frombuffer(pcm,dtype='<i2'))
                    else:
                        if not hub.get_gemini_api_key():raise ValueError('Configure Gemini key in OmniBot Hub settings')
                        coord=hub._build_live_coordinator(did)
                        coord.manual_activity=True
                        upstream_broadcast=coord._broadcast
                        async def broadcast(event):
                            if turn!=tid:return
                            if event.get('type')=='live_audio_chunk':
                                raw=base64.b64decode(event['b64'],validate=True)
                                rate=event.get('sample_rate')
                                if rate!=24000 or event.get('channels')!=1:raise ValueError('unsupported reply format')
                                await send_audio(converter.resample_chunk(np.frombuffer(raw,dtype='<i2')))
                            await upstream_broadcast(event)
                        coord._broadcast=broadcast
                        coord._on_wake_live_turn_done=lambda _did:done.set()
                        await coord.ensure_started();coord.begin_user_turn(str(tid))
                        from google.genai import types
                        await coord._session.send_realtime_input(activity_start=types.ActivityStart())
                        for off in range(0,len(pcm),3200):
                            await coord._session.send_realtime_input(audio=types.Blob(data=pcm[off:off+3200],mime_type='audio/pcm;rate=16000'))
                        await coord._session.send_realtime_input(activity_end=types.ActivityEnd())
                        await asyncio.wait_for(done.wait(),90)
                        await send_audio(converter.resample_chunk(np.empty(0,dtype='int16'),last=True))
                    if not sent:raise ValueError('No reply audio received')
                    await text('audio_end');state='idle'
                    print(f'[board] turn={tid} mode={mode} input_bytes={len(pcm)} output_bytes={output_bytes} elapsed={time.monotonic()-started:.2f}s',flush=True)
                except asyncio.CancelledError:raise
                except Exception as e:
                    # Provider exceptions can contain request details: expose only a bounded class/message for known local errors.
                    message=str(e) if isinstance(e,ValueError) else type(e).__name__
                    await text('error',message=message);state='idle'
                    if wake:wake.resume()
                    print(f'[board] turn={tid} failed={type(e).__name__}',flush=True)
                finally:
                    if coord:await coord.stop();coord=None

            while True:
                msg=await ws.receive()
                if msg['type']=='websocket.disconnect':break
                if msg.get('bytes') is not None:
                    raw=msg['bytes']
                    if state!='uploading' and wake and raw[:1]==b'\x10':
                        if state in ('idle','capturing'):await wake.feed(raw[1:])
                        continue
                    if state!='uploading':raise ValueError('unexpected audio')
                    if len(raw)>4096 or len(raw)%2 or len(clip)+len(raw)>MAX_INPUT:raise ValueError('input bounds')
                    clip.extend(raw);continue
                j=json.loads(msg.get('text','{}'));kind=j.get('type')
                if kind=='start':
                    if state!='idle':raise ValueError('turn already active')
                    candidate=j.get('turn_id')
                    if not isinstance(candidate,int) or not 0<candidate<2**32 or candidate<=turn:raise ValueError('invalid turn')
                    turn=candidate;clip.clear();state='uploading'
                    if wake:wake.pause()
                elif kind=='end':
                    if state!='uploading' or j.get('turn_id')!=turn or len(clip)<640:raise ValueError('invalid end')
                    pcm=bytes(clip);clip.clear();state='thinking';await text('thinking')
                    mode=j.get('mode','live')
                    if mode not in ('echo','live'):raise ValueError('invalid mode')
                    turn_task=asyncio.create_task(run_turn(pcm,turn,mode))
                elif kind=='cancel':
                    if j.get('turn_id')!=turn:continue
                    await stop_turn();clip.clear();await text('cancelled')
                    if wake:wake.resume()
                elif kind=='wake_finish' and wake and state=='capturing':await wake.finish()
                elif kind=='playback_done' and j.get('turn_id')==turn and state=='idle':
                    if wake and wake.paused:
                        wake.resume(followup=True)
                        print(f'[board] followup window=3s turn={turn}',flush=True)
                elif kind=='ping':await text('pong')
                else:raise ValueError('unknown message')
        except (WebSocketDisconnect,asyncio.TimeoutError,ValueError,json.JSONDecodeError) as exc:
            print('[board] session ended='+type(exc).__name__,flush=True)
        finally:
            session=hub.active_streams.pop(ws,None)
            await stop_turn()
            if session:await hub.manager.broadcast({'type':'esp32_disconnected','device_id':session['device_id']})
            try:await ws.close()
            except Exception:pass
            print('[board] disconnected',flush=True)
