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
  
  // --- Orchestrator & Setup States ---
  const [setupState, setSetupState] = useState(initialSetupState);

  // Helper to cleanly update nested setup state
  const updateSetup = (key, value) => {
    setSetupState(prev => ({ ...prev, [key]: value }));
  };

  const addLog = (sender, text) => {
    setLogs(prevLogs => [
      ...prevLogs,
      {
        id: Date.now() + Math.random(),
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        sender,
        text
      }
    ]);
  };

  // --- WebSocket Initialization ---
  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connectWebSocket = () => {
      ws = new WebSocket('ws://localhost:8000/ws/monitor');

      ws.onopen = () => {
        setWsStatus('connected');
        addLog('system', 'Prism Core connected successfully. Link established.');
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          if (message.type === 'esp32_connected') {
            setEsp32Status('online');
            setLastPing(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
          } else if (message.type === 'processing_started') {
            setEsp32Status('working');
            addLog('esp32', 'Sensory capture complete. Transmitting to Gemini...');
          } else if (message.type === 'ai_response') {
            setEsp32Status('online');
            addLog('ai', message.data);
          } else if (message.type === 'error') {
            setEsp32Status('online');
            addLog('error', message.data);
          }
        } catch (e) {
          console.error("Failed to parse message:", event.data);
        }
      };

      ws.onclose = () => {
        setWsStatus('disconnected');
        setEsp32Status('offline');
        addLog('error', 'Lost connection to Prism Core. Reconnecting in 5s...');
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
