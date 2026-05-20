"""
Ollama adapter — quacks like ``google.genai.Client`` for OmniBot's chat path.

Activated by setting ``OMNIBOT_OLLAMA_URL`` (e.g. ``http://localhost:11434``).
When active, :func:`hub_config.get_genai_client` returns an :class:`OllamaClient`
instance whose ``chats.create()`` / ``send_message_stream()`` route to the local
Ollama server. The ``model`` field on the bot's settings becomes the Ollama tag
(e.g. ``gemma3:4b``, ``llama3.2:3b``).

Limitations (acceptable for the dashboard text-chat use case):
  - **No function calling / tool invocation.** OmniBot's persona tools
    (``soul_replace``, ``memory_replace``, ``face_animation``, etc.) are silently
    skipped — Ollama models don't expose Gemini's function-call schema.
  - **No multimodal input.** Images and audio are dropped. Text in, text out.
  - **No grounding metadata / web search.** ``chunk.candidates`` is always ``[]``.
  - Streams cumulative text chunks (matches the Gemini SDK's default behavior
    that ``app.py`` already handles at the chunk-merge step).

Chat history is round-trippable: :meth:`OllamaChat.get_history` emits objects
with ``.role`` and ``.parts`` attributes that the next
``chats.create(history=...)`` call accepts unchanged.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Minimal Gemini-shaped value objects.
# ---------------------------------------------------------------------------

class _FakePart:
    """Mimics google.genai.types.Part — only the ``text`` attribute is used."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _FakeContent:
    """Mimics google.genai.types.Content — ``.role`` ∈ {'user','model'}."""
    __slots__ = ("role", "parts")

    def __init__(self, role: str, parts: List[_FakePart]):
        self.role = role
        self.parts = parts


class _FakeChunk:
    """Mimics a streamed chunk from genai. Cumulative text; no candidates."""
    __slots__ = ("text", "candidates")

    def __init__(self, text: str):
        self.text = text
        # Empty list so app.py's tool-call introspection finds nothing.
        self.candidates = []


class _FakeResponse:
    """Non-streaming response (returned by send_message). Same shape callers
    expect from google.genai: ``.text`` + ``.candidates`` (always empty for
    Ollama since the model can't function-call)."""
    __slots__ = ("text", "candidates")

    def __init__(self, text: str):
        self.text = text
        self.candidates = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(message: Any) -> str:
    """Best-effort text extraction. Accepts str / Content-like / list-of-parts."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    parts = getattr(message, "parts", None)
    if parts is not None:
        return "".join((getattr(p, "text", "") or "") for p in parts)
    if isinstance(message, list):
        return "".join(_extract_text(p) for p in message)
    text_attr = getattr(message, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    return ""


def _system_instruction_text(config: Any) -> str:
    """Pull the system instruction string out of a genai GenerateContentConfig."""
    si = getattr(config, "system_instruction", None) if config is not None else None
    return _extract_text(si)


# ---------------------------------------------------------------------------
# Chat + Client
# ---------------------------------------------------------------------------

class OllamaChat:
    """One conversation session against an Ollama ``/api/chat`` endpoint."""

    def __init__(self, url: str, model: str, config: Any, history: Optional[list]):
        self._url = url.rstrip("/")
        self._model = model
        self._messages: List[dict] = []

        si = _system_instruction_text(config)
        if si:
            self._messages.append({"role": "system", "content": si})

        for item in history or []:
            role = getattr(item, "role", None) or "user"
            # Gemini's "model" role maps to Ollama/OpenAI's "assistant".
            if role == "model":
                role = "assistant"
            elif role not in ("user", "assistant", "system"):
                role = "user"
            text = _extract_text(item)
            if text:
                self._messages.append({"role": role, "content": text})

    def send_message_stream(self, message: Any) -> Iterator[_FakeChunk]:
        """Yield cumulative-text chunks until Ollama reports ``done``."""
        msg_text = _extract_text(message)
        self._messages.append({"role": "user", "content": msg_text})

        accumulated = ""
        try:
            with httpx.stream(
                "POST",
                f"{self._url}/api/chat",
                json={
                    "model": self._model,
                    "messages": self._messages,
                    "stream": True,
                },
                timeout=httpx.Timeout(
                    connect=10.0, read=300.0, write=10.0, pool=10.0
                ),
            ) as r:
                # Ollama returns HTTP 200 even on model-not-found; the error
                # surfaces inside the NDJSON stream as {"error": "..."}.
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in obj:
                        raise RuntimeError(f"ollama error: {obj['error']}")
                    msg = obj.get("message") or {}
                    delta = msg.get("content") or ""
                    if delta:
                        accumulated += delta
                        yield _FakeChunk(accumulated)
                    if obj.get("done"):
                        break
        finally:
            # Always persist the assistant turn (partial or full) so a
            # subsequent get_history() reflects what the user saw.
            if accumulated:
                self._messages.append(
                    {"role": "assistant", "content": accumulated}
                )

    def send_message(self, message: Any) -> _FakeResponse:
        """Non-streaming variant — consume the entire stream then return.

        Used by callers that want a single resolved response (e.g. the
        heartbeat maintenance loop). Candidates are always empty because
        Ollama can't function-call, which the heartbeat caller correctly
        treats as "no tool calls; break". The heartbeat pass therefore
        becomes a no-op under Ollama, which is the right behavior."""
        last_text = ""
        for chunk in self.send_message_stream(message):
            last_text = chunk.text  # cumulative
        return _FakeResponse(last_text)

    def get_history(self, curated: bool = False) -> List[_FakeContent]:
        """Return a Gemini-shaped history (skipping the system message)."""
        out: List[_FakeContent] = []
        for m in self._messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else "user"
            out.append(_FakeContent(role=role, parts=[_FakePart(text=m["content"])]))
        return out


class _OllamaChats:
    def __init__(self, url: str):
        self._url = url

    def create(self, *, model: str, config: Any = None, history: Optional[list] = None):
        return OllamaChat(self._url, model, config, history)


class OllamaClient:
    """Top-level facade. Exposes ``.chats.create(...)`` like genai.Client."""

    def __init__(self, url: str):
        self._url = url
        self.chats = _OllamaChats(url)


# ---------------------------------------------------------------------------
# Env detection
# ---------------------------------------------------------------------------

def get_ollama_url() -> Optional[str]:
    """Return the configured Ollama base URL, or ``None`` if unset.

    Set ``OMNIBOT_OLLAMA_URL`` in ``app/backend/.env`` or the process env to
    activate. Trailing slash is stripped by the chat session.
    """
    u = (os.environ.get("OMNIBOT_OLLAMA_URL") or "").strip()
    return u or None
