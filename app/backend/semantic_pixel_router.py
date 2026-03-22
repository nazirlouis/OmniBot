"""Semantic routing: local/geo user intent -> Maps grounding; else Google Search.

Weather-related turns are forced to **search** so Gemini uses Google Search (and
``show_weather`` / ``face_animation`` weather) instead of Maps grounding.

Uses semantic-router with FastEmbed (``semantic-router[fastembed]``) — same pattern as
HuggingFaceEncoder but without ``transformers``/PyTorch. Embeddings cache under
``app/backend/models/fastembed_cache`` (configurable via ``OMNIBOT_FASTEMBED_CACHE``).

Aggregation is ``max`` so the best-matching utterance per route drives the score (default
``mean`` often falls below the similarity threshold when a route has many utterances).
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Literal, Optional

from semantic_router import Route
from semantic_router.encoders import FastEmbedEncoder
from semantic_router.routers import SemanticRouter

MAPS_LOCAL = "maps_local"
RetrievalMode = Literal["maps", "search"]

_router_lock = threading.Lock()
_router: Optional[SemanticRouter] = None


def _fastembed_cache_dir() -> str:
    env = (os.getenv("OMNIBOT_FASTEMBED_CACHE") or "").strip()
    if env:
        path = Path(env).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent / "models" / "fastembed_cache"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _maps_local_utterances() -> list[str]:
    return [
        "what is around me",
        "what is near me",
        "places near me",
        "coffee shop near me",
        "restaurant near me",
        "gas station near me",
        "pharmacy near me",
        "supermarket near me",
        "find a place nearby",
        "show me what is close by",
        "how far is it",
        "how long is the drive",
        "how long to drive there",
        "driving directions",
        "directions to",
        "navigate to",
        "route to",
        "traffic right now",
        "how is traffic",
        "traffic on the highway",
        "is there congestion",
        "ETA to",
        "estimated time of arrival",
        "commute time",
        "distance from here",
        "miles from my location",
        "where am I",
        "my current location",
        "map of this area",
        "open Google Maps",
        "walking distance",
        "public transit near me",
        "nearest bus stop",
        "nearest train station",
        "parking near me",
        "EV charging station near me",
    ]


def _build_router() -> SemanticRouter:
    threshold = float(os.getenv("OMNIBOT_ROUTE_THRESHOLD", "0.58"))
    encoder = FastEmbedEncoder(
        score_threshold=threshold,
        cache_dir=_fastembed_cache_dir(),
    )
    route = Route(
        name=MAPS_LOCAL,
        utterances=_maps_local_utterances(),
        score_threshold=threshold,
    )
    return SemanticRouter(
        encoder=encoder,
        routes=[route],
        auto_sync="local",
        aggregation="max",
    )


def _get_router() -> SemanticRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = _build_router()
        return _router


# If the user clearly wants weather info, always use Google Search (not Maps grounding).
_MAPS_NAV_HINTS = (
    "near me",
    "nearby",
    "close to me",
    "directions",
    "navigate",
    "route to",
    "driving directions",
    "how far",
    "how long to drive",
    "how long is the drive",
    "walk to",
    "drive to",
    "traffic",
    "commute",
    "miles from",
    "km from",
    "eta ",
)


def _looks_like_weather_intent(text: str) -> bool:
    """True when the turn is primarily about weather/conditions (Search), not POI/navigation (Maps)."""
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(h in t for h in _MAPS_NAV_HINTS):
        return False

    # Any standalone "weather" → Search. Stricter patterns below miss typos like "weather ina tl"
    # where "in" is not a whole word.
    if re.search(r"\bweather\b", t):
        return True

    if re.search(r"\bwhat'?s?\s+the\s+weather\b", t):
        return True
    if re.search(r"\bhow'?s?\s+the\s+weather\b", t):
        return True
    if re.search(r"\bweather\s+(like|in|for|at|near|around|today|tomorrow|now)\b", t):
        return True
    if re.search(r"\b(forecast|temperature|humidity|precipitation|dew\s*point|wind\s*chill|heat\s*index|uv\s*index)\b", t):
        return True
    if re.search(r"\bwill\s+it\s+(rain|snow)\b", t):
        return True
    if re.search(r"\bis\s+it\s+(going\s+to\s+)?(rain|snow|snowing|raining)\b", t):
        return True
    if re.search(r"\bchance\s+of\s+(rain|snow|storms?)\b", t):
        return True
    if re.search(r"\bhow\s+(hot|cold|warm|cool)\b", t):
        return True
    if re.search(r"\bis\s+it\s+(cold|hot|warm|cool)\b", t):
        return True
    if re.search(r"\bwhat\s+to\s+wear\b", t) and re.search(
        r"\b(rain|snow|cold|hot|warm|coat|jacket|umbrella|weather)\b", t
    ):
        return True

    weather_tokens = (
        "current conditions",
        "air quality",
        "pollen count",
        "sunrise",
        "sunset",
        "accuweather",
        "weather channel",
        "meteorologist",
        "weather report",
        "local weather",
        "today's high",
        "todays high",
        "tonight's low",
        "tonights low",
    )
    if any(tok in t for tok in weather_tokens):
        return True

    return False


def classify_retrieval(text: str) -> RetrievalMode:
    """Return ``maps`` when the query matches the local/geo route; else ``search``."""
    stripped = (text or "").strip()
    if not stripped:
        return "search"
    if _looks_like_weather_intent(stripped):
        return "search"
    choice = _get_router()(stripped)
    if choice.name == MAPS_LOCAL:
        return "maps"
    return "search"
