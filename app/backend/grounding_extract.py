"""Shared Google Search grounding → hub UI (sources + queries + inline citations)."""

import re

# Remove `[n](url)` citation tails added by add_inline_citations_from_grounding (device / TTS).
_INLINE_CITATION_RE = re.compile(r",?\s*\[\d+\]\([^)]*\)")
# Bare `[n]` markers (e.g. from model transcription) — remove for spoken TTS so words are not split.
_BARE_BRACKET_CITATION_RE = re.compile(r"\[\d+\]")


def strip_for_live_tts(text: str) -> str:
    """Strip grounding/citation markers before sending text to ElevenLabs (keeps prosody natural)."""
    if not text:
        return ""
    s = _INLINE_CITATION_RE.sub("", text)
    s = _BARE_BRACKET_CITATION_RE.sub("", s)
    return s


def strip_inline_citation_markdown(text: str) -> str:
    """Strip markdown-style citation links inserted after grounded segments."""
    if not text:
        return ""
    s = _INLINE_CITATION_RE.sub("", text)
    return s.strip()


def add_inline_citations_from_grounding(text: str, gm) -> str:
    """Insert [n](uri) markdown after each supported segment (groundingSupports + groundingChunks).

    Each support uses segment.end_index and grounding_chunk_indices pointing at grounding_chunks[i].web.uri.
    Inserts from highest end_index first so indices stay valid.
    """
    if not text or gm is None:
        return text or ""
    supports = getattr(gm, "grounding_supports", None) or []
    chunks = getattr(gm, "grounding_chunks", None) or []
    if not supports:
        return text

    def seg_end(sup) -> int:
        seg = getattr(sup, "segment", None)
        if seg is None:
            return -1
        ei = getattr(seg, "end_index", None)
        return int(ei) if ei is not None else -1

    sorted_supports = sorted(supports, key=seg_end, reverse=True)
    out = text
    for support in sorted_supports:
        seg = getattr(support, "segment", None)
        if seg is None:
            continue
        end_index = getattr(seg, "end_index", None)
        if end_index is None:
            continue
        end_index = int(end_index)
        indices = getattr(support, "grounding_chunk_indices", None) or []
        if not indices:
            continue
        citation_links: list[str] = []
        for i in indices:
            if i < 0 or i >= len(chunks):
                continue
            ch = chunks[i]
            web = getattr(ch, "web", None) if ch is not None else None
            if web is None:
                continue
            uri = getattr(web, "uri", None)
            if not uri:
                continue
            citation_links.append(f"[{int(i) + 1}]({uri})")
        if not citation_links:
            continue
        citation_string = ", ".join(citation_links)
        if end_index < 0:
            continue
        if end_index > len(out):
            end_index = len(out)
        out = out[:end_index] + citation_string + out[end_index:]
    return out


def extract_search_sources_from_grounding_metadata(gm) -> tuple[list[dict], list[str]]:
    """Build link list + query list for hub UI (Google Search grounding attribution)."""
    if gm is None:
        return [], []
    chunks = getattr(gm, "grounding_chunks", None) or []
    by_uri: dict[str, dict] = {}
    for ch in chunks:
        web = getattr(ch, "web", None)
        if web is None:
            continue
        uri = getattr(web, "uri", None)
        if not uri:
            continue
        title = (getattr(web, "title", None) or "").strip() or "Web source"
        by_uri[str(uri)] = {"title": title, "uri": str(uri)}
    queries = []
    for q in (getattr(gm, "web_search_queries", None) or []):
        s = str(q).strip()
        if s:
            queries.append(s)
    return list(by_uri.values()), queries
