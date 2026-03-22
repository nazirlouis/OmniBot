import { useState, useEffect } from 'react';
import './index.css';

import Sidebar from './components/Sidebar';
import IntelligenceFeed from './components/IntelligenceFeed';
import SetupOrchestrator from './components/SetupOrchestrator';
import BotSettings from './components/BotSettings';
import { initialSetupState, scanForDevices, scanForWifi, sendProvision } from './components/setupService';

function App() {
  // --- WebSocket Monitor States ---
  const [esp32Status, setEsp32Status] = useState('offline'); // offline | online | working
  const [lastPing, setLastPing] = useState(null);
  const [logs, setLogs] = useState([]);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [textMessage, setTextMessage] = useState('');
  const [isSendingText, setIsSendingText] = useState(false);
  
  // --- Orchestrator & Setup States ---
  const [setupState, setSetupState] = useState(initialSetupState);

  // Helper to cleanly update nested setup state
  const updateSetup = (key, value) => {
    setSetupState(prev => ({ ...prev, [key]: value }));
  };

  const addLog = (sender, text, extra = {}) => {
    setLogs(prevLogs => [
      ...prevLogs,
      {
        id: Date.now() + Math.random(),
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        sender,
        text,
        ...extra
      }
    ]);
  };

  const appendAiStreamDelta = (stream_id, delta) => {
    setLogs(prevLogs =>
      prevLogs.map(log => {
        if (log.sender === 'ai' && log.streamId === stream_id) {
          return { ...log, text: (log.text || '') + delta };
        }
        return log;
      })
    );
  };

  // --- WebSocket Initialization ---
  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connectWebSocket = () => {
      ws = new WebSocket('ws://localhost:8000/ws/monitor');

      ws.onopen = () => {
        setWsStatus('connected');
        addLog('system', 'OmniBot Core connected successfully. Link established.');
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          if (message.type === 'esp32_connected') {
            setEsp32Status('online');
            setLastPing(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
          } else if (message.type === 'esp32_disconnected') {
            setEsp32Status('offline');
          } else if (message.type === 'processing_started') {
            setEsp32Status('working');
          } else if (message.type === 'video_captured') {
            addLog('video', message.data);
          } else if (message.type === 'audio_captured') {
            addLog('audio', message.data);
          } else if (message.type === 'ai_response_stream_start') {
            setEsp32Status('working');
            addLog('ai', '', { streamId: message.stream_id });
          } else if (message.type === 'ai_response_stream_delta') {
            appendAiStreamDelta(message.stream_id, message.data);
          } else if (message.type === 'ai_response_stream_end') {
            setEsp32Status('online');
            const hasMapsSources =
              message.maps_sources && message.maps_sources.length > 0;
            const mapsToken = message.maps_widget_context_token;
            if (hasMapsSources || mapsToken) {
              setLogs((prev) =>
                prev.map((log) =>
                  log.sender === 'ai' && log.streamId === message.stream_id
                    ? {
                        ...log,
                        ...(hasMapsSources ? { mapsSources: message.maps_sources } : {}),
                        ...(mapsToken
                          ? { mapsWidgetContextToken: mapsToken }
                          : {})
                      }
                    : log
                )
              );
            }
          } else if (message.type === 'error') {
            setEsp32Status('online');
            addLog('error', message.data);
          } else if (message.type === 'tool_call') {
            const args = message.arguments || {};
            const argsStr = Object.entries(args).map(([k, v]) => `${k}: ${v}`).join(', ');
            const fnName = String(message.function_name ?? '').toLowerCase();
            if (fnName.includes('show_weather')) {
              const condition = String(args.condition ?? args.sky_condition ?? args.cond ?? '');
              const temperatureRaw = args.temperature ?? args.temp ?? 0;
              const temperature = Number(temperatureRaw);
              addLog(
                'tool',
                `${message.function_name}(${argsStr})`,
                {
                  toolType: 'show_weather',
                  weatherCondition: condition,
                  weatherTemperature: Number.isFinite(temperature) ? temperature : 0,
                  durationMs: 5000
                }
              );
            } else if (fnName.includes('face_animation') && String(args.animation ?? '').toLowerCase() === 'map') {
              addLog('tool', `${message.function_name}(${argsStr}) — map snapshot on Pixel`);
            } else {
              addLog('tool', `${message.function_name}(${argsStr})`);
            }
          }
        } catch {
          console.error("Failed to parse message:", event.data);
        }
      };

      ws.onclose = () => {
        setWsStatus('disconnected');
        setEsp32Status('offline');
        addLog('error', 'Lost connection to OmniBot Core. Reconnecting in 5s...');
        reconnectTimer = setTimeout(connectWebSocket, 5000);
      };
      
      ws.onerror = () => ws.close();
    };

    connectWebSocket();
    return () => {
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  // --- Setup Flow Actions ---
  const handleScan = async () => {
    updateSetup('isScanning', true);
    try {
      const devices = await scanForDevices();
      updateSetup('bleDevices', devices);
    } catch (e) {
      console.error("Scan error", e);
    } finally {
      updateSetup('isScanning', false);
    }
  };

  const handleWifiScan = async () => {
    updateSetup('isScanningWifi', true);
    try {
      const networks = await scanForWifi();
      updateSetup('wifiNetworks', networks);
    } catch (e) {
      console.error("Wi-Fi Scan error", e);
    } finally {
      updateSetup('isScanningWifi', false);
    }
  };

  const handleProvision = async (e) => {
    e.preventDefault();
    updateSetup('isProvisioning', true);
    try {
      const data = await sendProvision(setupState.ssid, setupState.password, setupState.selectedDevice.address);
      if(data.status === 'success') {
         addLog('system', `Credentials transmitted to ${setupState.selectedDevice.name}. Awaiting connection heartbeat...`);
      }
    } catch (e) {
      console.error("Provision error", e);
    } finally {
      updateSetup('isProvisioning', false);
    }
  };

  const handleSendTextCommand = async (e) => {
    e.preventDefault();
    const message = textMessage.trim();
    if (!message || isSendingText) {
      return;
    }

    addLog('user', message);
    setTextMessage('');
    setIsSendingText(true);

    try {
      const res = await fetch('http://localhost:8000/api/text-command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          device_id: 'default_bot'
        })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to send text command');
      }
    } catch (error) {
      addLog('error', error.message || 'Failed to send text command');
    } finally {
      setIsSendingText(false);
    }
  };

  // Group setters and actions for SetupOrchestrator
  const setupSetters = {
    setAppMode: (val) => updateSetup('appMode', val),
    setSetupStep: (val) => updateSetup('setupStep', val),
    setSelectedDevice: (val) => updateSetup('selectedDevice', val),
    setSsid: (val) => updateSetup('ssid', val),
    setPassword: (val) => updateSetup('password', val),
  };

  const setupActions = {
    handleScan,
    handleWifiScan,
    handleProvision
  };

  return (
    <div className="app-container">
      <Sidebar 
        esp32Status={esp32Status}
        lastPing={lastPing}
        appMode={setupState.appMode}
        setAppMode={setupSetters.setAppMode}
        setSetupStep={setupSetters.setSetupStep}
      />
      
      <main className="main-content">
        {setupState.appMode === 'dashboard' && (
          <IntelligenceFeed 
            logs={logs}
            wsStatus={wsStatus}
            textMessage={textMessage}
            setTextMessage={setTextMessage}
            isSendingText={isSendingText}
            onSendTextCommand={handleSendTextCommand}
          />
        )}
        
        {setupState.appMode === 'setup' && (
          <SetupOrchestrator 
            state={setupState}
            setters={setupSetters}
            actions={setupActions}
          />
        )}
        
        {setupState.appMode === 'settings' && (
          <BotSettings setAppMode={setupSetters.setAppMode} />
        )}
      </main>
    </div>
  );
}

export default App;
