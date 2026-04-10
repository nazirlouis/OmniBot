import { hubUrl } from '../hubOrigin';

export const initialSetupState = {
  appMode: 'dashboard', // 'dashboard' or 'setup'
  /** device → wifi → password → waiting (for hub) → soul (bootstrap) → complete */
  setupStep: 'device',
  /** Bot being walked through after Wi‑Fi connect; used for bootstrap + feed targeting */
  walkthroughDeviceId: null,
  /** True after user clicks Begin soul ritual in post–Wi‑Fi walkthrough */
  soulWalkthroughStarted: false,
  bleDevices: [],
  bleScanMessage: null,
  isScanning: false,
  selectedDevice: null,
  ssid: '',
  password: '',
  isProvisioning: false,
  wifiNetworks: [],
  wifiScanMessage: null,
  isScanningWifi: false,
};

// These API calls use the resolved hub origin (Vite proxy in dev, same origin in Docker, or VITE_HUB_API_ORIGIN).
export const scanForDevices = async () => {
  const res = await fetch(hubUrl('/setup/scan'));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return {
      devices: [],
      message: data.detail || 'BLE scan failed.',
      ble_available: false,
    };
  }
  return {
    devices: data.devices || [],
    message: data.message || null,
    ble_available: data.ble_available !== false,
  };
};

export const scanForWifi = async () => {
  const res = await fetch(hubUrl('/setup/wifi-networks'));
  const data = await res.json();
  return {
    networks: data.networks || [],
    message: data.message || null,
  };
};

/** Hub LAN IP/port for Pixel (from the machine running this API). */
export const fetchSetupHubEndpoint = async () => {
  const res = await fetch(hubUrl('/setup/hub-endpoint'));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { hub_ip: null, hub_port: 8000 };
  }
  return {
    hub_ip: data.hub_ip ?? null,
    hub_port: typeof data.hub_port === 'number' ? data.hub_port : 8000,
  };
};

export const sendProvision = async (ssid, password, device_address, hub_ip, hub_port) => {
  const body = {
    ssid,
    password,
    device_address,
  };
  if (hub_ip && String(hub_ip).trim()) {
    body.hub_ip = String(hub_ip).trim();
    body.hub_port = hub_port != null && Number.isFinite(Number(hub_port)) ? Number(hub_port) : 8000;
  }
  const res = await fetch(hubUrl('/setup/provision'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return await res.json();
};

export const getBotSettings = async (device_id) => {
  const res = await fetch(hubUrl(`/api/settings/${encodeURIComponent(device_id)}`));
  return await res.json();
};

export const updateBotSettings = async (device_id, settings) => {
  const res = await fetch(hubUrl(`/api/settings/${encodeURIComponent(device_id)}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings)
  });
  return await res.json();
};

export const resetBotSettingsToDefault = async (device_id) => {
  const res = await fetch(
    hubUrl(`/api/settings/${encodeURIComponent(device_id)}/reset`),
    { method: 'POST' }
  );
  return await res.json();
};

export const getPersonaStatus = async (device_id) => {
  const res = await fetch(hubUrl(`/api/persona/${encodeURIComponent(device_id)}/status`));
  if (!res.ok) throw new Error('Failed to load persona status');
  return res.json();
};

export const getPersonaFile = async (device_id, persona_file) => {
  const res = await fetch(
    hubUrl(`/api/persona/${encodeURIComponent(device_id)}/${encodeURIComponent(persona_file)}`)
  );
  if (!res.ok) throw new Error('Failed to load persona file');
  return res.json();
};

export const putPersonaFile = async (device_id, persona_file, content) => {
  const res = await fetch(
    hubUrl(`/api/persona/${encodeURIComponent(device_id)}/${encodeURIComponent(persona_file)}`),
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content ?? '' }),
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to save persona file');
  }
  return res.json();
};

/** Start soul bootstrap: reset persona markdown to hub templates, clear chat history, clear daily logs + heartbeat state, write BOOTSTRAP.md, stream Gemini. */
export const startBootstrapSoul = async (device_id) => {
  const res = await fetch(hubUrl('/api/text-command'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: '',
      device_id: device_id || 'default_bot',
      bootstrap: true,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Bootstrap ritual failed');
  }
  return res.json();
};

export const listFaceProfiles = async (device_id) => {
  const res = await fetch(hubUrl(`/api/bots/${encodeURIComponent(device_id)}/face-profiles`));
  if (!res.ok) throw new Error('Failed to list face profiles');
  return res.json();
};

export const createFaceProfile = async (device_id, display_name) => {
  const res = await fetch(hubUrl(`/api/bots/${encodeURIComponent(device_id)}/face-profiles`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: display_name || 'Person' }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create profile');
  }
  return res.json();
};

export const deleteFaceProfile = async (device_id, profile_id) => {
  const res = await fetch(
    hubUrl(`/api/bots/${encodeURIComponent(device_id)}/face-profiles/${encodeURIComponent(profile_id)}`),
    { method: 'DELETE' }
  );
  if (!res.ok) throw new Error('Failed to delete profile');
  return res.json();
};

export const uploadFaceReference = async (device_id, profile_id, file) => {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(
    hubUrl(
      `/api/bots/${encodeURIComponent(device_id)}/face-profiles/${encodeURIComponent(profile_id)}/reference`
    ),
    { method: 'POST', body: fd }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Upload failed');
  }
  return res.json();
};

export const captureFaceFromPixel = async (device_id, profile_id) => {
  const res = await fetch(
    hubUrl(
      `/api/bots/${encodeURIComponent(device_id)}/face-profiles/${encodeURIComponent(profile_id)}/capture-from-pixel`
    ),
    { method: 'POST' }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Capture request failed');
  }
  return res.json();
};

export const getHubStatus = async () => {
  const res = await fetch(hubUrl('/api/hub/status'));
  if (!res.ok) throw new Error('Hub status failed');
  return res.json();
};

/** Bots known to the hub (saved settings and/or devices that have streamed). */
export const listBots = async () => {
  const res = await fetch(hubUrl('/api/bots'));
  if (!res.ok) throw new Error('Failed to list bots');
  return res.json();
};

export const getHubSettings = async () => {
  const res = await fetch(hubUrl('/api/hub/settings'));
  if (!res.ok) throw new Error('Failed to load hub settings');
  return res.json();
};

export const getHubAppSettings = async () => {
  const res = await fetch(hubUrl('/api/hub/app-settings'));
  if (!res.ok) throw new Error('Failed to load hub app settings');
  return res.json();
};

export const postHubAppSettings = async (payload) => {
  const res = await fetch(hubUrl('/api/hub/app-settings'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = 'Failed to save hub app settings';
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
};

export const postHubSettings = async (payload) => {
  const res = await fetch(hubUrl('/api/hub/settings'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = 'Failed to save hub settings';
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
};
