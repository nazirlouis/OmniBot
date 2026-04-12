"""
Per-device Gemini Live API (3.1 Flash Live) session: realtime audio/video in,
native audio + transcriptions out, tool calls, session resumption, and context compression.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from face_matching import jpeg_bytes_looks_complete
from hub_config import get_elevenlabs_api_key, get_genai_client, get_gemini_api_key
from grounding_extract import (
    add_inline_citations_from_grounding,
    extract_search_sources_from_grounding_metadata,
)

logger = logging.getLogger(__name__)


def _live_audio_debug() -> bool:
    """Set OMNIBOT_LIVE_AUDIO_DEBUG=1 for verbose live audio / ElevenLabs tracing."""
    return (os.environ.get("OMNIBOT_LIVE_AUDIO_DEBUG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _alog(msg: str, *args: object) -> None:
    if _live_audio_debug():
        logger.info("[live-audio] " + msg, *args)


def _print_live_error(msg: str, *args: object) -> None:
    """Mirror warnings to stdout so `python app.py` / start.ps1 always shows Gemini Live issues."""
    try:
        line = msg % args if args else msg
    except Exception:
        line = msg
    print(f"[gemini-live] {line}", flush=True)


# Preview model per https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk
LIVE_MODEL = "gemini-3.1-flash-live-preview"

# Native Live output voice (prebuilt names match Gemini speech docs, e.g. Kore, Umbriel, Puck).
# Override without editing code: set OMNIBOT_LIVE_VOICE_NAME=Puck
LIVE_PREBUILT_VOICE_NAME = (os.environ.get("OMNIBOT_LIVE_VOICE_NAME") or "Umbriel").strip() or "Umbriel"

# Live output audio is typically 24 kHz PCM (see Live API tool use samples).
LIVE_OUTPUT_AUDIO_SAMPLE_RATE = 24000

_coordinators: Dict[str, "GeminiLiveCoordinator"] = {}


def live_coordinator_for(device_id: str) -> Optional["GeminiLiveCoordinator"]:
    return _coordinators.get(device_id)


def register_live_coordinator(device_id: str, coord: "GeminiLiveCoordinator") -> None:
    _coordinators[device_id] = coord


def unregister_live_coordinator(device_id: str) -> None:
    _coordinators.pop(device_id, None)


def build_pixel_live_tools() -> list[types.Tool]:
    """Grounding + function tools for Live — same split as REST (`_pixel_chat_generate_config`).

    Per Live API docs, Google Search is its own tool entry, e.g. `[{'google_search': {}}, {...}]`;
    we mirror that with two `types.Tool` objects. Maps not supported on Live.
    """
    import heartbeat_service
    import persona
    from pixel_tool_declarations import FACE_ANIMATION_FUNCTION_DECLARATION

    ctor = getattr(types, "ToolGoogleSearch", None)
    google_search_tool = ctor() if ctor is not None else types.GoogleSearch()

    custom_declarations = [
        types.FunctionDeclaration(**FACE_ANIMATION_FUNCTION_DECLARATION),
        types.FunctionDeclaration(**persona.SOUL_REPLACE_DECLARATION),
        types.FunctionDeclaration(**heartbeat_service.MEMORY_REPLACE_DECLARATION),
        types.FunctionDeclaration(**persona.PERSONA_REPLACE_DECLARATION),
        types.FunctionDeclaration(**persona.DAILY_LOG_APPEND_DECLARATION),
        types.FunctionDeclaration(**persona.BOOTSTRAP_COMPLETE_DECLARATION),
    ]
    return [
        types.Tool(google_search=google_search_tool),
        types.Tool(function_declarations=custom_declarations),
    ]


class GeminiLiveCoordinator:
    """Owns one AsyncSession, PCM uplink, receive loop, resumption, and GoAway handling."""

    def __init__(
        self,
        *,
        device_id: str,
        get_bot_settings: Callable[[str], dict],
        resolve_system_instruction: Callable[[str], str],
        get_history: Callable[[str], list],
        append_history_content: Callable[[str, types.Content], None],
        broadcast: Callable[[dict], Awaitable[None]],
        tool_executor: Callable[
            [str, str, list], Awaitable[list[types.FunctionResponse]]
        ],
        notify_esp32_first_token: Callable[[str], Awaitable[None]],
        notify_esp32_reply: Callable[[str, str], Awaitable[None]],
        on_wake_processor_live_turn_done: Callable[[str], None],
        on_user_transcription_activity: Callable[[str], None],
        on_video_frame: Optional[Callable[[str, bytes], Awaitable[None]]] = None,
    ) -> None:
        self.device_id = device_id
        self._get_bot_settings = get_bot_settings
        self._resolve_system_instruction = resolve_system_instruction
        self._get_history = get_history
        self._append_history_content = append_history_content
        self._broadcast = broadcast
        self._tool_executor = tool_executor
        self._notify_esp32_first_token = notify_esp32_first_token
        self._notify_esp32_reply = notify_esp32_reply
        self._on_wake_live_turn_done = on_wake_processor_live_turn_done
        self._on_user_transcription_activity = on_user_transcription_activity
        self._on_video_frame = on_video_frame

        self._lock = asyncio.Lock()
        self._session_cm: Any = None
        self._session: Any = None
        self._pcm_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=512)
        self._sender_task: Optional[asyncio.Task] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._started = False

        self.resumption_handle: Optional[str] = None
        self._reconnecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._go_away_reconnect_scheduled = False
        self._clear_resumption_on_next_reconnect = False

        self._current_stream_id: Optional[str] = None
        self._first_token_sent_for_stream = False
        self._last_input_text = ""
        self._last_output_text = ""
        self._video_last_sent_mono: float = 0.0
        self._closed_this_turn = False
        # After send_text/begin_user_turn, the next interrupt-only server message is the API
        # acknowledging the prior turn was aborted — not completion of the new turn. If we
        # treated it as turn_done, we'd set _closed_this_turn with acc_out_chars=0 and silence.
        self._expect_user_turn_preempt_ack = False
        self._live_turn_search_sources: list[dict] = []
        self._live_turn_search_queries: list[str] = []
        self._live_turn_gm: Any = None

        self._el_q: Optional[asyncio.Queue] = None
        self._el_task: Optional[asyncio.Task] = None
        self._el_logged_no_key = False
        #: Set when ``send_text(..., track_turn_done=True)``; signaled after assistant turn + EL complete.
        self._turn_done_waiter: Optional[asyncio.Event] = None
        #: Filled when a turn completes while ``_turn_done_waiter`` is set; consumed by ``pop_http_turn_reply_text``.
        self._http_turn_reply_text: str = ""

    def _want_elevenlabs_playback(self) -> bool:
        bs = self._get_bot_settings(self.device_id)
        mode = str(bs.get("pixel_tts_voice") or "gemini").strip()
        if mode not in ("elevenlabs_pixel_male", "elevenlabs_pixel_female"):
            return False
        if not get_elevenlabs_api_key():
            if not self._el_logged_no_key:
                logger.warning(
                    "[live] pixel_tts_voice=%s but no ElevenLabs API key; using Gemini PCM device=%s",
                    mode,
                    self.device_id,
                )
                self._el_logged_no_key = True
            return False
        return True

    async def _ensure_elevenlabs_worker(self) -> None:
        if self._el_q is not None:
            return
        from elevenlabs_tts_stream import stream_elevenlabs_turn_pcm, voice_id_for_pixel_tts_mode

        mode = str(self._get_bot_settings(self.device_id).get("pixel_tts_voice") or "")
        vid = voice_id_for_pixel_tts_mode(mode)
        key = get_elevenlabs_api_key()
        self._el_q = asyncio.Queue()
        # Pin at worker creation: emit_pcm runs concurrently with receive_loop; if begin_user_turn()
        # updates _current_stream_id mid-emit, old audio must not reuse the new id (interleaved chunks
        # share one stream_id on the client and sound jumbled).
        el_stream_id = self._current_stream_id or str(uuid.uuid4())

        async def emit_pcm(chunk: bytes) -> None:
            if not chunk:
                _alog("emit_pcm(skip empty) stream_id=%s", el_stream_id)
                return
            b64 = base64.b64encode(chunk).decode("ascii")
            _alog(
                "broadcast live_audio_chunk bytes=%s stream_id=%s",
                len(chunk),
                el_stream_id,
            )
            await self._broadcast(
                {
                    "type": "live_audio_chunk",
                    "device_id": self.device_id,
                    "stream_id": el_stream_id,
                    "sample_rate": LIVE_OUTPUT_AUDIO_SAMPLE_RATE,
                    "channels": 1,
                    "encoding": "pcm_s16le",
                    "b64": b64,
                }
            )

        async def _run_el() -> None:
            try:
                await stream_elevenlabs_turn_pcm(
                    api_key=key,
                    voice_id=vid,
                    text_queue=self._el_q,
                    emit_pcm=emit_pcm,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[live] ElevenLabs stream task failed device=%s: %s", self.device_id, e)
                raise

        self._el_task = asyncio.create_task(_run_el())

    async def ensure_started(self) -> None:
        async with self._lock:
            if self._session is not None:
                return
            await self._connect_locked(initial=True)

    async def _connect_locked(self, *, initial: bool) -> None:
        gc = get_genai_client()
        if gc is None:
            raise RuntimeError("Gemini API key not configured.")
        cfg = self._live_config()
        self._session_cm = gc.aio.live.connect(model=LIVE_MODEL, config=cfg)
        try:
            self._session = await self._session_cm.__aenter__()
        except Exception as e:
            _print_live_error("Live session connect failed device=%s: %s", self.device_id, e)
            logger.exception("[live] connect __aenter__ failed device=%s", self.device_id)
            raise
        self._stop.clear()

        hist = list(self._get_history(self.device_id) or [])
        if hist:
            try:
                await self._session.send_client_content(turns=hist, turn_complete=True)
            except Exception as e:
                logger.warning("[live] seed history failed device=%s: %s", self.device_id, e)
                _print_live_error("seed history failed device=%s: %s", self.device_id, e)

        if self._sender_task is None or self._sender_task.done():
            self._sender_task = asyncio.create_task(self._pcm_sender_loop())
        if self._receiver_task is None or self._receiver_task.done():
            self._receiver_task = asyncio.create_task(self._receive_loop())
        self._started = True

    def _live_config(self) -> types.LiveConnectConfig:
        bs = self._get_bot_settings(self.device_id)
        thinking_level = bs.get("thinking_level") or "minimal"
        sys_instr = self._resolve_system_instruction(self.device_id)

        tools = build_pixel_live_tools()
        kw: dict[str, Any] = {
            "response_modalities": [types.Modality.AUDIO],
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=LIVE_PREBUILT_VOICE_NAME
                    )
                )
            ),
            "system_instruction": sys_instr,
            "tools": tools,
            "input_audio_transcription": types.AudioTranscriptionConfig(),
            "output_audio_transcription": types.AudioTranscriptionConfig(),
            "realtime_input_config": types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    prefix_padding_ms=200,
                    silence_duration_ms=400,
                )
            ),
            "context_window_compression": types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
        }
        from hub_config import GEMINI_THINKING_LEVEL_AUTO

        if thinking_level != GEMINI_THINKING_LEVEL_AUTO:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)

        hist = self._get_history(self.device_id) or []
        if hist:
            kw["history_config"] = types.HistoryConfig(initial_history_in_client_content=True)

        if self.resumption_handle:
            kw["session_resumption"] = types.SessionResumptionConfig(handle=self.resumption_handle)

        return types.LiveConnectConfig(**kw)

    async def stop(self) -> None:
        self._stop.set()
        async with self._lock:
            await self._drain_pcm_queue()
            await self._shutdown_session_locked()
        self.resumption_handle = None
        unregister_live_coordinator(self.device_id)

    async def _drain_pcm_queue(self) -> None:
        try:
            while True:
                self._pcm_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def _shutdown_session_locked(self) -> None:
        for t in (self._sender_task, self._receiver_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._sender_task = None
        self._receiver_task = None
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("[live] session __aexit__: %s", e)
        self._session_cm = None
        self._session = None

    async def enqueue_pcm(self, chunk: bytes) -> None:
        if not chunk or self._session is None:
            return
        try:
            self._pcm_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                _ = self._pcm_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._pcm_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    async def send_video_jpeg(self, jpeg_bytes: bytes) -> None:
        if not jpeg_bytes or self._session is None:
            return
        if not jpeg_bytes_looks_complete(jpeg_bytes):
            return
        now = time.monotonic()
        if now - self._video_last_sent_mono < 1.0:
            return
        self._video_last_sent_mono = now
        try:
            await self._session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
            )
            if self._on_video_frame:
                await self._on_video_frame(self.device_id, jpeg_bytes)
        except Exception as e:
            logger.warning("[live] video frame send failed: %s", e)
            _print_live_error("video frame send failed: %s", e)

    async def _await_in_flight_elevenlabs(self) -> None:
        """HTTP send_text runs concurrently with the receive loop; do not cancel EL mid-decode."""
        t = self._el_task
        if t is None or t.done():
            return
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("[live] await in-flight ElevenLabs: %s", e)

    async def _interrupt_in_flight_elevenlabs(self) -> None:
        """Cancel and drain in-flight ElevenLabs (e.g. dashboard text must not block on greeting TTS)."""
        t = self._el_task
        if t is None or t.done():
            self._el_q = None
            self._el_task = None
            return
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("[live] interrupt ElevenLabs: %s", e)
        self._el_q = None
        self._el_task = None

    async def send_text(
        self, text: str, *, interrupt_previous: bool = False, track_turn_done: bool = False
    ) -> Optional[asyncio.Event]:
        text = (text or "").strip()
        if not text:
            return None
        await self.ensure_started()
        sess = self._session
        if sess is None:
            return None
        if interrupt_previous:
            await self._interrupt_in_flight_elevenlabs()
        else:
            await self._await_in_flight_elevenlabs()
        self.begin_user_turn(stream_id=str(uuid.uuid4()))
        ev: Optional[asyncio.Event] = None
        if track_turn_done:
            ev = asyncio.Event()
            self._turn_done_waiter = ev
        await sess.send_realtime_input(text=text)
        return ev

    def pop_http_turn_reply_text(self) -> str:
        """Return assistant plain text for the last completed ``track_turn_done`` turn, then clear."""
        t = self._http_turn_reply_text
        self._http_turn_reply_text = ""
        return t

    def begin_user_turn(self, stream_id: Optional[str] = None) -> str:
        old_waiter = self._turn_done_waiter
        self._turn_done_waiter = None
        if old_waiter is not None and not old_waiter.is_set():
            self._http_turn_reply_text = ""
            old_waiter.set()
        sid = stream_id or str(uuid.uuid4())
        self._current_stream_id = sid
        self._first_token_sent_for_stream = False
        self._last_input_text = ""
        self._last_output_text = ""
        self._closed_this_turn = False
        self._live_turn_search_sources = []
        self._live_turn_search_queries = []
        self._live_turn_gm = None
        if self._el_task and not self._el_task.done():
            self._el_task.cancel()
        self._el_task = None
        self._el_q = None
        self._expect_user_turn_preempt_ack = True
        return sid

    async def _pcm_sender_loop(self) -> None:
        while not self._stop.is_set():
            sess = self._session
            if sess is None:
                await asyncio.sleep(0.05)
                continue
            try:
                chunk = await asyncio.wait_for(self._pcm_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            if chunk is None:
                break
            try:
                await sess.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
            except Exception as e:
                if not self._stop.is_set():
                    logger.warning("[live] pcm send error: %s", e)

    async def _receive_loop(self) -> None:
        while not self._stop.is_set():
            sess = self._session
            if sess is None:
                await asyncio.sleep(0.05)
                continue
            try:
                async for msg in sess.receive():
                    await self._dispatch_message(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._stop.is_set():
                    break
                logger.warning("[live] receive error device=%s: %s", self.device_id, e)
                _print_live_error("receive error device=%s: %s", self.device_id, e)
                self._clear_resumption_on_next_reconnect = True
                await self._schedule_reconnect()
                # Do not call receive() again on the dead session; wait until reconnect
                # finishes or this task is cancelled during shutdown.
                wait_cancelled = False
                try:
                    while self._reconnecting and not self._stop.is_set():
                        await asyncio.sleep(0.05)
                except asyncio.CancelledError:
                    wait_cancelled = True
                if wait_cancelled:
                    break

    async def _dispatch_message(self, msg: types.LiveServerMessage) -> None:
        if msg.go_away is not None and not self._go_away_reconnect_scheduled:
            self._go_away_reconnect_scheduled = True
            asyncio.create_task(self._reconnect_after_go_away())

        if msg.session_resumption_update is not None:
            u = msg.session_resumption_update
            if getattr(u, "new_handle", None):
                self.resumption_handle = u.new_handle

        if msg.tool_call and msg.tool_call.function_calls:
            sid = self._current_stream_id or str(uuid.uuid4())
            try:
                responses = await self._tool_executor(
                    self.device_id, sid, msg.tool_call.function_calls
                )
                if self._session and responses:
                    await self._session.send_tool_response(function_responses=responses)
            except Exception as e:
                logger.exception("[live] tool_call handling: %s", e)
                _print_live_error("tool_call handling failed: %s", e)

        sc = msg.server_content
        if sc is None:
            return

        gm = getattr(sc, "grounding_metadata", None)
        if gm is not None:
            self._live_turn_gm = gm
            web_src, web_queries = extract_search_sources_from_grounding_metadata(gm)
            if web_src:
                self._live_turn_search_sources = web_src
            if web_queries:
                self._live_turn_search_queries = web_queries

        if sc.input_transcription and sc.input_transcription.text:
            t = sc.input_transcription.text
            fin = bool(sc.input_transcription.finished)
            try:
                self._on_user_transcription_activity(self.device_id)
            except Exception:
                logger.debug(
                    "[live] on_user_transcription_activity callback failed device=%s",
                    self.device_id,
                )
            self._last_input_text = self._last_input_text + t
            await self._broadcast(
                {
                    "type": "live_transcription",
                    "device_id": self.device_id,
                    "role": "user",
                    "text": t,
                    "finished": fin,
                    "stream_id": self._current_stream_id,
                }
            )
            if fin:
                ut = self._last_input_text.strip()
                if ut:
                    try:
                        self._append_history_content(
                            self.device_id,
                            types.Content(
                                role="user",
                                parts=[types.Part(text=ut)],
                            ),
                        )
                    except Exception:
                        pass
                self._last_input_text = ""

        if sc.output_transcription and sc.output_transcription.text:
            t = sc.output_transcription.text
            fin = bool(sc.output_transcription.finished)
            _alog(
                "output_tx delta_len=%s finished=%s stream_id=%s preview=%r",
                len(t or ""),
                fin,
                self._current_stream_id,
                (t or "")[:100].replace("\n", "\\n"),
            )
            if not self._first_token_sent_for_stream:
                self._first_token_sent_for_stream = True
                await self._notify_esp32_first_token(self.device_id)
            await self._broadcast(
                {
                    "type": "live_transcription",
                    "device_id": self.device_id,
                    "role": "model",
                    "text": t,
                    "finished": fin,
                    "stream_id": self._current_stream_id,
                }
            )
            self._last_output_text = self._last_output_text + t
            if self._want_elevenlabs_playback():
                try:
                    await self._ensure_elevenlabs_worker()
                    if self._el_q is not None:
                        await self._el_q.put((t, fin))
                        _alog(
                            "elevenlabs queue put len=%s finished=%s el_task_running=%s",
                            len(t or ""),
                            fin,
                            bool(self._el_task and not self._el_task.done()),
                        )
                except Exception as e:
                    logger.warning("[live] ElevenLabs enqueue failed: %s", e)

        raw_audio = msg.data
        if raw_audio is None and sc.model_turn and sc.model_turn.parts:
            chunks: list[bytes] = []
            for part in sc.model_turn.parts:
                inl = getattr(part, "inline_data", None)
                if not inl or not inl.data:
                    continue
                d = inl.data
                if isinstance(d, bytes):
                    chunks.append(d)
                elif isinstance(d, str):
                    try:
                        chunks.append(base64.b64decode(d))
                    except Exception:
                        pass
            if chunks:
                raw_audio = b"".join(chunks)
        if raw_audio:
            if self._want_elevenlabs_playback():
                _alog(
                    "Gemini native PCM present bytes=%s (suppressed; ElevenLabs mode) stream_id=%s",
                    len(raw_audio),
                    self._current_stream_id,
                )
            else:
                if not self._first_token_sent_for_stream:
                    self._first_token_sent_for_stream = True
                    await self._notify_esp32_first_token(self.device_id)
                _alog(
                    "broadcast Gemini PCM bytes=%s stream_id=%s",
                    len(raw_audio),
                    self._current_stream_id,
                )
                b64 = base64.b64encode(raw_audio).decode("ascii")
                await self._broadcast(
                    {
                        "type": "live_audio_chunk",
                        "device_id": self.device_id,
                        "stream_id": self._current_stream_id,
                        "sample_rate": LIVE_OUTPUT_AUDIO_SAMPLE_RATE,
                        "channels": 1,
                        "encoding": "pcm_s16le",
                        "b64": b64,
                    }
                )

        # Live often sets generation_complete when the model finishes; turn_complete may be absent,
        # which left wake forwarding and the dashboard stuck on "streaming".
        tc = bool(getattr(sc, "turn_complete", False))
        gc = bool(getattr(sc, "generation_complete", False))
        intr = bool(getattr(sc, "interrupted", False))
        interrupt_only = intr and not tc and not gc
        if interrupt_only and self._expect_user_turn_preempt_ack:
            self._expect_user_turn_preempt_ack = False
            logger.info(
                "[live-audio] ignored interrupt-only (new user turn preempted previous reply) "
                "device=%s stream_id=%s",
                self.device_id,
                self._current_stream_id,
            )
        else:
            # turn_complete alone can arrive before any model tokens; finalizing then leaves
            # _closed_this_turn set and ElevenLabs waiting forever for output_transcription.
            has_out = bool((self._last_output_text or "").strip())
            if self._want_elevenlabs_playback():
                # Empty turn_complete before any output_transcription would close the turn and strand EL.
                turn_done = bool(gc) or bool(intr) or (bool(tc) and (has_out or bool(gc)))
                if tc and not gc and not intr and not has_out:
                    logger.info(
                        "[live-audio] ignored empty turn_complete (await generation_complete / model text) "
                        "device=%s stream_id=%s",
                        self.device_id,
                        self._current_stream_id,
                    )
            else:
                turn_done = bool(tc) or bool(gc) or bool(intr)
            if turn_done and not self._closed_this_turn:
                self._closed_this_turn = True
                self._expect_user_turn_preempt_ack = False
                prev_out = (self._last_output_text or "").strip()
                logger.info(
                    "[live-audio] turn_done device=%s stream_id=%s el=%s "
                    "turn_complete=%s generation_complete=%s interrupted=%s acc_out_chars=%s",
                    self.device_id,
                    self._current_stream_id,
                    self._want_elevenlabs_playback(),
                    bool(getattr(sc, "turn_complete", False)),
                    bool(getattr(sc, "generation_complete", False)),
                    bool(getattr(sc, "interrupted", False)),
                    len(prev_out),
                )
                if self._el_q is not None:
                    try:
                        _alog(
                            "elevenlabs sentinel queue put (end turn) stream_id=%s",
                            self._current_stream_id,
                        )
                        await self._el_q.put(None)
                        if self._el_task:
                            try:
                                await self._el_task
                                logger.info(
                                    "[live-audio] ElevenLabs task finished cleanly device=%s stream_id=%s",
                                    self.device_id,
                                    self._current_stream_id,
                                )
                            except asyncio.CancelledError:
                                # begin_user_turn() cancels the previous EL task when a new turn
                                # starts. That must NOT propagate: _receive_loop treats CancelledError
                                # as "stop receive forever" and would break the Gemini stream.
                                if self._stop.is_set():
                                    raise
                                logger.info(
                                    "[live-audio] ElevenLabs task superseded (new turn) device=%s stream_id=%s",
                                    self.device_id,
                                    self._current_stream_id,
                                )
                    except Exception as e:
                        logger.warning(
                            "[live] ElevenLabs turn failed device=%s: %s", self.device_id, e
                        )
                    finally:
                        self._el_q = None
                        self._el_task = None
                out_plain = (self._last_output_text or "").strip()
                out_cited = (
                    add_inline_citations_from_grounding(out_plain, self._live_turn_gm)
                    if out_plain and self._live_turn_gm is not None
                    else out_plain
                )
                if out_plain:
                    try:
                        self._append_history_content(
                            self.device_id,
                            types.Content(
                                role="model",
                                parts=[types.Part(text=out_plain)],
                            ),
                        )
                    except Exception:
                        pass
                    await self._notify_esp32_reply(self.device_id, out_plain)
                else:
                    await self._notify_esp32_reply(self.device_id, "")
                if self._live_turn_search_sources or self._live_turn_search_queries or (
                    out_cited and out_cited != out_plain
                ):
                    payload: dict[str, Any] = {
                        "type": "live_search_grounding",
                        "device_id": self.device_id,
                        "stream_id": self._current_stream_id,
                        "search_sources": self._live_turn_search_sources,
                        "search_queries": self._live_turn_search_queries,
                    }
                    if out_cited and out_cited != out_plain:
                        payload["final_text"] = out_cited
                    await self._broadcast(payload)
                    self._live_turn_search_sources = []
                    self._live_turn_search_queries = []
                self._live_turn_gm = None
                self._on_wake_live_turn_done(self.device_id)
                tdw = self._turn_done_waiter
                self._turn_done_waiter = None
                if tdw is not None and not tdw.is_set():
                    self._http_turn_reply_text = out_plain
                    tdw.set()
                self._first_token_sent_for_stream = False
                self._last_output_text = ""
                self._last_input_text = ""

    async def _schedule_reconnect(self) -> None:
        if self._reconnecting or self._stop.is_set():
            return
        self._reconnecting = True
        logger.info("[live] reconnect scheduled device=%s", self.device_id)
        self._reconnect_task = asyncio.create_task(self._reconnect_with_backoff())

    async def _reconnect_after_go_away(self) -> None:
        try:
            await asyncio.sleep(0.5)
            await self._schedule_reconnect()
        finally:
            self._go_away_reconnect_scheduled = False

    async def _reconnect_with_backoff(self) -> None:
        if self._stop.is_set():
            return
        if self._clear_resumption_on_next_reconnect:
            self.resumption_handle = None
            self._clear_resumption_on_next_reconnect = False
        delay = 0.5
        try:
            async with self._lock:
                await self._shutdown_session_locked()
            for attempt in range(8):
                if self._stop.is_set():
                    return
                logger.info(
                    "[live] reconnect attempt %s/%s device=%s",
                    attempt + 1,
                    8,
                    self.device_id,
                )
                try:
                    async with self._lock:
                        await self._connect_locked(initial=False)
                    logger.info(
                        "[live] reconnect success attempt=%s device=%s",
                        attempt + 1,
                        self.device_id,
                    )
                    return
                except Exception as e:
                    logger.warning("[live] reconnect attempt %s failed: %s", attempt + 1, e)
                    _print_live_error("reconnect attempt %s failed: %s", attempt + 1, e)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
            logger.error("[live] reconnect exhausted device=%s", self.device_id)
            _print_live_error("reconnect exhausted (giving up) device=%s", self.device_id)
        finally:
            self._reconnecting = False
            self._reconnect_task = None


def live_api_configured() -> bool:
    return bool(get_gemini_api_key())
