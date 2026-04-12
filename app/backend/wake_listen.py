"""
Hub-side wake word (openWakeWord) + WebRTC VAD end-of-utterance.

Custom wake word "pixel": train an ONNX model with the openWakeWord project and either:
  - Place the file at app/backend/models/wake/pixel.onnx, or
  - Set OMNIBOT_WAKE_WORD_MODEL to the absolute path of your .onnx file.

If no custom model is present, falls back to the pretrained ``hey_jarvis`` model (say the phrase
that model expects, or add pixel.onnx).

Environment:
  OMNIBOT_WAKE_WORD_MODEL   Path to .onnx wake word model (optional).
  OMNIBOT_WAKE_THRESHOLD    Score 0..1 to trigger wake (default 0.55).
  OMNIBOT_WAKE_SILENCE_MS   Trailing silence to end utterance (default 550).
  OMNIBOT_WAKE_COOLDOWN_S   Seconds after a wake before detecting again (default 1.2).
  OMNIBOT_FOLLOW_UP_S       Fallback when bot settings do not supply post-reply follow-up:
                            seconds of VAD-only listening after each reply without the wake
                            phrase (default 5). Set 0 to disable. Hub Pixel settings override
                            for Live and REST wake when configured.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import numpy as np

try:
    from openwakeword.model import Model as OwwModel
except ImportError:  # pragma: no cover
    OwwModel = None  # type: ignore[misc, assignment]

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
# 20 ms frames for webrtcvad at 16 kHz
VAD_FRAME_SAMPLES = 320
VAD_FRAME_BYTES = VAD_FRAME_SAMPLES * BYTES_PER_SAMPLE
PRE_ROLL_SEC = 1.2
MAX_UTTERANCE_SEC = 15.0

# Hub → dashboard (Intelligence Feed): how Pixel voice interaction is gated on this stream.
WAKE_LISTEN_WAKE_REQUIRED = "wake_required"
WAKE_LISTEN_FOLLOW_UP = "follow_up"
WAKE_LISTEN_STREAMING = "streaming"


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _ensure_openwakeword_pretrained_files() -> None:
    """Pip does not always ship ONNX/tflite blobs; download into package resources on first use."""
    try:
        import openwakeword
        from openwakeword.utils import download_models
    except ImportError:
        return
    td = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
    hey_onnx = os.path.join(td, "hey_jarvis_v0.1.onnx")
    if os.path.isfile(hey_onnx):
        return
    print("[wake] Downloading openWakeWord pretrained models (first run, ~tens of MB)...")
    os.makedirs(td, exist_ok=True)
    download_models(model_names=["hey_jarvis_v0.1"], target_directory=td)


def _resolve_wake_model() -> tuple[list[str], str]:
    """Returns (wakeword_models list for OWW, short label for logging)."""
    env_path = (os.getenv("OMNIBOT_WAKE_WORD_MODEL") or "").strip()
    if env_path and Path(env_path).is_file():
        return [env_path], Path(env_path).stem

    here = Path(__file__).resolve().parent
    bundled = here / "models" / "wake" / "pixel.onnx"
    if bundled.is_file():
        return [str(bundled)], "pixel"

    _ensure_openwakeword_pretrained_files()
    # Pretrained fallback name (paths resolved inside Model() to package resources).
    return ["hey_jarvis"], "hey_jarvis"


def build_openwakeword_model() -> tuple[Any, str]:
    if OwwModel is None:
        raise RuntimeError("openwakeword is not installed")
    paths, label = _resolve_wake_model()
    m = OwwModel(wakeword_models=paths, inference_framework="onnx")
    return m, label


@dataclass
class WakeListenProcessor:
    """Streaming PCM processor: wake detection + VAD tail; calls async callback with raw PCM."""

    on_utterance: Callable[[bytes], Coroutine[Any, Any, None]]
    on_capture_start: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
    on_capture_end: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
    on_capture_abort: Optional[Callable[[], None]] = None
    # When True, after wake (or follow-up VAD) PCM goes to on_live_pcm; hub VAD does not cut utterances.
    use_gemini_live: bool = False
    on_live_pcm: Optional[Callable[[bytes], Coroutine[Any, Any, None]]] = None
    on_live_wake: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
    # After Gemini Live ends a turn: seconds of VAD-only follow-up (no wake phrase). From bot settings via callable.
    get_follow_up_window_s: Optional[Callable[[], float]] = None
    # Optional: async callback(mode) for dashboard — WAKE_LISTEN_* strings.
    on_wake_listen_state: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None
    wake_threshold: float = field(default_factory=lambda: _env_float("OMNIBOT_WAKE_THRESHOLD", 0.55))
    silence_ms: int = field(
        default_factory=lambda: int(_env_float("OMNIBOT_WAKE_SILENCE_MS", 550))
    )
    cooldown_s: float = field(default_factory=lambda: _env_float("OMNIBOT_WAKE_COOLDOWN_S", 1.2))

    model: Any = field(init=False)
    model_label: str = field(init=False)
    vad: Any = field(init=False)

    _ring: bytearray = field(init=False)
    _pre_roll_bytes: int = field(init=False)
    _capturing: bool = field(init=False, default=False)
    _utterance: bytearray = field(init=False, default_factory=bytearray)
    _vad_carry: bytearray = field(init=False, default_factory=bytearray)
    _silence_ms_acc: float = field(init=False, default=0.0)
    _cooldown_until: float = field(init=False, default=0.0)
    _capture_started_at: Optional[float] = field(init=False, default=None)
    paused: bool = field(init=False, default=False)
    _follow_up_until: Optional[float] = field(init=False, default=None)
    _followup_scan_carry: bytearray = field(init=False, default_factory=bytearray)
    _capture_from_followup: bool = field(init=False, default=False)
    _live_forward: bool = field(init=False, default=False)
    _follow_up_window_s: float = field(init=False, default=5.0)
    _follow_up_turn_started: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.model, self.model_label = build_openwakeword_model()
        try:
            import webrtcvad
        except ImportError as e:
            raise RuntimeError(
                "webrtcvad is required for wake/VAD. Install with: pip install webrtcvad "
                "(or re-run .\\scripts\\install.ps1 from the repo root)."
            ) from e
        self.vad = webrtcvad.Vad(2)
        self._pre_roll_bytes = int(PRE_ROLL_SEC * SAMPLE_RATE * BYTES_PER_SAMPLE)
        self._ring = bytearray()

    def _schedule_wake_listen_state(self, mode: str) -> None:
        if not self.on_wake_listen_state:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.on_wake_listen_state(mode))

    async def _await_wake_listen_state(self, mode: str) -> None:
        if self.on_wake_listen_state:
            await self.on_wake_listen_state(mode)

    def begin_follow_up_window(self, seconds: Optional[float] = None) -> None:
        """After a bot reply, allow no-wake follow-up for a bounded window."""
        raw = _env_float("OMNIBOT_FOLLOW_UP_S", 5.0) if seconds is None else float(seconds)
        self._follow_up_window_s = raw
        self._follow_up_turn_started = False
        if raw <= 0:
            self._follow_up_until = None
            self._followup_scan_carry.clear()
            self._schedule_wake_listen_state(WAKE_LISTEN_WAKE_REQUIRED)
            return
        self._follow_up_until = time.monotonic() + raw
        self._cooldown_until = 0.0
        self._schedule_wake_listen_state(WAKE_LISTEN_FOLLOW_UP)

    def begin_follow_up_turn_if_needed(self) -> bool:
        """Mark the next follow-up user activity as a fresh turn once."""
        if self._follow_up_until is None:
            return False
        if self._follow_up_turn_started:
            return False
        self._follow_up_turn_started = True
        self._schedule_wake_listen_state(WAKE_LISTEN_STREAMING)
        return True

    def ensure_follow_up_window_if_forwarding(self) -> None:
        """Open the post-reply follow-up window if mic is streaming but the window was never set.

        Normal wake flow calls ``end_live_forwarding`` → ``begin_follow_up_window``. Paths that only
        call ``start_live_pcm_forwarding_only`` (e.g. after a face greeting) never set
        ``_follow_up_until``, so ``begin_follow_up_turn_if_needed`` never runs and Gemini keeps one
        ``stream_id`` for all turns — dashboard transcripts and audio routing break.
        """
        if not self._live_forward:
            return
        if self._follow_up_until is not None:
            return
        if self.get_follow_up_window_s is not None:
            self.begin_follow_up_window(self.get_follow_up_window_s())
        else:
            self.begin_follow_up_window()

    async def activate_live_forwarding_from_external_wake(self) -> None:
        """Pixel (or other source) detected wake; stream conversation audio from elsewhere (e.g. browser).

        Skips forwarding the local pre-roll ring into Live — avoids sending ESP32 audio when the hub
        uses browser mic for Gemini Live.
        """
        if not self.use_gemini_live or not self.on_live_pcm:
            return
        if self._live_forward:
            return
        if self.on_capture_start:
            await self.on_capture_start()
        if self.on_live_wake:
            await self.on_live_wake()
        self._ring.clear()
        self._followup_scan_carry.clear()
        self._cooldown_until = time.monotonic() + self.cooldown_s
        self._live_forward = True
        await self._await_wake_listen_state(WAKE_LISTEN_STREAMING)
        try:
            self.model.reset()
        except Exception:
            pass

    async def start_live_pcm_forwarding_only(self) -> None:
        """Stream mic to Live without ``on_live_wake`` / ``begin_user_turn`` (turn already started)."""
        if not self.use_gemini_live or not self.on_live_pcm:
            return
        if self._live_forward:
            return
        self._ring.clear()
        self._followup_scan_carry.clear()
        self._cooldown_until = time.monotonic() + self.cooldown_s
        self._live_forward = True
        await self._await_wake_listen_state(WAKE_LISTEN_STREAMING)
        try:
            self.model.reset()
        except Exception:
            pass

    def note_model_user_activity(self) -> None:
        """Refresh follow-up timeout based on Gemini input transcription activity."""
        if self._follow_up_until is None:
            return
        if self._follow_up_window_s <= 0:
            return
        self._follow_up_until = time.monotonic() + self._follow_up_window_s
        self._schedule_wake_listen_state(WAKE_LISTEN_FOLLOW_UP)

    def set_paused(self, p: bool) -> None:
        self.paused = bool(p)
        if self.paused:
            if self._capturing and self.on_capture_abort:
                self.on_capture_abort()
            self._capturing = False
            self._capture_started_at = None
            self._utterance = bytearray()
            self._vad_carry = bytearray()
            self._silence_ms_acc = 0.0
            self._followup_scan_carry.clear()
            self._live_forward = False
            try:
                self.model.reset()
            except Exception:
                pass
            self._schedule_wake_listen_state(WAKE_LISTEN_WAKE_REQUIRED)

    def end_live_forwarding(self, enable_followup: bool = True) -> None:
        """Stop streaming state for this turn; optionally open follow-up window."""
        self._live_forward = False
        self._follow_up_turn_started = False
        try:
            self.model.reset()
        except Exception:
            pass
        if enable_followup:
            if self.get_follow_up_window_s is not None:
                self.begin_follow_up_window(self.get_follow_up_window_s())
            else:
                self.begin_follow_up_window()
        else:
            self._follow_up_until = None
            self._followup_scan_carry.clear()
            self._follow_up_turn_started = False
            self._schedule_wake_listen_state(WAKE_LISTEN_WAKE_REQUIRED)

    def _trim_ring(self) -> None:
        if len(self._ring) > self._pre_roll_bytes:
            del self._ring[: len(self._ring) - self._pre_roll_bytes]

    def _max_wake_score(self, scores: dict) -> float:
        if not scores:
            return 0.0
        out = 0.0
        for v in scores.values():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > out:
                out = fv
        return out

    async def feed_pcm(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % BYTES_PER_SAMPLE != 0:
            return

        now = time.monotonic()

        if self.paused:
            return

        if self._live_forward and self.on_live_pcm:
            await self.on_live_pcm(pcm)
            return

        if (
            self.use_gemini_live
            and self._follow_up_until is not None
            and now < self._follow_up_until
            and self.on_live_pcm
        ):
            await self.on_live_pcm(pcm)
            return

        if self._follow_up_until is not None and now >= self._follow_up_until:
            self._follow_up_until = None
            self._followup_scan_carry.clear()
            self._follow_up_turn_started = False
            if self.use_gemini_live:
                await self._await_wake_listen_state(WAKE_LISTEN_WAKE_REQUIRED)

        if self._capturing:
            self._utterance.extend(pcm)
            self._vad_carry.extend(pcm)
            done = self._process_vad_tail()
            min_ms = 400.0
            elapsed_ms = (
                (time.monotonic() - self._capture_started_at) * 1000.0
                if self._capture_started_at is not None
                else 0.0
            )
            if (
                (done and elapsed_ms >= min_ms)
                or len(self._utterance) > MAX_UTTERANCE_SEC * SAMPLE_RATE * BYTES_PER_SAMPLE
            ):
                await self._finalize_utterance()
            return

        # Follow-up window: VAD speech onset only (no wake phrase), REST mode only.
        if (
            not self.use_gemini_live
            and self._follow_up_until is not None
            and now < self._follow_up_until
            and now >= self._cooldown_until
        ):
            self._ring.extend(pcm)
            self._trim_ring()
            self._followup_scan_carry.extend(pcm)
            while len(self._followup_scan_carry) >= VAD_FRAME_BYTES:
                frame = bytes(self._followup_scan_carry[:VAD_FRAME_BYTES])
                del self._followup_scan_carry[:VAD_FRAME_BYTES]
                if not self.vad.is_speech(frame, SAMPLE_RATE):
                    continue
                if self.on_capture_start:
                    await self.on_capture_start()
                await self._await_wake_listen_state(WAKE_LISTEN_STREAMING)
                self._capturing = True
                self._capture_from_followup = True
                self._capture_started_at = time.monotonic()
                self._utterance = bytearray(self._ring)
                self._ring.clear()
                self._utterance.extend(frame)
                self._vad_carry = bytearray(frame)
                self._silence_ms_acc = 0.0
                while len(self._followup_scan_carry) >= VAD_FRAME_BYTES:
                    frame2 = bytes(self._followup_scan_carry[:VAD_FRAME_BYTES])
                    del self._followup_scan_carry[:VAD_FRAME_BYTES]
                    self._utterance.extend(frame2)
                    self._vad_carry.extend(frame2)
                    if self._process_vad_tail():
                        await self._finalize_utterance()
                        return
                rest = bytes(self._followup_scan_carry)
                self._followup_scan_carry.clear()
                if rest:
                    self._utterance.extend(rest)
                    self._vad_carry.extend(rest)
                    if self._process_vad_tail():
                        await self._finalize_utterance()
                return
            return

        # Follow-up window in live mode is model-driven (input transcription callback).
        if (
            self._follow_up_until is not None
            and now < self._follow_up_until
            and self.use_gemini_live
        ):
            return

        # Listening for wake (cooldown after wake capture; skipped for follow-up utterances)
        if now < self._cooldown_until:
            self._ring.extend(pcm)
            self._trim_ring()
            return

        self._ring.extend(pcm)
        self._trim_ring()

        audio_i16 = np.frombuffer(pcm, dtype=np.int16)
        try:
            scores = self.model.predict(audio_i16)
        except Exception as e:
            print(f"[wake] openWakeWord predict error: {e}")
            return

        if self._max_wake_score(scores) >= self.wake_threshold:
            self._followup_scan_carry.clear()
            if self.use_gemini_live and self.on_live_pcm:
                if self.on_capture_start:
                    await self.on_capture_start()
                if self.on_live_wake:
                    await self.on_live_wake()
                ring = bytes(self._ring)
                self._ring.clear()
                self._cooldown_until = time.monotonic() + self.cooldown_s
                self._live_forward = True
                await self._await_wake_listen_state(WAKE_LISTEN_STREAMING)
                if ring:
                    await self.on_live_pcm(ring)
                try:
                    self.model.reset()
                except Exception:
                    pass
                return
            if self.on_capture_start:
                await self.on_capture_start()
            await self._await_wake_listen_state(WAKE_LISTEN_STREAMING)
            self._capturing = True
            self._capture_from_followup = False
            self._capture_started_at = time.monotonic()
            self._utterance = bytearray(self._ring)
            self._vad_carry = bytearray()
            self._silence_ms_acc = 0.0
            self._ring.clear()
            return

    def _process_vad_tail(self) -> bool:
        """Consume vad_carry; return True if trailing silence exceeded threshold."""
        frame_ms = (VAD_FRAME_SAMPLES / SAMPLE_RATE) * 1000.0

        while len(self._vad_carry) >= VAD_FRAME_BYTES:
            frame = bytes(self._vad_carry[:VAD_FRAME_BYTES])
            del self._vad_carry[:VAD_FRAME_BYTES]
            is_speech = self.vad.is_speech(frame, SAMPLE_RATE)
            if is_speech:
                self._silence_ms_acc = 0.0
            else:
                self._silence_ms_acc += frame_ms

            if self._silence_ms_acc >= float(self.silence_ms):
                return True
        return False

    async def _finalize_utterance(self) -> None:
        raw = bytes(self._utterance)
        self._capturing = False
        self._capture_started_at = None
        self._utterance = bytearray()
        self._vad_carry = bytearray()
        self._silence_ms_acc = 0.0
        if not self._capture_from_followup:
            self._cooldown_until = time.monotonic() + self.cooldown_s
        self._capture_from_followup = False
        try:
            self.model.reset()
        except Exception:
            pass

        if self.on_capture_end:
            await self.on_capture_end()

        # Minimum audible length (~0.25 s) to avoid noise blips
        if len(raw) < int(0.25 * SAMPLE_RATE * BYTES_PER_SAMPLE):
            return

        await self.on_utterance(raw)