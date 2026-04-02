/**
 * Loads the Maps JavaScript API (alpha) and the `places` library so
 * `<gmp-place-contextual>` can render Gemini `googleMapsWidgetContextToken`.
 * Bootstrap matches Google's dynamic library import snippet (v=alpha for contextual view).
 */

import { hubUrl } from './hubOrigin';

export function installGoogleMapsBootstrap(apiKey) {
  if (typeof window === 'undefined') return;
  if (window.google?.maps?.importLibrary) return;

  (g => {
    let h;
    let k;
    const p = 'The Google Maps JavaScript API';
    const c = 'google';
    const l = 'importLibrary';
    const q = '__ib__';
    const m = document;
    let b = window;
    b = b[c] || (b[c] = {});
    const d = b.maps || (b.maps = {});
    const r = new Set();
    const e = new URLSearchParams();
    const u = () =>
      h ||
      (h = new Promise((resolve, reject) => {
        const script = m.createElement('script');
        e.set('libraries', [...r] + '');
        for (k in g) {
          e.set(k.replace(/[A-Z]/g, t => `_${t[0].toLowerCase()}`), g[k]);
        }
        e.set('callback', `${c}.maps.${q}`);
        script.src = `https://maps.${c}apis.com/maps/api/js?${e}`;
        d[q] = resolve;
        script.onerror = () => reject(new Error(`${p} could not load.`));
        script.nonce = m.querySelector('script[nonce]')?.nonce || '';
        m.head.appendChild(script);
      }));
    if (d[l]) {
      console.warn(`${p} only loads once. Ignoring:`, g);
    } else {
      d[l] = (f, ...n) => r.add(f) && u().then(() => d[l](f, ...n));
    }
  })({ key: apiKey, v: 'alpha' });
}

/**
 * @param {string} apiKey
 * @returns {Promise<void>}
 */
export async function loadMapsPlacesForContextual(apiKey) {
  if (!apiKey) {
    throw new Error('Missing Google Maps JavaScript API key');
  }
  installGoogleMapsBootstrap(apiKey);
  await window.google.maps.importLibrary('places');
}

/**
 * @returns {Promise<string>}
 */
export async function resolveMapsJsApiKey() {
  const fromEnv = import.meta.env.VITE_GOOGLE_MAPS_JS_API_KEY?.trim();
  if (fromEnv) return fromEnv;
  const r = await fetch(hubUrl('/api/hub-config'));
  if (!r.ok) throw new Error('hub-config failed');
  const cfg = await r.json();
  return (cfg.maps_js_api_key || '').trim();
}
