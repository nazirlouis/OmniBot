import { hubUrl } from '../hubOrigin';

export const initialSetupState = {
  appMode: 'dashboard', // 'dashboard' or 'setup'
  setupStep: 'device', // 'device', 'wifi', 'password'
  bleDevices: [],
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
  const data = await res.json();
  return data.devices || [];
};

export const scanForWifi = async () => {
  const res = await fetch(hubUrl('/setup/wifi-networks'));
  const data = await res.json();
  return {
    networks: data.networks || [],
    message: data.message || null,
  };
};

export const sendProvision = async (ssid, password, device_address) => {
  const res = await fetch(hubUrl('/setup/provision'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ssid,
      password,
      device_address
    })
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

export const getHubStatus = async () => {
  const res = await fetch(hubUrl('/api/hub/status'));
  if (!res.ok) throw new Error('Hub status failed');
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
