import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';

import Sidebar from './components/Sidebar';
import IntelligenceFeed from './components/IntelligenceFeed';
import SetupOrchestrator from './components/SetupOrchestrator';
import SettingsShell from './components/SettingsShell';
import FirstRunSetup from './components/FirstRunSetup';
import {
  initialSetupState,
  scanForDevices,
  scanForWifi,
  sendProvision,
  fetchSetupHubEndpoint,
  getHubStatus,
  listBots,
  startBootstrapSoul,
} from './components/setupService';
import { hubUrl, getHubWebSocketUrl } from './hubOrigin';

function App() {
  /** null = checking, false = show first-run key screen, true = hub has Gemini configured */
  const [geminiConfigured, setGeminiConfigured] = useState(null);
  const [hubLoadError, setHubLoadError] = useState(null);

  const loadHubStatus = useCallback(() => {
    setHubLoadError(null);
    setGeminiConfigured(null);
    getHubStatus()
      .then((s) => setGeminiConfigured(Boolean(s.gemini_configured)))
      .catch(() =>
        setHubLoadError(
          'Cannot reach the OmniBot hub. Start the backend (e.g. python app.py in app/backend) and ensure this UI can reach it (port 8000, or your Vite proxy).'
        )
      );
  }, []);

  useEffect(() => {
    loadHubStatus();
  }, [loadHubStatus]);

  const [sidebarBots, setSidebarBots] = useState([]);
  const [botUiStatus, setBotUiStatus] = useState({});
  const [lastPingById, setLastPingById] = useState({});
  const [selectedBotId, setSelectedBotId] = useState('default_bot');
  const selectedBotIdRef = useRef(selectedBotId);
  selectedBotIdRef.current = selectedBotId;

  const [logs, setLogs] = useState([]);
  const [toolCalls, setToolCalls] = useState([]);
  const [hubActivityLog, setHubActivityLog] = useState([]);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [textMessage, setTextMessage] = useState('');
  const [isSendingText, setIsSendingText] = useState(false);

  const [setupState, setSetupState] = useState(initialSetupState);
  const setupStateRef = useRef(setupState);
  setupStateRef.current = setupState;
  const [settingsTab, setSettingsTab] = useState('pixel');
  const provisionPollRef = useRef(null);

  const exitSetupToDashboard = useCallback(() => {
    setSetupState({ ...initialSetupState, appMode: 'dashboard', setupStep: 'device' });
  }, []);

  const enterSetupFlow = useCallback(() => {
    setSetupState({ ...initialSetupState, appMode: 'setup', setupStep: 'device' });
  }, []);

  const updateSetup = (key, value) => {
    setSetupState((prev) => ({ ...prev, [key]: value }));
  };

  const addLog = (sender, text, extra = {}) => {
    const entry = {
      id: Date.now() + Math.random(),
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      sender,
      text,
      ...extra,
    };
    if (sender === 'system' || sender === 'error') {
      setHubActivityLog((prev) => [...prev, entry]);
      return;
    }
    setLogs((prevLogs) => [...prevLogs, entry]);
  };

  const appendAiStreamDelta = (stream_id, delta) => {
    setLogs((prevLogs) =>
      prevLogs.map((log) => {
        if (log.sender === 'ai' && log.streamId === stream_id) {
          return { ...log, text: (log.text || '') + delta };
        }
        return log;
      })
    );
  };

  const mergeBotList = useCallback((rows) => {
    const bots = rows || [];
    setSidebarBots(bots);
    setBotUiStatus((prev) => {
      const next = {};
      const ids = new Set(bots.map((b) => b.device_id));
      for (const b of bots) {
        const id = b.device_id;
        const was = prev[id];
        if (was === 'working') {
          next[id] = 'working';
        } else if (b.online === true) {
          next[id] = 'online';
        } else {
          next[id] = 'offline';
        }
      }
      for (const k of Object.keys(prev)) {
        if (!ids.has(k)) delete next[k];
      }
      return next;
    });
  }, []);

  const refreshBotList = useCallback(async () => {
    const data = await listBots();
    mergeBotList(data.bots || []);
  }, [mergeBotList]);

  useEffect(() => {
    if (sidebarBots.length === 0) return;
    const ids = new Set(sidebarBots.map((b) => b.device_id));
    if (!ids.has(selectedBotId)) {
      setSelectedBotId(sidebarBots[0].device_id);
    }
  }, [sidebarBots, selectedBotId]);

  /** Poll hub stream presence so Offline stays accurate if WebSocket events are missed. */
  useEffect(() => {
    if (geminiConfigured !== true) {
      return undefined;
    }
    const id = window.setInterval(() => {
      void refreshBotList().catch(() => {});
    }, 60000);
    return () => window.clearInterval(id);
  }, [geminiConfigured, refreshBotList]);

  useEffect(() => {
    if (geminiConfigured !== true) {
      return undefined;
    }

    let ws;
    let cancelled = false;

    const run = async () => {
      try {
        const data = await listBots();
        if (cancelled) return;
        mergeBotList(data.bots || []);
      } catch (e) {
        console.error('Failed to load bot list', e);
      }

      ws = new WebSocket(getHubWebSocketUrl('/ws/monitor'));

      ws.onopen = () => {
        setWsStatus('connected');
        addLog('system', 'OmniBot Core connected successfully. Link established.');
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

          if (message.type === 'stream_snapshot') {
            const online = new Set(message.online_device_ids || []);
            setBotUiStatus((prev) => {
              const next = { ...prev };
              for (const k of Object.keys(next)) {
                next[k] = online.has(k) ? 'online' : 'offline';
              }
              for (const id of online) {
                next[id] = 'online';
              }
              return next;
            });
            return;
          }

          if (message.type === 'esp32_connected') {
            const did = message.device_id || 'default_bot';
            void refreshBotList().then(() => {
              if (provisionPollRef.current) {
                clearInterval(provisionPollRef.current);
                provisionPollRef.current = null;
              }
              const walkthroughToSoul =
                setupStateRef.current.appMode === 'setup' &&
                setupStateRef.current.setupStep === 'waiting';
              setSetupState((prev) => {
                if (prev.appMode === 'setup' && prev.setupStep === 'waiting') {
                  return {
                    ...prev,
                    setupStep: 'soul',
                    walkthroughDeviceId: did,
                    soulWalkthroughStarted: false,
                  };
                }
                return prev;
              });
              if (walkthroughToSoul) {
                setSelectedBotId(did);
              }
              setBotUiStatus((prev) => ({ ...prev, [did]: 'online' }));
              const t = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
              setLastPingById((p) => ({ ...p, [did]: t }));
            });
          } else if (message.type === 'esp32_disconnected') {
            const did = message.device_id || 'default_bot';
            setBotUiStatus((prev) => ({ ...prev, [did]: 'offline' }));
          } else if (message.type === 'processing_started') {
            const did = message.device_id || selectedBotIdRef.current;
            if (did) {
              setBotUiStatus((prev) => ({ ...prev, [did]: 'working' }));
            }
          } else if (message.type === 'video_captured') {
            addLog('video', message.data);
          } else if (message.type === 'audio_captured') {
            addLog('audio', message.data);
          } else if (message.type === 'ai_response_stream_start') {
            if (message.device_id) {
              setBotUiStatus((prev) => ({ ...prev, [message.device_id]: 'working' }));
            }
            addLog('ai', '', { streamId: message.stream_id });
          } else if (message.type === 'ai_response_stream_delta') {
            appendAiStreamDelta(message.stream_id, message.data);
          } else if (message.type === 'ai_response_stream_end') {
            if (message.device_id) {
              const live = message.device_stream_connected;
              setBotUiStatus((prev) => ({
                ...prev,
                [message.device_id]:
                  live === undefined ? 'online' : live ? 'online' : 'offline',
              }));
            }
            const hasSearchSources = message.search_sources && message.search_sources.length > 0;
            const hasSearchQueries = message.search_queries && message.search_queries.length > 0;
            if (hasSearchSources || hasSearchQueries) {
              setLogs((prev) =>
                prev.map((log) =>
                  log.sender === 'ai' && log.streamId === message.stream_id
                    ? {
                        ...log,
                        ...(hasSearchSources ? { searchSources: message.search_sources } : {}),
                        ...(hasSearchQueries ? { searchQueries: message.search_queries } : {}),
                      }
                    : log
                )
              );
            }
          } else if (message.type === 'error') {
            if (message.device_id) {
              const live = message.device_stream_connected;
              setBotUiStatus((prev) => ({
                ...prev,
                [message.device_id]:
                  live === undefined ? 'online' : live ? 'online' : 'offline',
              }));
            }
            addLog('error', message.data);
          } else if (message.type === 'tool_call') {
            const args = message.arguments || {};
            const did = message.device_id || selectedBotIdRef.current || 'default_bot';
            setToolCalls((prev) => [
              ...prev,
              {
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
                time: new Date().toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                }),
                deviceId: did,
                streamId: message.stream_id || null,
                functionName: message.function_name || 'unknown',
                arguments: args,
              },
            ]);
          } else if (message.type === 'persona_file_updated') {
            const did = message.device_id || '';
            const f = message.file || 'persona file';
            addLog(
              'system',
              did ? `Persona updated (${did}): ${f}` : `Persona updated: ${f}`
            );
            const st = setupStateRef.current;
            if (
              st.appMode === 'setup' &&
              st.setupStep === 'soul' &&
              did === st.walkthroughDeviceId &&
              typeof f === 'string' &&
              f.includes('BOOTSTRAP') &&
              f.includes('deleted')
            ) {
              setSetupState((prev) =>
                prev.appMode === 'setup' && prev.setupStep === 'soul'
                  ? { ...prev, setupStep: 'complete' }
                  : prev
              );
            }
          }
        } catch {
          console.error('Failed to parse message:', event.data);
        }
      };

      ws.onclose = () => {
        setWsStatus('disconnected');
        setBotUiStatus((prev) => {
          const next = { ...prev };
          for (const k of Object.keys(next)) {
            next[k] = 'offline';
          }
          return next;
        });
        addLog('error', 'Lost connection to OmniBot Core. Refresh the page to reconnect.');
      };

      ws.onerror = () => ws.close();
    };

    run();

    return () => {
      cancelled = true;
      if (ws) ws.close();
    };
  }, [geminiConfigured, mergeBotList, refreshBotList]);

  useEffect(
    () => () => {
      if (provisionPollRef.current) {
        clearInterval(provisionPollRef.current);
        provisionPollRef.current = null;
      }
    },
    []
  );

  const handleScan = async () => {
    updateSetup('isScanning', true);
    updateSetup('bleScanMessage', null);
    try {
      const { devices, message } = await scanForDevices();
      updateSetup('bleDevices', devices);
      updateSetup('bleScanMessage', message || null);
    } catch (e) {
      console.error('Scan error', e);
      updateSetup('bleScanMessage', e.message || 'Scan failed.');
    } finally {
      updateSetup('isScanning', false);
    }
  };

  const handleWifiScan = async () => {
    updateSetup('isScanningWifi', true);
    try {
      const { networks, message } = await scanForWifi();
      updateSetup('wifiNetworks', networks);
      updateSetup('wifiScanMessage', message);
    } catch (e) {
      console.error('Wi-Fi Scan error', e);
    } finally {
      updateSetup('isScanningWifi', false);
    }
  };

  const handleProvision = async (e) => {
    e.preventDefault();
    updateSetup('isProvisioning', true);
    try {
      let hubIp = null;
      let hubPort = 8000;
      try {
        const ep = await fetchSetupHubEndpoint();
        hubIp = ep.hub_ip;
        hubPort = ep.hub_port;
      } catch {
        /* hub-endpoint is best-effort */
      }
      const data = await sendProvision(
        setupState.ssid,
        setupState.password,
        setupState.selectedDevice.address,
        hubIp,
        hubPort
      );
      if (data.status === 'success') {
        addLog(
          'system',
          hubIp
            ? `Credentials and hub address (${hubIp}:${hubPort}) sent to ${setupState.selectedDevice.name}. Waiting for Pixel to connect…`
            : `Credentials sent to ${setupState.selectedDevice.name}. Hub LAN IP was not detected — ensure Pixel firmware points to this PC, then wait for connection…`
        );
        updateSetup('setupStep', 'waiting');
        try {
          await refreshBotList();
        } catch {
          /* ignore */
        }
        if (provisionPollRef.current) {
          clearInterval(provisionPollRef.current);
        }
        provisionPollRef.current = setInterval(() => {
          void refreshBotList().catch(() => {});
        }, 3000);
        window.setTimeout(() => {
          if (provisionPollRef.current) {
            clearInterval(provisionPollRef.current);
            provisionPollRef.current = null;
          }
        }, 90000);
      }
    } catch (e) {
      console.error('Provision error', e);
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
      const res = await fetch(hubUrl('/api/text-command'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          device_id: selectedBotId,
        }),
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

  const handleGiveSoul = async () => {
    if (isSendingText) return;
    addLog('user', 'Give me a soul — starting bootstrap ritual');
    setIsSendingText(true);
    try {
      await startBootstrapSoul(selectedBotId);
    } catch (error) {
      addLog('error', error.message || 'Bootstrap ritual failed');
    } finally {
      setIsSendingText(false);
    }
  };

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
    handleProvision,
    exitSetupToDashboard,
    onStartSoulWalkthrough: async () => {
      const id = setupState.walkthroughDeviceId;
      if (!id || isSendingText) return;
      updateSetup('soulWalkthroughStarted', true);
      addLog('user', 'Give me a soul — starting bootstrap ritual');
      setIsSendingText(true);
      try {
        await startBootstrapSoul(id);
      } catch (error) {
        addLog('error', error.message || 'Bootstrap ritual failed');
        updateSetup('soulWalkthroughStarted', false);
      } finally {
        setIsSendingText(false);
      }
    },
    onSkipSoulWalkthrough: () => {
      setSetupState((prev) => ({ ...prev, setupStep: 'complete' }));
    },
    onConfigureSettings: () => {
      setSettingsTab('pixel');
      setSetupState({ ...initialSetupState, appMode: 'settings', setupStep: 'device' });
    },
    onGoToFeed: () => {
      setSetupState({ ...initialSetupState, appMode: 'dashboard', setupStep: 'device' });
    },
  };

  if (hubLoadError) {
    return (
      <div className="first-run">
        <div className="first-run-card">
          <h1 className="first-run-title">Cannot connect</h1>
          <p className="first-run-lead">{hubLoadError}</p>
          <button type="button" className="first-run-submit" onClick={loadHubStatus}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (geminiConfigured === null) {
    return (
      <div className="first-run">
        <div className="first-run-card">
          <p className="first-run-lead" style={{ margin: 0 }}>
            Connecting to hub…
          </p>
        </div>
      </div>
    );
  }

  if (geminiConfigured === false) {
    return <FirstRunSetup onConfigured={() => setGeminiConfigured(true)} />;
  }

  return (
    <div className="app-container">
      <Sidebar
        bots={sidebarBots}
        botStatus={botUiStatus}
        lastPingById={lastPingById}
        selectedBotId={selectedBotId}
        setSelectedBotId={setSelectedBotId}
        appMode={setupState.appMode}
        setAppMode={setupSetters.setAppMode}
        setSetupStep={setupSetters.setSetupStep}
        setSettingsTab={setSettingsTab}
        settingsTab={settingsTab}
        onEnterSetup={enterSetupFlow}
      />

      <main className="main-content">
        {setupState.appMode === 'dashboard' && (
          <IntelligenceFeed
            logs={logs}
            toolCalls={toolCalls}
            hubActivityLog={hubActivityLog}
            selectedBotId={selectedBotId}
            wsStatus={wsStatus}
            textMessage={textMessage}
            setTextMessage={setTextMessage}
            isSendingText={isSendingText}
            onSendTextCommand={handleSendTextCommand}
            onGiveSoul={handleGiveSoul}
          />
        )}

        {setupState.appMode === 'setup' &&
          (setupState.setupStep === 'soul' ? (
            <div className="setup-soul-layout">
              <SetupOrchestrator state={setupState} setters={setupSetters} actions={setupActions} />
              <IntelligenceFeed
                logs={logs}
                toolCalls={toolCalls}
                hubActivityLog={hubActivityLog}
                selectedBotId={setupState.walkthroughDeviceId || selectedBotId}
                wsStatus={wsStatus}
                textMessage={textMessage}
                setTextMessage={setTextMessage}
                isSendingText={isSendingText}
                onSendTextCommand={handleSendTextCommand}
                onGiveSoul={null}
              />
            </div>
          ) : (
            <SetupOrchestrator state={setupState} setters={setupSetters} actions={setupActions} />
          ))}

        {setupState.appMode === 'settings' && (
          <SettingsShell
            setAppMode={setupSetters.setAppMode}
            tab={settingsTab}
            setTab={setSettingsTab}
            deviceId={selectedBotId}
            onBotsChanged={refreshBotList}
          />
        )}
      </main>
    </div>
  );
}

export default App;
