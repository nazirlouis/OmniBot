"""Periodic HEARTBEAT maintenance: consolidate logs into MEMORY via Gemini (memory_replace only)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from typing import Any

from google.genai import types

from hub_config import (
    GEMINI_THINKING_LEVEL_AUTO,
    get_genai_client,
    get_gemini_api_key,
    normalize_gemini_thinking_level,
)
from persona import (
    build_composed_system_instruction,
    get_heartbeat_state,
    list_all_device_ids_for_heartbeat,
    read_heartbeat_instructions,
    read_persona_markdown,
    replace_memory_markdown,
    set_heartbeat_state,
    tail_recent_daily_logs,
)


def _pixel_base_rules_for_heartbeat() -> str:
    """Minimal hub rules for maintenance (no user-facing reply style)."""
    return (
        "You are the hub maintenance brain for Pixel. Follow HEARTBEAT.md and read RECENT_DAILY_LOGS in the user message. "
        "You may only change MEMORY.md (via memory_replace). Do not use soul_replace in this pass; SOUL, IDENTITY, USER, and TOOLS are out of scope. "
        "Use only the provided tools. Do not invent web facts. Be concise; the user does not see this turn unless you later add proactive speech support."
    )


MEMORY_REPLACE_DECLARATION = {
    "name": "memory_replace",
    "description": (
        "Replace the entire MEMORY.md file with updated curated markdown. "
        "Use when consolidating daily logs or fixing memory (MEMORY.md only in this maintenance pass). "
        "Max size enforced server-side."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Full new body for MEMORY.md (complete file).",
            },
        },
        "required": ["markdown"],
    },
}


def _heartbeat_generate_config(*, system_instruction: str, thinking_level: str) -> types.GenerateContentConfig:
    custom = [types.FunctionDeclaration(**MEMORY_REPLACE_DECLARATION)]
    kw: dict = {
        "system_instruction": system_instruction,
        "tools": [types.Tool(function_declarations=custom)],
        "tool_config": types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        ),
    }
    if thinking_level != GEMINI_THINKING_LEVEL_AUTO:
        kw["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    return types.GenerateContentConfig(**kw)


async def run_heartbeat_tick(
    device_id: str,
    *,
    gemini_turn_lock: asyncio.Lock,
    default_model: str,
    get_bot_settings_fn,
) -> None:
    if not get_gemini_api_key():
        return

    async with gemini_turn_lock:
        bs = get_bot_settings_fn(device_id)
        if not bs.get("heartbeat_enabled", True):
            return
        interval = int(bs.get("heartbeat_interval_minutes", 30) or 30)
        if interval < 1:
            return

        model = bs.get("model") or default_model
        hb_text = read_heartbeat_instructions(device_id)
        logs_tail = tail_recent_daily_logs(device_id)
        memory_current = read_persona_markdown(device_id, "memory")

        user_msg = (
            "Run your HEARTBEAT maintenance pass now.\n\n"
            f"=== HEARTBEAT_MD ===\n{hb_text}\n\n"
            f"=== RECENT_DAILY_LOGS ===\n{logs_tail}\n\n"
            f"=== CURRENT_MEMORY_MD ===\n{memory_current}\n"
        )

        sys_instr = build_composed_system_instruction(
            device_id,
            _pixel_base_rules_for_heartbeat(),
            for_heartbeat_maintenance=True,
        )

        gc = get_genai_client()
        if gc is None:
            return

        config = _heartbeat_generate_config(
            system_instruction=sys_instr,
            thinking_level=normalize_gemini_thinking_level(bs.get("thinking_level")),
        )
        loop = asyncio.get_running_loop()

        def _run_chat() -> tuple[str, bool]:
            chat = gc.chats.create(model=model, config=config, history=[])
            msg: Any = user_msg
            memory_updated = False
            for _ in range(5):
                resp = chat.send_message(msg)
                fcalls = []
                if resp.candidates:
                    for c in resp.candidates:
                        if c.content and c.content.parts:
                            for p in c.content.parts:
                                fc = getattr(p, "function_call", None)
                                if fc:
                                    fcalls.append(fc)
                if not fcalls:
                    break
                parts_out = []
                for fc in fcalls:
                    if fc.name != "memory_replace":
                        parts_out.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=fc.name,
                                    response={"result": {"ok": False, "error": "unknown function"}},
                                    id=fc.id,
                                )
                            )
                        )
                        continue
                    args = dict(fc.args) if fc.args else {}
                    md = str(args.get("markdown") or "")
                    result = replace_memory_markdown(device_id, md)
                    if result.get("ok"):
                        memory_updated = True
                    parts_out.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                name="memory_replace",
                                response={"result": result},
                                id=fc.id,
                            )
                        )
                    )
                msg = parts_out
            return "", memory_updated

        try:
            _, updated = await loop.run_in_executor(None, partial(_run_chat))
            set_heartbeat_state(
                device_id,
                last_run_utc=datetime.now(timezone.utc).isoformat(),
                last_memory_updated=updated,
            )
        except Exception as e:
            print(f"[Omnibot/heartbeat] device_id={device_id!r} error: {e}")


async def heartbeat_supervisor_loop(
    *,
    default_model: str,
    get_bot_settings_fn,
    gemini_turn_locks: dict[str, asyncio.Lock],
) -> None:
    """Wake periodically; for each device, run tick if interval elapsed."""
    while True:
        try:
            await asyncio.sleep(30.0)
            if not get_gemini_api_key():
                continue
            for device_id in list_all_device_ids_for_heartbeat():
                bs = get_bot_settings_fn(device_id)
                if not bs.get("heartbeat_enabled", True):
                    continue
                interval_min = max(1, int(bs.get("heartbeat_interval_minutes", 30) or 30))
                st = get_heartbeat_state(device_id)
                last = st.get("last_run_utc")
                now = datetime.now(timezone.utc)
                need = False
                if not last:
                    need = True
                else:
                    try:
                        from datetime import datetime as dt

                        lp = dt.fromisoformat(str(last).replace("Z", "+00:00"))
                        if lp.tzinfo is None:
                            lp = lp.replace(tzinfo=timezone.utc)
                        delta_sec = (now - lp).total_seconds()
                        if delta_sec >= interval_min * 60:
                            need = True
                    except Exception:
                        need = True
                if not need:
                    continue
                lock = gemini_turn_locks.setdefault(device_id, asyncio.Lock())
                await run_heartbeat_tick(
                    device_id,
                    gemini_turn_lock=lock,
                    default_model=default_model,
                    get_bot_settings_fn=get_bot_settings_fn,
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Omnibot/heartbeat] supervisor error: {e}")
            await asyncio.sleep(5.0)
