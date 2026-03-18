import { useState, useEffect, useRef } from 'react';
import './index.css';

function App() {
  // Monitor States
  const [esp32Status, setEsp32Status] = useState('offline');
  const [lastPing, setLastPing] = useState(null);
  const [logs, setLogs] = useState([]);
  const [wsStatus, setWsStatus] = useState('disconnected');
  
  // Setup States
  const [appMode, setAppMode] = useState('monitor'); // Default to dashboard now
  const [bleDevices, setBleDevices] = useState([]);
  const [isScanning, setIsScanning] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState(null);
  const [ssid, setSsid] = useState('');
  const [password, setPassword] = useState('');
  const [isProvisioning, setIsProvisioning] = useState(false);
  const [wifiNetworks, setWifiNetworks] = useState([]);
  const [isScanningWifi, setIsScanningWifi] = useState(false);
  const [setupStep, setSetupStep] = useState('device'); // 'device', 'wifi', 'password'
  const logEndRef = useRef(null);

  useEffect(() => {
    // Scroll to bottom whenever logs change
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connectWebSocket = () => {
      ws = new WebSocket('ws://localhost:8000/ws/monitor');

      ws.onopen = () => {
        setWsStatus('connected');
        addLog('system', 'Connected to Brain Monitor Dashboard');
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          if (message.type === 'esp32_connected') {
            setEsp32Status('online');
            setLastPing(new Date().toLocaleTimeString());
            setAppMode('monitor'); // Jump to monitor if it pings us!
          } else if (message.type === 'processing_started') {
            setEsp32Status('working');
            addLog('esp32', 'Captured Audio/Image! Sent to Gemini for processing...');
          } else if (message.type === 'ai_response') {
            setEsp32Status('online'); // goes back to online when done
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
        addLog('error', 'Lost connection to Brain Server. Reconnecting in 5s...');
        reconnectTimer = setTimeout(connectWebSocket, 5000);
      };
      
      ws.onerror = () => {
        ws.close();
      };
    };

    connectWebSocket();

    return () => {
      clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  const addLog = (sender, text) => {
    setLogs(prevLogs => [
      ...prevLogs,
      {
        id: Date.now() + Math.random(),
        time: new Date().toLocaleTimeString(),
        sender,
        text
      }
    ]);
  };

  const handleScan = async () => {
    setIsScanning(true);
    try {
      const res = await fetch('http://localhost:8000/setup/scan');
      const data = await res.json();
      setBleDevices(data.devices || []);
    } catch (e) {
      console.error("Scan error", e);
    } finally {
      setIsScanning(false);
    }
  };

  const handleWifiScan = async () => {
    setIsScanningWifi(true);
    try {
      const res = await fetch('http://localhost:8000/setup/wifi-networks');
      const data = await res.json();
      setWifiNetworks(data.networks || []);
    } catch (e) {
      console.error("Wi-Fi Scan error", e);
    } finally {
      setIsScanningWifi(false);
    }
  };

  const handleProvision = async (e) => {
    e.preventDefault();
    if (!selectedDevice || !ssid) return;
    
    setIsProvisioning(true);
    try {
      const res = await fetch('http://localhost:8000/setup/provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ssid,
          password,
          device_address: selectedDevice.address
        })
      });
      const data = await res.json();
      if(data.status === 'success') {
         // Optionally wait for the ping to trigger appMode='monitor' automatically
         addLog('system', 'Credentials sent successfully via BLE. Waiting for ESP32 to connect to Wi-Fi...');
      }
    } catch (e) {
      console.error("Provision error", e);
    } finally {
      setIsProvisioning(false);
    }
  };

  if (appMode === 'setup') {
    return (
      <div className="setup-container">
        <div className="setup-card">
          <h1>🤖 ESP32 Wireless Setup</h1>
          <p className="setup-subtitle">Connect your Desktop Robot to Wi-Fi via Bluetooth.</p>

          {setupStep === 'device' && (
            <div className="scan-section">
              <button onClick={handleScan} disabled={isScanning} className="action-btn">
                {isScanning ? 'Scanning...' : 'Scan for Robot'}
              </button>
              <div className="device-list">
                {bleDevices.map(d => (
                  <div 
                    key={d.address} 
                    className={`device-item ${selectedDevice?.address === d.address ? 'selected' : ''}`}
                    onClick={() => {
                      setSelectedDevice(d);
                      setSetupStep('wifi');
                      handleWifiScan(); // Automatically scan when a robot is picked
                    }}
                  >
                    <span className="device-name">{d.name}</span>
                    <span className="device-mac">{d.address}</span>
                  </div>
                ))}
                {bleDevices.length === 0 && !isScanning && <div className="no-devices">No devices found.</div>}
              </div>
            </div>
          )}

          {setupStep === 'wifi' && (
            <div className="wifi-selection-section" style={{ marginTop: '20px' }}>
              <h3 style={{ marginBottom: '15px', color: '#888' }}>Select Wi-Fi Network</h3>
              {isScanningWifi ? (
                <div className="scanning-indicator" style={{ padding: '20px', textAlign: 'center', color: '#646cff' }}>
                  Scanning Nearby Networks...
                </div>
              ) : (
                <div className="device-list" style={{ maxHeight: '250px', overflowY: 'auto' }}>
                  {wifiNetworks.map((net, i) => (
                    <div 
                      key={i} 
                      className="device-item"
                      onClick={() => {
                        setSsid(net);
                        setPassword(''); // Clear old password
                        setSetupStep('password');
                      }}
                    >
                      <span className="device-name">📶 {net}</span>
                    </div>
                  ))}
                  {wifiNetworks.length === 0 && <div className="no-devices">No networks found.</div>}
                  <div className="device-item" onClick={() => { setSsid(''); setPassword(''); setSetupStep('password'); }}>
                    <span className="device-name">➕ Other / Hidden Network</span>
                  </div>
                </div>
              )}
              <button className="text-btn" onClick={() => setSetupStep('device')} style={{ marginTop: '15px' }}>
                Back to Robot Selection
              </button>
            </div>
          )}

          {setupStep === 'password' && (
            <form className="provision-section" onSubmit={handleProvision} style={{ marginTop: '20px' }}>
              <h3 style={{ marginBottom: '15px', color: '#fff' }}>Connecting to {ssid ? `"${ssid}"` : 'Network'}</h3>
              
              {!ssid && (
                <div className="input-group">
                  <label>Network Name (SSID)</label>
                  <input type="text" value={ssid} onChange={e => setSsid(e.target.value)} required placeholder="Enter exact network name" />
                </div>
              )}

              <div className="input-group">
                <label>Wi-Fi Password</label>
                <input type="password" value={password} onChange={e => setPassword(e.target.value)} autoFocus placeholder="Leave blank if open network" />
              </div>
              
              <div style={{ display: 'flex', gap: '10px' }}>
                <button type="button" onClick={() => setSetupStep('wifi')} className="action-btn" style={{ flex: 1, backgroundColor: '#444' }}>
                  Back
                </button>
                <button type="submit" disabled={!ssid || isProvisioning} className="action-btn primary" style={{ flex: 2 }}>
                  {isProvisioning ? 'Sending...' : 'Send to Robot'}
                </button>
              </div>
            </form>
          )}

          <button className="text-btn" onClick={() => setAppMode('monitor')} style={{ marginTop: '30px' }}>Back to Dashboard</button>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-container">
      <header className="dashboard-header">
        <div className="title-area">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
            <h1>🧠 Brain Interface Monitor</h1>
            <button 
              onClick={() => {
                setAppMode('setup');
                setSetupStep('device');
              }}
              style={{
                background: 'transparent',
                border: '1px solid #444',
                color: '#fff',
                padding: '8px 16px',
                borderRadius: '8px',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '8px'
              }}
            >
              ⚙️ Settings
            </button>
          </div>
          <span className={`ws-badge ${wsStatus}`}>
            {wsStatus === 'connected' ? 'Server Connected' : 'Server Disconnected'}
          </span>
        </div>
        
        <div className={`esp32-status-card ${esp32Status}`}>
          <div className="status-icon">
            {esp32Status === 'offline' && '🔴'}
            {esp32Status === 'online' && '🟢'}
            {esp32Status === 'working' && '⚙️'}
          </div>
          <div className="status-details">
            <span className="status-label">ESP32 ROBOT</span>
            <span className="status-value">
              {esp32Status === 'offline' && 'Disconnected'}
              {esp32Status === 'online' && 'Online & Ready'}
              {esp32Status === 'working' && 'Processing...'}
            </span>
            {lastPing && esp32Status !== 'offline' && (
              <span className="last-ping">Last seen: {lastPing}</span>
            )}
          </div>
        </div>
      </header>

      <main className="log-container">
        {logs.length === 0 ? (
          <div className="empty-state">
            <div className="pulse-ring"></div>
            <p>Waiting for ESP32 activity...</p>
          </div>
        ) : (
          <div className="log-scroll-area">
            {logs.map((log) => (
              <div key={log.id} className={`log-entry ${log.sender}`}>
                <div className="log-meta">
                  <span className="log-time">{log.time}</span>
                  <span className="log-origin">
                    {log.sender === 'system' && 'SYSTEM'}
                    {log.sender === 'esp32' && 'ESP32'}
                    {log.sender === 'ai' && 'GEMINI AI'}
                    {log.sender === 'error' && 'ERROR'}
                  </span>
                </div>
                <div className="log-text">{log.text}</div>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
