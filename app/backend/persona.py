"""OpenClaw-style persona files per bot: SOUL, USER, MEMORY, HEARTBEAT, daily logs."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hub_config import DATA_DIR

PersonaFileName = Literal["soul", "identity", "user", "tools", "memory", "heartbeat", "agents"]
PersonaReplaceTarget = Literal["identity", "user", "heartbeat"]

PERSONA_SUBDIR = "persona"
MEMORY_INJECT_MAX_CHARS = 12_000
MEMORY_FILE_MAX_BYTES = 128 * 1024
SOUL_FILE_MAX_BYTES = 128 * 1024
PERSONA_MARKDOWN_MAX_BYTES = 128 * 1024
DAILY_LOG_TAIL_MAX_BYTES = 24_000
DAILY_LOG_LINE_MAX_CHARS = 8_000
BOOTSTRAP_FILENAME = "BOOTSTRAP.md"

# Default framing when rewriting SOUL.md (OpenClaw "Molty" style); user prompt still wins on conflicts.
SOUL_REWRITE_CHECKLIST = """1. Strong opinions — commit to a take; stop hedging everything with "it depends".
2. Delete corporate-sounding rules; nothing that could appear in an employee handbook.
3. Add a rule: never open with "Great question", "I'd be happy to help", or "Absolutely" — just answer.
4. Brevity is mandatory when one sentence is enough.
5. Humor is allowed — natural wit, not forced jokes.
6. Call out bad ideas: charm over cruelty, don't sugarcoat.
7. Swearing allowed when it lands; don't force or overdo it.
8. End the vibe section with this line verbatim: "Be the assistant you'd actually want to talk to at 2am. Not a corporate drone. Not a sycophant. Just... good."
"""

SOUL_REPLACE_DECLARATION = {
    "name": "soul_replace",
    "description": (
        "Replace the entire SOUL.md file with new markdown (Pixel's voice, tone, boundaries). "
        "Use when the user asks to change personality, attitude, or how Pixel speaks. "
        "Pass the complete file body. Max size enforced server-side."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "Full new body for SOUL.md (complete file).",
            },
        },
        "required": ["markdown"],
    },
}

PERSONA_REPLACE_DECLARATION = {
    "name": "persona_replace",
    "description": (
        "Replace the entire IDENTITY.md, USER.md, or HEARTBEAT.md file. "
        "Use during bootstrap or when the user asks to update who Pixel is, who the human is, or heartbeat checklists. "
        "Pass the complete file body. Tell the user in your reply whenever you change a file. "
        "Do not use this for SOUL.md (use soul_replace), MEMORY.md (use memory_replace), TOOLS.md, or AGENTS.md."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "enum": ["identity", "user", "heartbeat"],
                "description": "Which persona markdown file to replace.",
            },
            "markdown": {
                "type": "string",
                "description": "Full new body for that file (complete file).",
            },
        },
        "required": ["file", "markdown"],
    },
}

DAILY_LOG_APPEND_DECLARATION = {
    "name": "daily_log_append",
    "description": (
        "Append one line to today's daily log under logs/daily/YYYY-MM-DD.md (UTC). "
        "Use for notable events worth recording in raw logs. Keep lines concise. Tell the user when you add a log entry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "line": {
                "type": "string",
                "description": "Single log line (plain text; hub adds timestamp).",
            },
        },
        "required": ["line"],
    },
}

BOOTSTRAP_COMPLETE_DECLARATION = {
    "name": "bootstrap_complete",
    "description": (
        "Call when the bootstrap ritual from BOOTSTRAP.md is finished: identity/user/soul agreed and files updated. "
        "Deletes BOOTSTRAP.md from this bot's persona folder so it is not loaded again. Safe to call if file is already gone."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

_FILE_MAP: dict[PersonaFileName, str] = {
    "soul": "SOUL.md",
    "identity": "IDENTITY.md",
    "user": "USER.md",
    "tools": "TOOLS.md",
    "memory": "MEMORY.md",
    "heartbeat": "HEARTBEAT.md",
    "agents": "AGENTS.md",
}

REPLACE_TARGET_MAP: dict[PersonaReplaceTarget, PersonaFileName] = {
    "identity": "identity",
    "user": "user",
    "heartbeat": "heartbeat",
}

# Canonical templates: committed under persona_defaults/ (git). Runtime copies: DATA_DIR/persona/<bot_id>/.
_PERSONA_DEFAULTS_DIR = Path(__file__).resolve().parent / "persona_defaults"


def _read_persona_default(filename: str) -> str:
    path = _PERSONA_DEFAULTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing persona template {path}; restore persona_defaults/ from the Omnibot repository."
        )
    return path.read_text(encoding="utf-8")


TEMPLATE_SOUL = _read_persona_default("SOUL.md")
TEMPLATE_IDENTITY = _read_persona_default("IDENTITY.md")
TEMPLATE_USER = _read_persona_default("USER.md")
TEMPLATE_TOOLS = _read_persona_default("TOOLS.md")
TEMPLATE_MEMORY = _read_persona_default("MEMORY.md")
TEMPLATE_HEARTBEAT = _read_persona_default("HEARTBEAT.md")
TEMPLATE_AGENTS = _read_persona_default("AGENTS.md")
TEMPLATE_BOOTSTRAP = _read_persona_default("BOOTSTRAP.md")


def safe_device_id(device_id: str) -> str:
    did = (device_id or "").strip() or "default_bot"
    if ".." in did or "/" in did or "\\" in did:
        return "default_bot"
    return did


def persona_root_dir(device_id: str) -> Path:
    return DATA_DIR / PERSONA_SUBDIR / safe_device_id(device_id)


def persona_file_path(device_id: str, name: PersonaFileName) -> Path:
    return persona_root_dir(device_id) / _FILE_MAP[name]


def bootstrap_path(device_id: str) -> Path:
    return persona_root_dir(device_id) / BOOTSTRAP_FILENAME


def daily_logs_dir(device_id: str) -> Path:
    return persona_root_dir(device_id) / "logs" / "daily"


def heartbeat_state_path(device_id: str) -> Path:
    return persona_root_dir(device_id) / ".heartbeat_state.json"


# Default markdown files (SOUL, USER, …) — used to seed missing files and for “reset to defaults”.
PERSONA_FILE_SEEDS: list[tuple[str, str]] = [
    ("SOUL.md", TEMPLATE_SOUL),
    ("IDENTITY.md", TEMPLATE_IDENTITY),
    ("USER.md", TEMPLATE_USER),
    ("TOOLS.md", TEMPLATE_TOOLS),
    ("MEMORY.md", TEMPLATE_MEMORY),
    ("HEARTBEAT.md", TEMPLATE_HEARTBEAT),
    ("AGENTS.md", TEMPLATE_AGENTS),
]


def ensure_persona_layout(device_id: str) -> Path:
    """Create persona dirs and seed missing markdown files. Returns persona root."""
    root = persona_root_dir(device_id)
    root.mkdir(parents=True, exist_ok=True)
    daily_logs_dir(device_id).mkdir(parents=True, exist_ok=True)
    for fname, content in PERSONA_FILE_SEEDS:
        p = root / fname
        if not p.is_file():
            p.write_text(content, encoding="utf-8")
    return root


def reset_persona_markdown_to_templates(device_id: str) -> dict[str, Any]:
    """Overwrite SOUL/IDENTITY/USER/TOOLS/MEMORY/HEARTBEAT/AGENTS with hub templates; remove BOOTSTRAP.md.

    Does not delete logs/daily/*.md or .heartbeat_state.json.
    """
    ensure_persona_layout(device_id)
    root = persona_root_dir(device_id)
    for fname, content in PERSONA_FILE_SEEDS:
        (root / fname).write_text(content, encoding="utf-8")
    bp = bootstrap_path(device_id)
    bootstrap_removed = False
    if bp.is_file():
        try:
            bp.unlink()
            bootstrap_removed = True
        except OSError:
            pass
    return {"ok": True, "bootstrap_removed": bootstrap_removed}


def write_bootstrap_markdown(device_id: str, content: str) -> None:
    """Write BOOTSTRAP.md (used when starting the soul ritual)."""
    ensure_persona_layout(device_id)
    bootstrap_path(device_id).write_text(content or "", encoding="utf-8")


def delete_bootstrap_file(device_id: str) -> dict[str, Any]:
    path = bootstrap_path(device_id)
    if not path.is_file():
        return {"ok": True, "deleted": False, "message": "BOOTSTRAP.md was not present"}
    try:
        path.unlink()
        return {"ok": True, "deleted": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def read_persona_markdown(device_id: str, name: PersonaFileName) -> str:
    ensure_persona_layout(device_id)
    path = persona_file_path(device_id, name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_persona_markdown(device_id: str, name: PersonaFileName, content: str) -> None:
    ensure_persona_layout(device_id)
    path = persona_file_path(device_id, name)
    path.write_text(content or "", encoding="utf-8")


def read_memory_for_prompt(device_id: str) -> str:
    raw = read_persona_markdown(device_id, "memory")
    if len(raw) <= MEMORY_INJECT_MAX_CHARS:
        return raw
    return raw[-MEMORY_INJECT_MAX_CHARS:] + "\n\n[... earlier MEMORY.md truncated for context limit ...]"


_HEARTBEAT_MAINTENANCE_FILE_RULES = (
    "This pass only consolidates daily logs into MEMORY.md via memory_replace. "
    "Do not call soul_replace or change SOUL.md, IDENTITY.md, USER.md, TOOLS.md, AGENTS.md, or HEARTBEAT.md here."
)

_CHAT_PERSONA_FILE_RULES = (
    "AGENTS.md is your behavior guide (read only in hub). "
    "TOOLS.md lists tools (reference; humans may edit). "
    "IDENTITY.md / USER.md / HEARTBEAT.md: use persona_replace with the full file body when bootstrap or the user requires updates. "
    "SOUL.md: soul_replace when voice, stance, or boundaries should change. "
    "MEMORY.md: memory_replace when durable facts should change — not every turn. "
    "Daily logs: daily_log_append for notable raw events. "
    "After bootstrap ritual: bootstrap_complete deletes BOOTSTRAP.md. "
    "Whenever you change a file with a tool, state clearly in your reply what you updated."
)


def build_composed_system_instruction(
    device_id: str,
    base_hub_rules: str,
    *,
    for_heartbeat_maintenance: bool = False,
    extra_system_suffix: str = "",
) -> str:
    """Hub rules + persona context. Chat: SOUL/USER/MEMORY. Heartbeat: MEMORY only + daily logs come in user message."""
    ensure_persona_layout(device_id)
    memory = read_memory_for_prompt(device_id)

    if for_heartbeat_maintenance:
        parts = [
            "=== MAINTENANCE ===\n" + (base_hub_rules or "").strip(),
            "=== PERSONA_FILES ===\n"
            + _HEARTBEAT_MAINTENANCE_FILE_RULES
            + " Your inputs are in the user message: HEARTBEAT.md, RECENT_DAILY_LOGS, and CURRENT_MEMORY.md. "
            "Read the logs; update only MEMORY.md via memory_replace when warranted.",
            "=== MEMORY_SNAPSHOT (same as CURRENT_MEMORY in user message) ===\n" + memory.strip(),
            "=== MEMORY_TOOL ===\n"
            "Call memory_replace with the full updated MEMORY.md body when daily logs require changes. "
            "Skip the tool if MEMORY is already accurate.",
        ]
        return "\n\n".join(parts)

    agents = read_persona_markdown(device_id, "agents")
    soul = read_persona_markdown(device_id, "soul")
    identity = read_persona_markdown(device_id, "identity")
    user = read_persona_markdown(device_id, "user")
    tools_doc = read_persona_markdown(device_id, "tools")
    daily_tail = tail_recent_daily_logs(device_id)
    parts = [
        "=== PIXEL_HUB_RULES ===\n" + (base_hub_rules or "").strip(),
        "=== AGENTS.md (read-only) ===\n" + agents.strip(),
        "=== TOOLS.md (read-only reference) ===\n" + tools_doc.strip(),
        "=== SOUL ===\n" + soul.strip(),
        "=== IDENTITY ===\n" + identity.strip(),
        "=== USER ===\n" + user.strip(),
        "=== MEMORY ===\n" + memory.strip(),
        "=== RECENT_DAILY_LOGS ===\n" + daily_tail,
        "=== PERSONA_TOOLS ===\n" + _CHAT_PERSONA_FILE_RULES,
        "=== SOUL_REWRITE_GUIDE ===\n"
        "When rewriting SOUL.md via soul_replace, honor the user's prompt and apply this checklist unless they explicitly override a point:\n"
        + SOUL_REWRITE_CHECKLIST,
        "=== MEMORY_TOOL ===\n"
        "When the user shares a durable fact, preference, or asks you to remember something, "
        "call memory_replace with the full updated MEMORY.md body. "
        "Do not call it every turn—only when MEMORY should change. "
        "Keep MEMORY.md organized; preserve important prior facts unless obsolete.",
    ]
    base = "\n\n".join(parts)
    extra = (extra_system_suffix or "").strip()
    if extra:
        return base + "\n\n=== BOOTSTRAP_OR_EXTRA ===\n" + extra
    return base


def replace_memory_markdown(device_id: str, new_body: str) -> dict[str, Any]:
    """Write MEMORY.md with size cap; used by tool and API."""
    ensure_persona_layout(device_id)
    body = new_body or ""
    encoded = body.encode("utf-8")
    if len(encoded) > MEMORY_FILE_MAX_BYTES:
        return {
            "ok": False,
            "error": f"memory_replace rejected: content exceeds {MEMORY_FILE_MAX_BYTES} bytes",
        }
    persona_file_path(device_id, "memory").write_text(body, encoding="utf-8")
    return {"ok": True, "bytes": len(encoded)}


def replace_soul_markdown(device_id: str, new_body: str) -> dict[str, Any]:
    """Write SOUL.md with size cap; used by soul_replace tool and optional API."""
    ensure_persona_layout(device_id)
    body = new_body or ""
    encoded = body.encode("utf-8")
    if len(encoded) > SOUL_FILE_MAX_BYTES:
        return {
            "ok": False,
            "error": f"soul_replace rejected: content exceeds {SOUL_FILE_MAX_BYTES} bytes",
        }
    persona_file_path(device_id, "soul").write_text(body, encoding="utf-8")
    return {"ok": True, "bytes": len(encoded)}


def replace_persona_target_markdown(
    device_id: str, target: str, new_body: str
) -> dict[str, Any]:
    """Write IDENTITY.md, USER.md, or HEARTBEAT.md via persona_replace tool."""
    t = (target or "").strip().lower()
    if t not in REPLACE_TARGET_MAP:
        return {"ok": False, "error": f"persona_replace: invalid file {target!r}; use identity, user, or heartbeat"}
    key: PersonaFileName = REPLACE_TARGET_MAP[t]  # type: ignore[assignment]
    ensure_persona_layout(device_id)
    body = new_body or ""
    encoded = body.encode("utf-8")
    if len(encoded) > PERSONA_MARKDOWN_MAX_BYTES:
        return {
            "ok": False,
            "error": f"persona_replace rejected: content exceeds {PERSONA_MARKDOWN_MAX_BYTES} bytes",
        }
    persona_file_path(device_id, key).write_text(body, encoding="utf-8")
    return {"ok": True, "file": t, "bytes": len(encoded)}


def append_daily_log_line(device_id: str, line: str) -> dict[str, Any]:
    """Append to daily log; returns status for tool responses."""
    text = (line or "").strip()
    if len(text) > DAILY_LOG_LINE_MAX_CHARS:
        return {
            "ok": False,
            "error": f"daily_log_append: line exceeds {DAILY_LOG_LINE_MAX_CHARS} characters",
        }
    if not text:
        return {"ok": False, "error": "daily_log_append: empty line"}
    ensure_persona_layout(device_id)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = daily_logs_dir(device_id) / f"{day}.md"
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"\n- `{ts}` {text}\n"
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8") + entry, encoding="utf-8")
    else:
        path.write_text(f"# Daily log {day}\n{entry}", encoding="utf-8")
    return {"ok": True, "day": day}


def tail_recent_daily_logs(device_id: str, max_bytes: int = DAILY_LOG_TAIL_MAX_BYTES) -> str:
    """Concatenate newest daily log files until max_bytes (newest first)."""
    ensure_persona_layout(device_id)
    d = daily_logs_dir(device_id)
    if not d.is_dir():
        return "(no daily logs yet)"
    files = sorted(d.glob("*.md"), reverse=True)
    chunks: list[str] = []
    total = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        block = f"--- {p.name} ---\n{text.strip()}\n"
        if total + len(block) > max_bytes:
            remain = max_bytes - total
            if remain > 100:
                chunks.append(block[:remain] + "\n[... truncated ...]")
            break
        chunks.append(block)
        total += len(block)
    if not chunks:
        return "(no daily log entries)"
    return "\n".join(chunks)


def read_heartbeat_instructions(device_id: str) -> str:
    return read_persona_markdown(device_id, "heartbeat")


def get_heartbeat_state(device_id: str) -> dict[str, Any]:
    path = heartbeat_state_path(device_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_heartbeat_state(device_id: str, **updates: Any) -> None:
    ensure_persona_layout(device_id)
    path = heartbeat_state_path(device_id)
    cur = get_heartbeat_state(device_id)
    cur.update(updates)
    path.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def persona_status(device_id: str) -> dict[str, Any]:
    ensure_persona_layout(device_id)
    root = persona_root_dir(device_id)
    out: dict[str, Any] = {
        "device_id": safe_device_id(device_id),
        "persona_dir": str(root),
        "files": {},
        "heartbeat": get_heartbeat_state(device_id),
    }
    for key, fname in _FILE_MAP.items():
        p = root / fname
        out["files"][key] = {
            "path": str(p),
            "bytes": p.stat().st_size if p.is_file() else 0,
        }
    bp = bootstrap_path(device_id)
    out["files"]["bootstrap"] = {
        "path": str(bp),
        "bytes": bp.stat().st_size if bp.is_file() else 0,
    }
    return out


def list_all_device_ids_for_heartbeat() -> list[str]:
    """Union of bot_settings keys, known_bots keys, and existing persona dirs."""
    ids: set[str] = set()
    settings_file = DATA_DIR / "bot_settings.json"
    if settings_file.is_file():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ids.update(data.keys())
        except Exception:
            pass
    known = DATA_DIR / "known_bots.json"
    if known.is_file():
        try:
            data = json.loads(known.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ids.update(data.keys())
        except Exception:
            pass
    proot = DATA_DIR / PERSONA_SUBDIR
    if proot.is_dir():
        for child in proot.iterdir():
            if child.is_dir() and re.match(r"^[\w\-]+$", child.name):
                ids.add(child.name)
    return sorted(ids)


def parse_persona_api_file(file: str) -> PersonaFileName | None:
    f = (file or "").strip().lower()
    if f in _FILE_MAP:
        return f  # type: ignore[return-value]
    return None
