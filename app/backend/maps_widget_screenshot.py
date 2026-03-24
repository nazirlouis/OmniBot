"""Headless screenshot of Gemini Maps grounding contextual widget for ESP32 display."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Optional

from PIL import Image


def capture_contextual_map_jpeg_sync(
    widget_context_token: str,
    maps_js_api_key: str,
    *,
    size: int = 240,
    wait_after_load_ms: int = 5500,
) -> Optional[bytes]:
    """
    Render ``gmp-place-contextual`` with the given context token and return JPEG bytes.

    Returns None if inputs are empty or Playwright/screenshot fails (caller should log).
    """
    token = (widget_context_token or "").strip()
    key = (maps_js_api_key or "").strip()
    if not token or not key:
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    key_js = json.dumps(key)
    token_js = json.dumps(token)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#111;}}
#host{{width:{size}px;height:{size}px;overflow:hidden;}}</style></head>
<body><div id="host"></div>
<script>
const KEY = {key_js};
const TOKEN = {token_js};
(function init() {{
  const g = {{ key: KEY, v: 'alpha' }};
  const c = 'google', l = 'maps', q = '__ib__', m = document;
  let h;
  let k;
  const p = 'Maps API';
  let b = window;
  b = b[c] || (b[c] = {{}});
  const d = b[l] || (b[l] = {{}});
  const r = new Set();
  const e = new URLSearchParams();
  const u = () => h || (h = new Promise((resolve, reject) => {{
    const script = m.createElement('script');
    e.set('libraries', [...r] + '');
    for (k in g) {{
      e.set(k.replace(/[A-Z]/g, t => '_' + t[0].toLowerCase()), g[k]);
    }}
    e.set('callback', c + '.' + l + '.' + q);
    script.src = 'https://maps.googleapis.com/maps/api/js?' + e;
    d[q] = resolve;
    script.onerror = () => reject(new Error(p));
    m.head.appendChild(script);
  }}));
  if (!d.importLibrary) {{
    d.importLibrary = (f, ...n) => r.add(f) && u().then(() => d.importLibrary(f, ...n));
  }}
  d.importLibrary('places').then(async () => {{
    const host = document.getElementById('host');
    host.replaceChildren();
    const el = document.createElement('gmp-place-contextual');
    el.contextToken = TOKEN;
    host.appendChild(el);
  }});
}})();
</script></body></html>"""

    png: bytes
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        try:
            page = browser.new_page(viewport={"width": size + 80, "height": size + 80})
            page.set_content(html, wait_until="load", timeout=90000)
            page.wait_for_timeout(wait_after_load_ms)
            png = page.locator("#host").screenshot(type="png", timeout=30000)
        finally:
            browser.close()

    im = Image.open(BytesIO(png)).convert("RGB")
    if im.size != (size, size):
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    out = BytesIO()
    # Baseline JPEG only — ESP32 JPEGDEC often fails on progressive scans.
    im.save(out, format="JPEG", quality=85, optimize=True, progressive=False)
    return out.getvalue()
