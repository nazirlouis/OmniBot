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
"""

from __future__ import annotations

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

    def set_paused(self, p: bool) -> None:
        self.paused = bool(p)
        if self.paused:
            self._capturing = False
            self._capture_started_at = None
            self._utterance = bytearray()
            self._vad_carry = bytearray()
            self._silence_ms_acc = 0.0
            try:
                self.model.reset()
            except Exception:
                pass

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

        # Listening for wake
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
            self._capturing = True
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
        self._cooldown_until = time.monotonic() + self.cooldown_s
        try:
            self.model.reset()
        except Exception:
            pass

        # Minimum audible length (~0.25 s) to avoid noise blips
        if len(raw) < int(0.25 * SAMPLE_RATE * BYTES_PER_SAMPLE):
            return

        await self.on_utterance(raw)