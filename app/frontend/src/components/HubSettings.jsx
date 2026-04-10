import React, { useState, useEffect } from 'react';
import './BotSettings.css';
import {
  getHubSettings,
  postHubSettings,
  getHubAppSettings,
  postHubAppSettings,
  listBots,
  startBootstrapSoul,
} from './setupService';

const TIMEZONE_OPTIONS = [
  { value: 'EST5EDT,M3.2.0/2,M11.1.0/2', label: 'US Eastern (EST/EDT)' },
  { value: 'CST6CDT,M3.2.0/2,M11.1.0/2', label: 'US Central (CST/CDT)' },
  { value: 'MST7MDT,M3.2.0/2,M11.1.0/2', label: 'US Mountain (MST/MDT)' },
  { value: 'PST8PDT,M3.2.0/2,M11.1.0/2', label: 'US Pacific (PST/PDT)' },
  { value: 'UTC0', label: 'UTC' },
];

const HubSettings = () => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState(null);
  const [saveStatus, setSaveStatus] = useState(null);
  const [form, setForm] = useState({
    gemini_api_key: '',
    nominatim_user_agent: '',
  });

  const [locSaving, setLocSaving] = useState(false);
  const [locSaveStatus, setLocSaveStatus] = useState(null);
  const [locForm, setLocForm] = useState({
    timezone_rule: TIMEZONE_OPTIONS[0].value,
  });

  const [voiceSaving, setVoiceSaving] = useState(false);
  const [voiceSaveStatus, setVoiceSaveStatus] = useState(null);
  const [audioInputs, setAudioInputs] = useState([]);
  const [audioOutputs, setAudioOutputs] = useState([]);
  const [voiceForm, setVoiceForm] = useState({
    live_voice_source: 'esp32',
    browser_audio_input_device_id: '',
    browser_audio_output_device_id: '',
  });

  const [soulBots, setSoulBots] = useState([]);
  const [soulDeviceId, setSoulDeviceId] = useState('default_bot');
  const [soulRunning, setSoulRunning] = useState(false);
  const [soulFeedback, setSoulFeedback] = useState(null);

  const hasTimezoneOption = TIMEZONE_OPTIONS.some((tz) => tz.value === locForm.timezone_rule);

  const load = async () => {
    try {
      const [v, app, botsRes] = await Promise.all([
        getHubSettings(),
        getHubAppSettings(),
        listBots().catch(() => ({ bots: [] })),
      ]);
      const bots = botsRes.bots || [];
      setSoulBots(bots);
      setSoulDeviceId((prev) => {
        if (bots.some((b) => b.device_id === prev)) return prev;
        return bots[0]?.device_id || 'default_bot';
      });
      setView(v);
      setForm((f) => ({
        ...f,
        nominatim_user_agent: v.nominatim_user_agent || '',
      }));
      setLocForm({
        timezone_rule: app.timezone_rule || TIMEZONE_OPTIONS[0].value,
      });
      setVoiceForm({
        live_voice_source:
          app.live_voice_source === 'browser' ? 'browser' : 'esp32',
        browser_audio_input_device_id: app.browser_audio_input_device_id || '',
        browser_audio_output_device_id: app.browser_audio_output_device_id || '',
      });
    } catch (e) {
      console.error(e);
      setSaveStatus('error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const clearKey = async (field) => {
    setSaving(true);
    setSaveStatus(null);
    try {
      const res = await postHubSettings({ [field]: '' });
      setView(res.settings);
      setForm((f) => ({ ...f, [field]: '' }));
      setSaveStatus('success');
    } catch (err) {
      console.error(err);
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitKeys = async (e) => {
    e.preventDefault();
    setSaving(true);
    setSaveStatus(null);
    try {
      const payload = {};
      if (form.gemini_api_key.trim()) payload.gemini_api_key = form.gemini_api_key.trim();
      if (!view?.nominatim_user_agent_from_env) {
        const nextN = form.nominatim_user_agent.trim();
        const prevN = (view?.nominatim_user_agent || '').trim();
        if (nextN !== prevN) {
          payload.nominatim_user_agent = nextN;
        }
      }
      if (Object.keys(payload).length === 0) {
        setSaving(false);
        setSaveStatus(null);
        return;
      }
      const res = await postHubSettings(payload);
      setView(res.settings);
      setForm((f) => ({
        ...f,
        gemini_api_key: '',
      }));
      setSaveStatus('success');
    } catch (err) {
      console.error(err);
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  };

  const refreshAudioDevices = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
    } catch (err) {
      console.warn('[HubSettings] getUserMedia for enumerateDevices', err);
    }
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      setAudioInputs(list.filter((d) => d.kind === 'audioinput'));
      setAudioOutputs(list.filter((d) => d.kind === 'audiooutput'));
    } catch (err) {
      console.error(err);
    }
  };

  const handleGiveSoul = async () => {
    const warn = [
      "Give me a soul resets this bot to a fresh persona baseline:",
      '',
      '• Hub chat history is cleared.',
      '• SOUL, IDENTITY, USER, TOOLS, MEMORY, HEARTBEAT, and AGENTS are overwritten from the hub persona templates (persona_defaults).',
      '• A new BOOTSTRAP.md is written and the soul ritual runs with Gemini.',
      '• Daily logs under logs/daily/ and .heartbeat_state.json are removed so the model is not fed old diary context.',
      '',
      'Face profiles and saved Pixel settings (model, vision, etc.) are unchanged.',
      '',
      'Continue?',
    ].join('\n');
    if (!window.confirm(warn)) return;

    setSoulRunning(true);
    setSoulFeedback(null);
    try {
      await startBootstrapSoul(soulDeviceId);
      setSoulFeedback({
        type: 'success',
        text: 'Bootstrap ritual started. Open the dashboard Intelligence Feed to follow progress.',
      });
    } catch (err) {
      console.error(err);
      setSoulFeedback({
        type: 'error',
        text: err.message || 'Bootstrap ritual failed.',
      });
    } finally {
      setSoulRunning(false);
    }
  };

  const handleSubmitVoice = async (e) => {
    e.preventDefault();
    setVoiceSaving(true);
    setVoiceSaveStatus(null);
    try {
      const res = await postHubAppSettings({
        live_voice_source: voiceForm.live_voice_source,
        browser_audio_input_device_id: voiceForm.browser_audio_input_device_id || '',
        browser_audio_output_device_id: voiceForm.browser_audio_output_device_id || '',
      });
      const saved = res.settings || {};
      setVoiceForm({
        live_voice_source:
          saved.live_voice_source === 'browser' ? 'browser' : 'esp32',
        browser_audio_input_device_id: saved.browser_audio_input_device_id || '',
        browser_audio_output_device_id: saved.browser_audio_output_device_id || '',
      });
      setVoiceSaveStatus('success');
    } catch (err) {
      console.error(err);
      setVoiceSaveStatus('error');
    } finally {
      setVoiceSaving(false);
    }
  };

  const handleSubmitLocation = async (e) => {
    e.preventDefault();
    setLocSaving(true);
    setLocSaveStatus(null);
    try {
      const res = await postHubAppSettings({
        timezone_rule: locForm.timezone_rule,
      });
      const saved = res.settings || {};
      setLocForm({
        timezone_rule: saved.timezone_rule || TIMEZONE_OPTIONS[0].value,
      });
      setLocSaveStatus('success');
    } catch (err) {
      console.error(err);
      setLocSaveStatus('error');
    } finally {
      setLocSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="settings-panel-inner">
        <div className="scanning-container">
          <div className="pulse-ring mx-auto"></div>
          <p>Loading hub settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-panel-inner fade-in">
      <div className="settings-subheader">
        <h2>Hub / application</h2>
      </div>

      <p className="help-text" style={{ marginTop: 0 }}>
        API keys live in <code>hub_secrets.json</code>. Hub clock settings live in <code>hub_app_settings.json</code>.
        {view?.data_dir && (
          <>
            {' '}
            Data dir:{' '}
            <code className="bot-identifier" style={{ display: 'inline', padding: '0.15rem 0.4rem' }}>
              {view.data_dir}
            </code>
          </>
        )}
      </p>

      <h3 className="hub-section-title">API keys</h3>
      {view?.nominatim_user_agent_from_env && (
        <p className="help-text">Nominatim user agent is set via <code>NOMINATIM_USER_AGENT</code> (overrides file).</p>
      )}

      <form className="settings-form" onSubmit={handleSubmitKeys}>
        <div className="form-group">
          <label htmlFor="hubGeminiKey">Gemini API key</label>
          {view?.gemini_api_key_configured && <p className="help-text">Stored: {view.gemini_api_key_masked}</p>}
          <input
            id="hubGeminiKey"
            type="password"
            autoComplete="off"
            className="holo-input"
            value={form.gemini_api_key}
            onChange={(e) => setForm((f) => ({ ...f, gemini_api_key: e.target.value }))}
            placeholder={view?.gemini_api_key_configured ? 'Enter new key to replace' : 'Enter API key'}
          />
          {view?.gemini_api_key_configured && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ alignSelf: 'flex-start', marginTop: '0.35rem' }}
              disabled={saving}
              onClick={() => clearKey('gemini_api_key')}
            >
              Remove stored Gemini key
            </button>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="hubNominatim">Nominatim user agent</label>
          <input
            id="hubNominatim"
            type="text"
            className="holo-input"
            value={form.nominatim_user_agent}
            onChange={(e) => setForm((f) => ({ ...f, nominatim_user_agent: e.target.value }))}
            placeholder="App name + contact (OSM policy)"
            disabled={Boolean(view?.nominatim_user_agent_from_env)}
          />
        </div>

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save API keys'}
            </button>
          </div>
        </div>
        {saveStatus === 'success' && <div className="status-message success slide-enter">API keys saved.</div>}
        {saveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save API keys.</div>
        )}
      </form>

      <h3 className="hub-section-title">Browser live voice</h3>
      <p className="help-text">
        Use your <strong>PC microphone and speakers</strong> in the dashboard so the browser can apply echo
        cancellation (same machine for capture and playback). Requires a secure context (HTTPS or localhost).
        Saved device IDs are browser- and machine-specific; use <strong>Refresh devices</strong> if hardware
        changes. With <strong>wake word</strong> enabled for the bot in Pixel settings, Pixel still listens for
        the wake phrase on its mic; only your spoken question is taken from the browser mic after wake.
      </p>

      <form className="settings-form" onSubmit={handleSubmitVoice}>
        <div className="form-group">
          <label htmlFor="hubLiveVoiceSource">Voice input source</label>
          <div className="select-wrapper">
            <select
              id="hubLiveVoiceSource"
              value={voiceForm.live_voice_source}
              onChange={(e) =>
                setVoiceForm((f) => ({ ...f, live_voice_source: e.target.value }))
              }
              className="holo-select"
            >
              <option value="esp32">Pixel (ESP32) microphone</option>
              <option value="browser">This computer (browser mic + speakers)</option>
            </select>
          </div>
        </div>

        {voiceForm.live_voice_source === 'browser' && (
          <>
            <div className="form-group">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void refreshAudioDevices()}
              >
                Refresh audio devices
              </button>
            </div>
            <div className="form-group">
              <label htmlFor="hubBrowserMic">Microphone</label>
              <div className="select-wrapper">
                <select
                  id="hubBrowserMic"
                  value={voiceForm.browser_audio_input_device_id}
                  onChange={(e) =>
                    setVoiceForm((f) => ({
                      ...f,
                      browser_audio_input_device_id: e.target.value,
                    }))
                  }
                  className="holo-select"
                >
                  <option value="">System default</option>
                  {audioInputs.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || `Microphone (${d.deviceId.slice(0, 8)}…)`}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="form-group">
              <label htmlFor="hubBrowserSpeaker">Speakers (playback)</label>
              <div className="select-wrapper">
                <select
                  id="hubBrowserSpeaker"
                  value={voiceForm.browser_audio_output_device_id}
                  onChange={(e) =>
                    setVoiceForm((f) => ({
                      ...f,
                      browser_audio_output_device_id: e.target.value,
                    }))
                  }
                  className="holo-select"
                >
                  <option value="">System default</option>
                  {audioOutputs.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || `Output (${d.deviceId.slice(0, 8)}…)`}
                    </option>
                  ))}
                </select>
              </div>
              <p className="help-text">
                Output routing uses <code>AudioContext.setSinkId</code> where supported (e.g. Chromium).
              </p>
            </div>
          </>
        )}

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={voiceSaving}>
              {voiceSaving ? 'Saving...' : 'Save voice settings'}
            </button>
          </div>
        </div>
        {voiceSaveStatus === 'success' && (
          <div className="status-message success slide-enter">Voice settings saved.</div>
        )}
        {voiceSaveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save voice settings.</div>
        )}
      </form>

      <h3 className="hub-section-title">Give me a soul</h3>
      <p className="help-text">
        Resets persona markdown to hub templates (like a fresh install), clears that bot&apos;s hub chat history,
        removes daily logs and heartbeat state on disk, writes <code>BOOTSTRAP.md</code>, and runs the bootstrap ritual
        with Gemini. Confirm to proceed.
      </p>
      <div className="settings-form">
        <div className="form-group">
          <label htmlFor="hubSoulBot">Bot</label>
          <div className="select-wrapper">
            <select
              id="hubSoulBot"
              className="holo-select"
              value={soulDeviceId}
              onChange={(e) => setSoulDeviceId(e.target.value)}
              disabled={soulRunning}
            >
              {soulBots.length === 0 ? (
                <option value="default_bot">default_bot</option>
              ) : (
                soulBots.map((b) => (
                  <option key={b.device_id} value={b.device_id}>
                    {b.device_id}
                    {b.online === true ? ' (online)' : ''}
                  </option>
                ))
              )}
            </select>
          </div>
        </div>
        <div className="form-actions">
          <div className="form-actions-trailing">
            <button
              type="button"
              className="btn btn-primary"
              disabled={soulRunning}
              onClick={() => void handleGiveSoul()}
            >
              {soulRunning ? 'Starting…' : 'Give me a soul'}
            </button>
          </div>
        </div>
        {soulFeedback?.type === 'success' && (
          <div className="status-message success slide-enter">{soulFeedback.text}</div>
        )}
        {soulFeedback?.type === 'error' && (
          <div className="status-message error slide-enter">{soulFeedback.text}</div>
        )}
      </div>

      <h3 className="hub-section-title">Clock</h3>
      <p className="help-text">
        Used for Pixel clock sync (hub-wide, not per Pixel tab). Saving clears in-memory chat
        history on the hub.
      </p>

      <form className="settings-form" onSubmit={handleSubmitLocation}>
        <div className="form-group">
          <label htmlFor="hubTimezoneSelect">Timezone (clock sync)</label>
          <div className="select-wrapper">
            <select
              id="hubTimezoneSelect"
              value={locForm.timezone_rule}
              onChange={(e) => setLocForm((f) => ({ ...f, timezone_rule: e.target.value }))}
              className="holo-select"
            >
              {!hasTimezoneOption && <option value={locForm.timezone_rule}>Custom ({locForm.timezone_rule})</option>}
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz.value} value={tz.value}>
                  {tz.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={locSaving}>
              {locSaving ? 'Saving...' : 'Save clock'}
            </button>
          </div>
        </div>
        {locSaveStatus === 'success' && (
          <div className="status-message success slide-enter">Clock saved.</div>
        )}
        {locSaveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save clock.</div>
        )}
      </form>
    </div>
  );
};

export default HubSettings;
