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
  isScanningWifi: false,
};

// These API calls are unchanged from the original App.jsx
export const scanForDevices = async () => {
  const res = await fetch('http://localhost:8000/setup/scan');
  const data = await res.json();
  return data.devices || [];
};

export const scanForWifi = async () => {
  const res = await fetch('http://localhost:8000/setup/wifi-networks');
  const data = await res.json();
  return data.networks || [];
};

export const sendProvision = async (ssid, password, device_address) => {
  const res = await fetch('http://localhost:8000/setup/provision', {
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
  const res = await fetch(`http://localhost:8000/api/settings/${encodeURIComponent(device_id)}`);
  return await res.json();
};

export const updateBotSettings = async (device_id, settings) => {
  const res = await fetch(`http://localhost:8000/api/settings/${encodeURIComponent(device_id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings)
  });
  return await res.json();
};

export const resetBotSettingsToDefault = async (device_id) => {
  const res = await fetch(
    `http://localhost:8000/api/settings/${encodeURIComponent(device_id)}/reset`,
    { method: 'POST' }
  );
  return await res.json();
};
