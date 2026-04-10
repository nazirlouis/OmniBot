"""
Per-device Gemini Live API (3.1 Flash Live) session: realtime audio/video in,
native audio + transcriptions out, tool calls, session resumption, and context compression.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from hub_config import get_genai_client, get_gemini_api_key

logger = logging.getLogger(__name__)

# Preview model per https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk
LIVE_MODEL = "gemini-3.1-flash-live-preview"

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
    """Same tool surface as REST Pixel chat (Search + persona tools). Maps not supported on Live."""
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

        self._current_stream_id: Optional[str] = None
        self._first_token_sent_for_stream = False
        self._last_input_text = ""
        self._last_output_text = ""
        self._video_last_sent_mono: float = 0.0
        self._closed_this_turn = False

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
        self._session = await self._session_cm.__aenter__()
        self._stop.clear()

        hist = list(self._get_history(self.device_id) or [])
        if hist:
            try:
                await self._session.send_client_content(turns=hist, turn_complete=True)
            except Exception as e:
                logger.warning("[live] seed history failed device=%s: %s", self.device_id, e)

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

    async def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        await self.ensure_started()
        sess = self._session
        if sess is None:
            return
        self.begin_user_turn(stream_id=str(uuid.uuid4()))
        await sess.send_realtime_input(text=text)

    def begin_user_turn(self, stream_id: Optional[str] = None) -> str:
        sid = stream_id or str(uuid.uuid4())
        self._current_stream_id = sid
        self._first_token_sent_for_stream = False
        self._last_input_text = ""
        self._last_output_text = ""
        self._closed_this_turn = False
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
                await self._schedule_reconnect()
                # Avoid hammering logs while reconnect task is taking over.
                await asyncio.sleep(0.25)

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

        sc = msg.server_content
        if sc is None:
            return

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
            if not self._first_token_sent_for_stream:
                self._first_token_sent_for_stream = True
                await self._notify_esp32_first_token(self.device_id)
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
        turn_done = (
            bool(sc.turn_complete)
            or bool(sc.generation_complete)
            or bool(sc.interrupted)
        )
        if turn_done and not self._closed_this_turn:
            self._closed_this_turn = True
            out = (self._last_output_text or "").strip()
            if out:
                try:
                    self._append_history_content(
                        self.device_id,
                        types.Content(
                            role="model",
                            parts=[types.Part(text=out)],
                        ),
                    )
                except Exception:
                    pass
                await self._notify_esp32_reply(self.device_id, out)
            else:
                await self._notify_esp32_reply(self.device_id, "")
            self._on_wake_live_turn_done(self.device_id)
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
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
            logger.error("[live] reconnect exhausted device=%s", self.device_id)
        finally:
            self._reconnecting = False
            self._reconnect_task = None


def live_api_configured() -> bool:
    return bool(get_gemini_api_key())
