"""Data directory, persisted hub secrets, and effective API keys (environment overrides file)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


def _resolve_data_dir() -> Path:
    raw = (os.getenv("OMNIBOT_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent


DATA_DIR = _resolve_data_dir()
ENV_FILE = DATA_DIR / ".env"

# Prefer explicit .env next to app; fall back to cwd for backwards compatibility.
if ENV_FILE.is_file():
    load_dotenv(ENV_FILE)
else:
    load_dotenv()

SETTINGS_FILE = str(DATA_DIR / "bot_settings.json")
REMOVED_DEVICE_IDS_FILE = str(DATA_DIR / "removed_device_ids.json")
HUB_SECRETS_FILE = DATA_DIR / "hub_secrets.json"
HUB_APP_SETTINGS_FILE = DATA_DIR / "hub_app_settings.json"

# Must match app.py DEFAULT_TIMEZONE_RULE for merged defaults.
DEFAULT_HUB_TIMEZONE_RULE = "EST5EDT,M3.2.0/2,M11.1.0/2"

DEFAULT_NOMINATIM_USER_AGENT = (
    "OmnibotHub/1.0 (set NOMINATIM_USER_AGENT in .env - Nominatim usage policy)"
)

_secrets_lock = threading.Lock()
_secrets_file_cache: Optional[dict[str, Any]] = None

_genai_client: Any = None
_genai_key_used: Optional[str] = None
_genai_lock = threading.Lock()


def _read_secrets_file_raw() -> dict[str, Any]:
    if not HUB_SECRETS_FILE.is_file():
        return {}
    try:
        with open(HUB_SECRETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _secrets_from_file() -> dict[str, Any]:
    global _secrets_file_cache
    with _secrets_lock:
        if _secrets_file_cache is None:
            _secrets_file_cache = _read_secrets_file_raw()
        return dict(_secrets_file_cache)


def invalidate_secrets_cache() -> None:
    global _secrets_file_cache
    with _secrets_lock:
        _secrets_file_cache = None


def _write_secrets_file(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HUB_SECRETS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(HUB_SECRETS_FILE)
    invalidate_secrets_cache()


def merge_hub_secrets(updates: dict[str, Any]) -> dict[str, Any]:
    """Apply updates to hub_secrets.json. None = skip key. Empty string = remove key."""
    if not updates:
        return _read_secrets_file_raw()
    current = _read_secrets_file_raw()
    for k, v in updates.items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "" and k in {
            "gemini_api_key",
            "google_maps_js_api_key",
            "google_maps_static_api_key",
            "nominatim_user_agent",
        }:
            current.pop(k, None)
        else:
            current[k] = v
    _write_secrets_file(current)
    invalidate_genai_client()
    return current


def _env_first(env_name: str, file_key: str) -> str:
    v = (os.getenv(env_name) or "").strip()
    if v:
        return v
    return (str(_secrets_from_file().get(file_key) or "")).strip()


def get_gemini_api_key() -> str:
    return _env_first("GEMINI_API_KEY", "gemini_api_key")


def get_google_maps_js_api_key() -> str:
    return _env_first("GOOGLE_MAPS_JS_API_KEY", "google_maps_js_api_key")


def get_google_maps_static_api_key() -> str:
    return _env_first("GOOGLE_MAPS_STATIC_API_KEY", "google_maps_static_api_key")


def get_maps_key_static_then_js() -> str:
    s = get_google_maps_static_api_key()
    if s:
        return s
    return get_google_maps_js_api_key()


def get_maps_key_js_for_screenshot() -> str:
    """Maps widget / screenshot path prefers JS key."""
    return get_google_maps_js_api_key()


def get_nominatim_user_agent_raw() -> str:
    env = (os.getenv("NOMINATIM_USER_AGENT") or "").strip()
    if env:
        return env
    return (str(_secrets_from_file().get("nominatim_user_agent") or "")).strip()


def mask_secret(s: str) -> Optional[str]:
    if not s:
        return None
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]


def invalidate_genai_client() -> None:
    global _genai_client, _genai_key_used
    with _genai_lock:
        _genai_client = None
        _genai_key_used = None


def get_genai_client():
    """Return a google.genai.Client or None if no API key is configured."""
    from google import genai

    key = get_gemini_api_key()
    if not key:
        return None
    global _genai_client, _genai_key_used
    with _genai_lock:
        if _genai_client is not None and _genai_key_used == key:
            return _genai_client
        _genai_client = genai.Client(api_key=key)
        _genai_key_used = key
        return _genai_client


def _default_hub_app_settings_dict() -> dict[str, Any]:
    return {
        "timezone_rule": DEFAULT_HUB_TIMEZONE_RULE,
        "maps_grounding_enabled": False,
        "maps_street": "",
        "maps_state": "",
        "maps_postal_code": "",
        "maps_country": "",
        "maps_latitude": None,
        "maps_longitude": None,
        "maps_display_name": None,
    }


def _migrate_hub_app_from_legacy_bot_settings() -> dict[str, Any]:
    """If hub_app_settings.json is missing, lift timezone/maps from default_bot in bot_settings.json."""
    p = Path(SETTINGS_FILE)
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            all_data = json.load(f)
    except Exception:
        return {}
    leg = all_data.get("default_bot") or {}
    out: dict[str, Any] = {}
    for k in _default_hub_app_settings_dict().keys():
        if k in leg:
            out[k] = leg[k]
    return out


def load_hub_app_settings() -> dict[str, Any]:
    base = _default_hub_app_settings_dict()
    if HUB_APP_SETTINGS_FILE.is_file():
        try:
            with open(HUB_APP_SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                base.update(data)
                return base
        except Exception:
            pass
    migrated = _migrate_hub_app_from_legacy_bot_settings()
    if migrated:
        base.update(migrated)
        save_hub_app_settings(base)
    return base


def save_hub_app_settings(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HUB_APP_SETTINGS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(HUB_APP_SETTINGS_FILE)


def hub_public_status() -> dict[str, Any]:
    gk = get_gemini_api_key()
    return {
        "gemini_configured": bool(gk),
        "data_dir": str(DATA_DIR),
    }


def hub_settings_public_view() -> dict[str, Any]:
    """Non-secret hub settings for GET /api/hub/settings (keys are masked)."""
    gk = get_gemini_api_key()
    js = get_google_maps_js_api_key()
    st = get_google_maps_static_api_key()
    env_nomi = bool((os.getenv("NOMINATIM_USER_AGENT") or "").strip())
    stored_nomi = (str(_secrets_from_file().get("nominatim_user_agent") or "")).strip()
    return {
        "gemini_api_key_configured": bool(gk),
        "gemini_api_key_masked": mask_secret(gk) if gk else None,
        "google_maps_js_api_key_configured": bool(js),
        "google_maps_js_api_key_masked": mask_secret(js) if js else None,
        "google_maps_static_api_key_configured": bool(st),
        "google_maps_static_api_key_masked": mask_secret(st) if st else None,
        "nominatim_user_agent": stored_nomi,
        "nominatim_user_agent_from_env": env_nomi,
        "data_dir": str(DATA_DIR),
    }
