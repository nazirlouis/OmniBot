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
  getHubAppSettings,
  listBots,
  startBootstrapSoul,
} from './components/setupService';
import { hubUrl, getHubWebSocketUrl } from './hubOrigin';
import { floatTo16kPcmS16le, int16ToCopyBuffer } from './browserVoicePcm';

/** Decode base64 to Int16Array (little-endian PCM s16le). */
function base64ToInt16PCM(b64) {
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Int16Array(u8.buffer, u8.byteOffset, u8.byteLength / 2);
}

function App() {
  /** null = checking, false = show first-run key screen, true = hub has Gemini configured */
  const [geminiConfigured, setGeminiConfigured] = useState(null);
  const [hubLoadError, setHubLoadError] = useState(null);
  const [hubAppSettings, setHubAppSettings] = useState(null);
  const hubAppSettingsRef = useRef(null);
  hubAppSettingsRef.current = hubAppSettings;

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

  useEffect(() => {
    if (geminiConfigured !== true) {
      return;
    }
    getHubAppSettings()
      .then((s) => setHubAppSettings(s && typeof s === 'object' ? s : {}))
      .catch(() => setHubAppSettings({ live_voice_source: 'esp32' }));
  }, [geminiConfigured]);

  const [sidebarBots, setSidebarBots] = useState([]);
  const [botUiStatus, setBotUiStatus] = useState({});
  const [lastPingById, setLastPingById] = useState({});
  const [selectedBotId, setSelectedBotId] = useState('default_bot');
  const selectedBotIdRef = useRef(selectedBotId);
  selectedBotIdRef.current = selectedBotId;

  const hubTtsCtxRef = useRef(null);
  const hubTtsNextTimeRef = useRef(0);
  const hubTtsSampleRateRef = useRef(24000);
  const hubTtsStreamIdRef = useRef(null);

  const liveAudioCtxRef = useRef(null);
  const liveAudioNextTimeRef = useRef(0);
  const liveAudioSrRef = useRef(24000);
  const liveAudioStreamIdRef = useRef(null);

  const applyPlaybackSinkId = useCallback(async (ctx) => {
    const id = (hubAppSettingsRef.current?.browser_audio_output_device_id || '').trim();
    if (!id || typeof ctx?.setSinkId !== 'function') return;
    try {
      await ctx.setSinkId(id);
    } catch (e) {
      console.warn('AudioContext.setSinkId failed', e);
    }
  }, []);

  const stopLiveAudio = useCallback(() => {
    const ctx = liveAudioCtxRef.current;
    liveAudioCtxRef.current = null;
    liveAudioStreamIdRef.current = null;
    liveAudioNextTimeRef.current = 0;
    if (ctx) {
      try {
        ctx.close();
      } catch {
        /* ignore */
      }
    }
  }, []);

  const stopHubTtsAudio = useCallback(() => {
    const ctx = hubTtsCtxRef.current;
    hubTtsCtxRef.current = null;
    hubTtsStreamIdRef.current = null;
    hubTtsNextTimeRef.current = 0;
    if (ctx) {
      try {
        ctx.close();
      } catch {
        /* ignore */
      }
    }
    stopLiveAudio();
  }, [stopLiveAudio]);

  const [logs, setLogs] = useState([]);
  const [toolCalls, setToolCalls] = useState([]);
  const [hubActivityLog, setHubActivityLog] = useState([]);
  const [livePreviewByDevice, setLivePreviewByDevice] = useState({});
  const [wakeListenByDevice, setWakeListenByDevice] = useState({});
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
        if (log.sender === 'ai' && log.streamId === stream_id && !log.liveTranscript) {
          return { ...log, text: (log.text || '') + delta };
        }
        return log;
      })
    );
  };

  const appendLiveTranscription = useCallback((device_id, role, stream_id, text) => {
    const did = device_id || selectedBotIdRef.current;
    if (did !== selectedBotIdRef.current) return;
    const sender = role === 'user' ? 'user' : 'ai';
    setLogs((prevLogs) => {
      let idx = -1;
      for (let i = prevLogs.length - 1; i >= 0; i--) {
        const l = prevLogs[i];
        if (l.liveTranscript && l.streamId === stream_id && l.sender === sender) {
          idx = i;
          break;
        }
      }
      if (idx >= 0) {
        const next = [...prevLogs];
        const cur = next[idx];
        next[idx] = { ...cur, text: (cur.text || '') + (text || '') };
        return next;
      }
      return [
        ...prevLogs,
        {
          id: Date.now() + Math.random(),
          time: new Date().toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
          }),
          sender,
          text: text || '',
          streamId: stream_id,
          liveTranscript: true,
        },
      ];
    });
  }, []);

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
            setLivePreviewByDevice((prev) => {
              const next = { ...prev };
              delete next[did];
              return next;
            });
            setWakeListenByDevice((prev) => {
              const next = { ...prev };
              delete next[did];
              return next;
            });
          } else if (message.type === 'wake_listen_state') {
            const did = message.device_id || 'default_bot';
            setWakeListenByDevice((prev) => {
              const next = { ...prev };
              if (message.mode == null || message.mode === '') {
                delete next[did];
              } else {
                next[did] = message.mode;
              }
              return next;
            });
          } else if (message.type === 'live_video_frame') {
            const did = message.device_id || 'default_bot';
            if (message.clear) {
              setLivePreviewByDevice((prev) => {
                const next = { ...prev };
                delete next[did];
                return next;
              });
            } else if (message.jpeg_base64) {
              setLivePreviewByDevice((prev) => ({
                ...prev,
                [did]: `data:image/jpeg;base64,${message.jpeg_base64}`,
              }));
            }
          } else if (message.type === 'vision_changed') {
            const did = message.device_id || 'default_bot';
            if (message.enabled === false) {
              setLivePreviewByDevice((prev) => {
                const next = { ...prev };
                delete next[did];
                return next;
              });
            }
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
          } else if (message.type === 'hub_app_settings_changed') {
            setHubAppSettings((prev) => ({
              ...(prev && typeof prev === 'object' ? prev : {}),
              ...(message.settings && typeof message.settings === 'object'
                ? message.settings
                : {}),
            }));
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
          } else if (message.type === 'hub_tts_start') {
            stopHubTtsAudio();
            const rate = Number(message.sample_rate) || 24000;
            hubTtsSampleRateRef.current = rate;
            hubTtsStreamIdRef.current = message.stream_id || null;
            const ctx = new AudioContext();
            hubTtsCtxRef.current = ctx;
            hubTtsNextTimeRef.current = ctx.currentTime + 0.08;
            void ctx.resume().catch(() => {});
            void applyPlaybackSinkId(ctx);
          } else if (message.type === 'hub_tts_chunk') {
            if (message.stream_id !== hubTtsStreamIdRef.current) return;
            const ctx = hubTtsCtxRef.current;
            if (!ctx || !message.b64) return;
            try {
              const int16 = base64ToInt16PCM(message.b64);
              if (!int16.length) return;
              const sr = hubTtsSampleRateRef.current;
              const buffer = ctx.createBuffer(1, int16.length, sr);
              const ch = buffer.getChannelData(0);
              for (let i = 0; i < int16.length; i++) ch[i] = int16[i] / 32768.0;
              const src = ctx.createBufferSource();
              src.buffer = buffer;
              src.connect(ctx.destination);
              const startAt = Math.max(ctx.currentTime, hubTtsNextTimeRef.current);
              src.start(startAt);
              hubTtsNextTimeRef.current = startAt + buffer.duration;
            } catch (e) {
              console.error('Hub TTS chunk playback failed', e);
            }
          } else if (message.type === 'hub_tts_end') {
            if (message.stream_id !== hubTtsStreamIdRef.current) return;
            hubTtsStreamIdRef.current = null;
            if (message.error) {
              console.warn('Hub TTS:', message.error);
            }
          } else if (message.type === 'live_transcription') {
            appendLiveTranscription(
              message.device_id,
              message.role,
              message.stream_id,
              message.text
            );
          } else if (message.type === 'live_audio_chunk') {
            const did = message.device_id || selectedBotIdRef.current;
            if (did !== selectedBotIdRef.current) return;
            if (message.stream_id && message.stream_id !== liveAudioStreamIdRef.current) {
              stopLiveAudio();
              liveAudioStreamIdRef.current = message.stream_id;
              const rate = Number(message.sample_rate) || 24000;
              liveAudioSrRef.current = rate;
              const ctx = new AudioContext();
              liveAudioCtxRef.current = ctx;
              liveAudioNextTimeRef.current = ctx.currentTime + 0.08;
              void ctx.resume().catch(() => {});
              void applyPlaybackSinkId(ctx);
            }
            const ctx = liveAudioCtxRef.current;
            if (!ctx || !message.b64) return;
            try {
              const int16 = base64ToInt16PCM(message.b64);
              if (!int16.length) return;
              const sr = liveAudioSrRef.current;
              const buffer = ctx.createBuffer(1, int16.length, sr);
              const ch = buffer.getChannelData(0);
              for (let i = 0; i < int16.length; i++) ch[i] = int16[i] / 32768.0;
              const src = ctx.createBufferSource();
              src.buffer = buffer;
              src.connect(ctx.destination);
              const startAt = Math.max(ctx.currentTime, liveAudioNextTimeRef.current);
              src.start(startAt);
              liveAudioNextTimeRef.current = startAt + buffer.duration;
            } catch (e) {
              console.error('Live audio chunk playback failed', e);
            }
          }
        } catch {
          console.error('Failed to parse message:', event.data);
        }
      };

      ws.onclose = () => {
        stopHubTtsAudio();
        setLivePreviewByDevice({});
        setWakeListenByDevice({});
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
      stopHubTtsAudio();
      if (ws) ws.close();
    };
  }, [
    geminiConfigured,
    mergeBotList,
    refreshBotList,
    stopHubTtsAudio,
    appendLiveTranscription,
    stopLiveAudio,
    applyPlaybackSinkId,
  ]);

  useEffect(() => {
    if (geminiConfigured !== true) {
      return undefined;
    }
    if (!hubAppSettings || hubAppSettings.live_voice_source !== 'browser') {
      return undefined;
    }

    let vbWs = null;
    let mediaStream = null;
    let audioCtx = null;
    let proc = null;
    let carry = new Int16Array(0);
    const did = selectedBotId;

    const run = async () => {
      const url = getHubWebSocketUrl(
        `/ws/voice-bridge?device_id=${encodeURIComponent(did)}`
      );
      vbWs = new WebSocket(url);
      await new Promise((resolve, reject) => {
        const t = window.setTimeout(() => reject(new Error('voice bridge connect timeout')), 15000);
        vbWs.onopen = () => {
          window.clearTimeout(t);
          resolve();
        };
        vbWs.onerror = () => {
          window.clearTimeout(t);
          reject(new Error('voice bridge WebSocket error'));
        };
      });

      const constraints = {
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
        },
      };
      const inId = (hubAppSettings.browser_audio_input_device_id || '').trim();
      if (inId) {
        constraints.audio.deviceId = { exact: inId };
      }
      mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
      audioCtx = new AudioContext();
      await audioCtx.resume();
      const source = audioCtx.createMediaStreamSource(mediaStream);
      const bufferSize = 4096;
      proc = audioCtx.createScriptProcessor(bufferSize, 1, 1);
      proc.onaudioprocess = (ev) => {
        if (!vbWs || vbWs.readyState !== WebSocket.OPEN) return;
        const input = ev.inputBuffer.getChannelData(0);
        const chunk = floatTo16kPcmS16le(input, audioCtx.sampleRate);
        if (!chunk.length) return;
        const merged = new Int16Array(carry.length + chunk.length);
        merged.set(carry);
        merged.set(chunk, carry.length);
        const frameSamples = 320;
        let off = 0;
        while (off + frameSamples <= merged.length) {
          vbWs.send(int16ToCopyBuffer(merged.subarray(off, off + frameSamples)));
          off += frameSamples;
        }
        carry = merged.subarray(off);
      };
      source.connect(proc);
      proc.connect(audioCtx.destination);
    };

    run().catch((e) => console.error('[voice-bridge]', e));

    return () => {
      carry = new Int16Array(0);
      try {
        proc?.disconnect();
      } catch {
        /* ignore */
      }
      try {
        mediaStream?.getTracks().forEach((t) => t.stop());
      } catch {
        /* ignore */
      }
      try {
        void audioCtx?.close();
      } catch {
        /* ignore */
      }
      if (vbWs && vbWs.readyState === WebSocket.OPEN) {
        vbWs.close();
      }
    };
  }, [geminiConfigured, hubAppSettings, selectedBotId]);

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

  const hubStreamRelevant =
    wsStatus === 'connected' &&
    (botUiStatus[selectedBotId] === 'online' || botUiStatus[selectedBotId] === 'working');
  const wakeListenModeForSelected = hubStreamRelevant
    ? wakeListenByDevice[selectedBotId] || 'wake_required'
    : null;

  const soulDeviceId = setupState.walkthroughDeviceId || selectedBotId;
  const hubStreamRelevantSoul =
    wsStatus === 'connected' &&
    (botUiStatus[soulDeviceId] === 'online' || botUiStatus[soulDeviceId] === 'working');
  const wakeListenModeSoul = hubStreamRelevantSoul
    ? wakeListenByDevice[soulDeviceId] || 'wake_required'
    : null;

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
            livePreviewSrc={livePreviewByDevice[selectedBotId] || null}
            wakeListenMode={wakeListenModeForSelected}
            wsStatus={wsStatus}
            textMessage={textMessage}
            setTextMessage={setTextMessage}
            isSendingText={isSendingText}
            onSendTextCommand={handleSendTextCommand}
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
                livePreviewSrc={
                  livePreviewByDevice[setupState.walkthroughDeviceId || selectedBotId] || null
                }
                wakeListenMode={wakeListenModeSoul}
                wsStatus={wsStatus}
                textMessage={textMessage}
                setTextMessage={setTextMessage}
                isSendingText={isSendingText}
                onSendTextCommand={handleSendTextCommand}
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
