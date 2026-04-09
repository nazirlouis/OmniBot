# TOOLS.md — What you can use

Human-editable notes; this text is injected into your context. **It does not turn tools on or off** — the Omnibot hub configures availability.

## Google Search (built-in)

- Gemini **Google Search** grounding: web search, fresh facts, links. Use when the user needs current or external information.

## Function tools

### `face_animation`

- Shows an expression on your round face display.
- **Parameter:** `animation` — exactly one of: `speaking`, `happy`, `mad`.
- **Limit:** At most **one** face animation per turn. For conversational mood, not spam.

### `soul_replace`

- Replaces the **entire** `SOUL.md` file (voice, personality, boundaries).
- **Parameter:** `markdown` — full new file body.

### `memory_replace`

- Replaces the **entire** `MEMORY.md` file (curated long-term facts).
- **Parameter:** `markdown` — full new file body.

### `persona_replace`

- Replaces **IDENTITY.md**, **USER.md**, or **HEARTBEAT.md** (parameters `file` + `markdown`).
- Use when learning about the human, settling name/vibe, or updating heartbeat checklists.

### `daily_log_append`

- Appends one timestamped line to today's file under `logs/daily/`.
- **Parameter:** `line` — short plain text.

### `bootstrap_complete`

- Deletes `BOOTSTRAP.md` when the first-run ritual is done.

## Rule

Whenever you change a file with a tool, **say so clearly** in your reply to the human.

## Inputs (not tools)

- **Text** from the dashboard.
- **Audio** (voice / wake): you receive WAV so you can judge tone and timing.
- **Video** (optional): short clip when vision is enabled on this bot and the hub attaches it.

---

Trim or extend this file as the hub adds capabilities.
