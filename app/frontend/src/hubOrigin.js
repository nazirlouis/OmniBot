/**
 * Hub (FastAPI) base URL for REST calls. No trailing slash.
 * VITE_HUB_API_ORIGIN → window.location.origin (same-host / Docker) → localhost:8000.
 * With Vite dev server proxy, prefer same origin so /api routes to the backend.
 */

export function getHubHttpOrigin() {
  const env = import.meta.env.VITE_HUB_API_ORIGIN?.trim();
  if (env) {
    return env.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return 'http://localhost:8000';
}

/**
 * @param {string} path e.g. /api/hub-config
 */
export function hubUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${getHubHttpOrigin()}${p}`;
}

/**
 * WebSocket URL for paths like /ws/monitor
 * @param {string} path
 */
export function getHubWebSocketUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  const http = getHubHttpOrigin();
  try {
    const u = new URL(http);
    const wsProto = u.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${u.host}${p}`;
  } catch {
    return `ws://localhost:8000${p}`;
  }
}
