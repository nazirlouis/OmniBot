import React, { useState, useEffect, useCallback } from 'react';
import './BotSettings.css';
import './SettingsShell.css';
import {
  getBotSettings,
  updateBotSettings,
  resetBotSettingsToDefault,
  listFaceProfiles,
  createFaceProfile,
  deleteFaceProfile,
  uploadFaceReference,
  captureFaceFromPixel,
  getPersonaFile,
  getPersonaStatus,
  putPersonaFile,
} from './setupService';

/** Matches hub `normalize_gemini_thinking_level` / Gemini 3 docs thinking levels. */
const GEMINI_THINKING_LEVELS = ['auto', 'minimal', 'low', 'medium', 'high'];

const BotSettings = ({ setAppMode, embedded = false, deviceId = 'default_bot', onBotsChanged }) => {
  const [model, setModel] = useState('gemini-3.1-flash-lite-preview');
  const [thinkingLevel, setThinkingLevel] = useState('minimal');
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [wakeWordEnabled, setWakeWordEnabled] = useState(true);
  const [presenceScanEnabled, setPresenceScanEnabled] = useState(false);
  const [presenceIntervalSec, setPresenceIntervalSec] = useState(5);
  const [greetingCooldownMin, setGreetingCooldownMin] = useState(30);
  const [heartbeatIntervalMin, setHeartbeatIntervalMin] = useState(30);
  const [heartbeatEnabled, setHeartbeatEnabled] = useState(true);
  const [personaTab, setPersonaTab] = useState('soul');
  const [personaDrafts, setPersonaDrafts] = useState({
    soul: '',
    identity: '',
    user: '',
    tools: '',
    memory: '',
    heartbeat: '',
    agents: '',
  });
  const [personaStatus, setPersonaStatus] = useState(null);
  const [personaSaving, setPersonaSaving] = useState(false);
  const [personaMessage, setPersonaMessage] = useState(null);
  const [faceProfiles, setFaceProfiles] = useState([]);
  const [newProfileName, setNewProfileName] = useState('');
  const [faceBusyId, setFaceBusyId] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const [faceMessage, setFaceMessage] = useState(null);

  const loadProfiles = useCallback(async () => {
    try {
      const data = await listFaceProfiles(deviceId);
      setFaceProfiles(data.profiles || []);
    } catch (err) {
      console.error('Failed to load face profiles', err);
    }
  }, [deviceId]);

  const loadPersona = useCallback(async () => {
    setPersonaMessage(null);
    try {
      const files = ['soul', 'identity', 'user', 'tools', 'memory', 'heartbeat', 'agents'];
      const entries = await Promise.all(
        files.map(async (f) => {
          const d = await getPersonaFile(deviceId, f);
          return [f, d.content ?? ''];
        })
      );
      setPersonaDrafts(Object.fromEntries(entries));
      const st = await getPersonaStatus(deviceId);
      setPersonaStatus(st);
    } catch (err) {
      console.error('Failed to load persona files', err);
      setPersonaMessage('Could not load persona files (is the hub updated?)');
    }
  }, [deviceId]);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await getBotSettings(deviceId);
        setModel(data.model);
        setThinkingLevel(
          GEMINI_THINKING_LEVELS.includes(data.thinking_level) ? data.thinking_level : 'minimal'
        );
        setVisionEnabled(Boolean(data.vision_enabled));
        setWakeWordEnabled(data.wake_word_enabled !== false);
        setPresenceScanEnabled(Boolean(data.presence_scan_enabled));
        setPresenceIntervalSec(
          typeof data.presence_scan_interval_sec === 'number'
            ? data.presence_scan_interval_sec
            : 5
        );
        setGreetingCooldownMin(
          typeof data.greeting_cooldown_minutes === 'number'
            ? data.greeting_cooldown_minutes
            : 30
        );
        setHeartbeatIntervalMin(
          typeof data.heartbeat_interval_minutes === 'number'
            ? data.heartbeat_interval_minutes
            : 30
        );
        setHeartbeatEnabled(data.heartbeat_enabled !== false);
        await loadProfiles();
        await loadPersona();
      } catch (err) {
        console.error('Failed to fetch settings', err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchSettings();
  }, [deviceId, loadProfiles, loadPersona]);

  const handleSave = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    setSaveStatus(null);
    try {
      const res = await updateBotSettings(deviceId, {
        model,
        thinking_level: thinkingLevel,
        vision_enabled: visionEnabled,
        wake_word_enabled: wakeWordEnabled,
        presence_scan_enabled: presenceScanEnabled,
        presence_scan_interval_sec: Math.min(300, Math.max(3, Number(presenceIntervalSec) || 5)),
        greeting_cooldown_minutes: Math.min(720, Math.max(1, Number(greetingCooldownMin) || 30)),
        heartbeat_interval_minutes: Math.min(720, Math.max(5, Number(heartbeatIntervalMin) || 30)),
        heartbeat_enabled: heartbeatEnabled,
      });
      const saved = res.settings || {};
      setModel(saved.model);
      setThinkingLevel(
        GEMINI_THINKING_LEVELS.includes(saved.thinking_level) ? saved.thinking_level : 'minimal'
      );
      setVisionEnabled(Boolean(saved.vision_enabled));
      setWakeWordEnabled(saved.wake_word_enabled !== false);
      setPresenceScanEnabled(Boolean(saved.presence_scan_enabled));
      setPresenceIntervalSec(saved.presence_scan_interval_sec ?? 5);
      setGreetingCooldownMin(saved.greeting_cooldown_minutes ?? 30);
      setHeartbeatIntervalMin(saved.heartbeat_interval_minutes ?? 30);
      setHeartbeatEnabled(saved.heartbeat_enabled !== false);
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to save settings', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleResetToDefaults = async () => {
    if (
      !window.confirm(
        'Reset Pixel model, thinking level, vision, wake word, presence scan, and heartbeat settings to defaults? ' +
          'SOUL.md, IDENTITY.md, USER.md, TOOLS.md, MEMORY.md, HEARTBEAT.md, and AGENTS.md will be overwritten with hub templates (your edits are lost). BOOTSTRAP.md is removed if present. Daily logs under logs/daily/ are kept. Hub clock is unchanged.'
      )
    ) {
      return;
    }
    setIsSaving(true);
    setSaveStatus(null);
    try {
      const res = await resetBotSettingsToDefault(deviceId);
      if (res.status !== 'success' || !res.settings) {
        throw new Error(res.detail || 'Reset failed');
      }
      const saved = res.settings;
      setModel(saved.model);
      setThinkingLevel(
        GEMINI_THINKING_LEVELS.includes(saved.thinking_level) ? saved.thinking_level : 'minimal'
      );
      setVisionEnabled(Boolean(saved.vision_enabled));
      setWakeWordEnabled(saved.wake_word_enabled !== false);
      setPresenceScanEnabled(Boolean(saved.presence_scan_enabled));
      setPresenceIntervalSec(saved.presence_scan_interval_sec ?? 5);
      setGreetingCooldownMin(saved.greeting_cooldown_minutes ?? 30);
      setHeartbeatIntervalMin(saved.heartbeat_interval_minutes ?? 30);
      setHeartbeatEnabled(saved.heartbeat_enabled !== false);
      await loadPersona();
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to reset settings', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleAddProfile = async () => {
    const name = (newProfileName || '').trim() || 'Person';
    setFaceMessage(null);
    setFaceBusyId('__create__');
    try {
      await createFaceProfile(deviceId, name);
      setNewProfileName('');
      await loadProfiles();
      setFaceMessage('Profile added. Upload a reference photo or capture from Pixel.');
    } catch (err) {
      setFaceMessage(err.message || 'Could not add profile');
    } finally {
      setFaceBusyId(null);
    }
  };

  const handleDeleteProfile = async (profileId) => {
    if (!window.confirm('Delete this person and their reference photos?')) return;
    setFaceBusyId(profileId);
    setFaceMessage(null);
    try {
      await deleteFaceProfile(deviceId, profileId);
      await loadProfiles();
    } catch (err) {
      setFaceMessage(err.message || 'Delete failed');
    } finally {
      setFaceBusyId(null);
    }
  };

  const handleUploadFile = async (profileId, e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setFaceBusyId(profileId);
    setFaceMessage(null);
    try {
      await uploadFaceReference(deviceId, profileId, file);
      setFaceMessage('Reference photo saved.');
      await loadProfiles();
    } catch (err) {
      setFaceMessage(err.message || 'Upload failed');
    } finally {
      setFaceBusyId(null);
    }
  };

  const handleCaptureFromPixel = async (profileId) => {
    setFaceBusyId(profileId);
    setFaceMessage(null);
    try {
      await captureFaceFromPixel(deviceId, profileId);
      setFaceMessage('Capture requested. Pixel will take a photo when it receives the command.');
    } catch (err) {
      setFaceMessage(err.message || 'Capture failed — is Pixel online?');
    } finally {
      setFaceBusyId(null);
    }
  };

  const handleSavePersonaFile = async () => {
    setPersonaSaving(true);
    setPersonaMessage(null);
    try {
      await putPersonaFile(deviceId, personaTab, personaDrafts[personaTab] ?? '');
      const st = await getPersonaStatus(deviceId);
      setPersonaStatus(st);
      setPersonaMessage(`Saved ${personaTab}.md`);
      setTimeout(() => setPersonaMessage(null), 2500);
    } catch (err) {
      setPersonaMessage(err.message || 'Save failed');
    } finally {
      setPersonaSaving(false);
    }
  };

  if (isLoading) {
    const loadingInner = (
      <div className="scanning-container">
        <div className="pulse-ring mx-auto"></div>
        <p>Loading Pixel settings...</p>
      </div>
    );
    return embedded ? (
      <div className="settings-panel-inner">{loadingInner}</div>
    ) : (
      <div className="settings-container">{loadingInner}</div>
    );
  }

  const header = (
    <div className={embedded ? 'settings-subheader' : 'settings-header'}>
      <h2>{embedded ? 'Pixel bot' : 'Pixel bot'}</h2>
      <div className="bot-identifier">
        <span className="id-label">TARGET:</span> {deviceId}
      </div>
    </div>
  );

  const formBody = (
    <form className="settings-form" onSubmit={handleSave}>
      <div className="form-group">
        <label htmlFor="visionSelect">Vision input to model</label>
        <div className="select-wrapper">
          <select
            id="visionSelect"
            value={visionEnabled ? 'on' : 'off'}
            onChange={(e) => setVisionEnabled(e.target.value === 'on')}
            className="holo-select"
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </select>
        </div>
        <p className="help-text">
          When on, video can be sent to Gemini with voice turns (wake path is audio-only; tap recording removed). Stored
          per bot.
        </p>
      </div>

      <div className="form-group">
        <label htmlFor="wakeWordSelect">Wake word (mic stream to hub)</label>
        <div className="select-wrapper">
          <select
            id="wakeWordSelect"
            value={wakeWordEnabled ? 'on' : 'off'}
            onChange={(e) => setWakeWordEnabled(e.target.value === 'on')}
            className="holo-select"
          >
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </div>
        <p className="help-text">
          When on, Pixel streams the microphone to the hub for wake-word detection and end-of-speech (requires hub on
          your LAN). Train or place a custom model as <code>pixel.onnx</code> on the hub, or use the default test model
          (see hub logs).
        </p>
      </div>

      <div className="form-group">
        <label htmlFor="presenceScan">Presence face scan (hub)</label>
        <div className="select-wrapper">
          <select
            id="presenceScan"
            value={presenceScanEnabled ? 'on' : 'off'}
            onChange={(e) => setPresenceScanEnabled(e.target.value === 'on')}
            className="holo-select"
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </select>
        </div>
        <p className="help-text">
          When on, Pixel sends small snapshots to the hub for face matching. Greeting uses Gemini; images stay on your
          LAN. Default off for privacy.
        </p>
      </div>

      <div className="form-group">
        <label htmlFor="presenceInterval">Snapshot interval (seconds)</label>
        <input
          id="presenceInterval"
          type="number"
          min={3}
          max={300}
          value={presenceIntervalSec}
          onChange={(e) => setPresenceIntervalSec(Number(e.target.value))}
          className="holo-textarea"
          style={{ maxWidth: '120px', minHeight: 'unset', height: '40px' }}
        />
        <p className="help-text">How often Pixel captures a frame while idle (3–300).</p>
      </div>

      <div className="form-group">
        <label htmlFor="greetingCooldown">Greeting cooldown (minutes)</label>
        <input
          id="greetingCooldown"
          type="number"
          min={1}
          max={720}
          value={greetingCooldownMin}
          onChange={(e) => setGreetingCooldownMin(Number(e.target.value))}
          className="holo-textarea"
          style={{ maxWidth: '120px', minHeight: 'unset', height: '40px' }}
        />
        <p className="help-text">Minimum time between automated greetings for the same person (1–720).</p>
      </div>

      <div className="form-group">
        <label>Face recognition — enrolled people</label>
        <p className="help-text">
          Add a person, then upload a clear frontal photo or use Capture from Pixel while the bot is connected.
        </p>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <input
            type="text"
            placeholder="Display name"
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            className="holo-textarea"
            style={{ flex: '1', minWidth: '140px', minHeight: 'unset', height: '40px' }}
          />
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleAddProfile}
            disabled={isSaving || faceBusyId}
          >
            {faceBusyId === '__create__' ? 'Adding…' : 'Add person'}
          </button>
        </div>
        <ul className="face-profile-list" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {faceProfiles.map((p) => (
            <li
              key={p.profile_id}
              style={{
                border: '1px solid rgba(255,255,255,0.15)',
                borderRadius: '8px',
                padding: '10px',
                marginBottom: '8px',
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: '8px' }}>{p.display_name || p.profile_id}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
                <label className="btn btn-secondary" style={{ cursor: 'pointer', margin: 0 }}>
                  Upload photo
                  <input
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={(e) => handleUploadFile(p.profile_id, e)}
                    disabled={!!faceBusyId}
                  />
                </label>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => handleCaptureFromPixel(p.profile_id)}
                  disabled={!!faceBusyId}
                >
                  {faceBusyId === p.profile_id ? '…' : 'Capture from Pixel'}
                </button>
                <button
                  type="button"
                  className="btn btn-reset-defaults"
                  onClick={() => handleDeleteProfile(p.profile_id)}
                  disabled={!!faceBusyId}
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
        {faceMessage && (
          <p className="help-text" style={{ marginTop: '8px', color: '#9cf' }}>
            {faceMessage}
          </p>
        )}
      </div>

      <div className="form-group">
        <label htmlFor="modelSelect">Generative model</label>
        <div className="select-wrapper">
          <select
            id="modelSelect"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="holo-select"
          >
            <option value="gemini-3.1-flash-lite-preview">Gemini 3.1 Flash Lite (default)</option>
            <option value="gemini-3-flash-preview">Gemini 3 Flash</option>
            <option value="gemini-3.1-pro-preview">Gemini 3.1 Pro</option>
          </select>
        </div>
      </div>

      <div className="form-group">
        <label htmlFor="thinkingLevelSelect">Gemini thinking level</label>
        <div className="select-wrapper">
          <select
            id="thinkingLevelSelect"
            value={thinkingLevel}
            onChange={(e) => setThinkingLevel(e.target.value)}
            className="holo-select"
          >
            <option value="auto">Auto (API default — dynamic thinking)</option>
            <option value="minimal">Minimal (Flash / Flash-Lite; lowest latency)</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High (deeper reasoning; slower)</option>
          </select>
        </div>
        <p className="help-text">
          Maps to Gemini 3 <code>thinking_config.thinking_level</code> (see{' '}
          <a
            href="https://ai.google.dev/gemini-api/docs/thinking"
            target="_blank"
            rel="noopener noreferrer"
          >
            Thinking (Gemini API docs)
          </a>
          ). <strong>Auto</strong> does not send a level — the model uses its default dynamic thinking.{' '}
          <strong>minimal</strong> is not supported on Gemini 3.1 Pro; Flash and Flash-Lite support the full set per
          the docs table. If a request fails, try another level or model.
        </p>
      </div>

      <div className="form-group persona-section">
        <label>Persona (AGENTS / SOUL / IDENTITY / USER / TOOLS / MEMORY / HEARTBEAT)</label>
        <p className="help-text">
          Markdown files on the hub (OpenClaw-style). <strong>AGENTS.md</strong> is high-level behavior (injected for the
          model; edit here). <strong>TOOLS.md</strong> documents tools for context. The model can update{' '}
          <strong>SOUL.md</strong>, <strong>MEMORY.md</strong>, <strong>IDENTITY.md</strong>, <strong>USER.md</strong>, and{' '}
          <strong>HEARTBEAT.md</strong> via tools when appropriate (dashboard &quot;Give me a soul&quot; runs{' '}
          <strong>BOOTSTRAP.md</strong>). Heartbeat <em>maintenance</em> can merge daily logs into MEMORY. Voice turns use
          Gemini on the audio; add explicit daily-log lines with the <code>daily_log_append</code> tool if you want raw
          notes on disk. Save model and heartbeat toggles with the form{' '}
          <strong>Save</strong> button; save markdown with <strong>Save persona file</strong>.
        </p>
        <div className="persona-tab-row" role="tablist">
          {[
            { id: 'agents', label: 'AGENTS' },
            { id: 'soul', label: 'SOUL' },
            { id: 'identity', label: 'IDENTITY' },
            { id: 'user', label: 'USER' },
            { id: 'tools', label: 'TOOLS' },
            { id: 'memory', label: 'MEMORY' },
            { id: 'heartbeat', label: 'HEARTBEAT' },
          ].map(({ id, label }) => (
            <button
              key={id}
              type="button"
              className={`persona-tab-btn ${personaTab === id ? 'active' : ''}`}
              onClick={() => setPersonaTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <textarea
          id="personaEditor"
          aria-label={`Edit ${personaTab}.md`}
          value={personaDrafts[personaTab] ?? ''}
          onChange={(e) =>
            setPersonaDrafts((prev) => ({ ...prev, [personaTab]: e.target.value }))
          }
          className="holo-textarea"
          rows="12"
        />
        <div className="persona-file-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleSavePersonaFile}
            disabled={personaSaving}
          >
            {personaSaving ? 'Saving…' : 'Save persona file'}
          </button>
        </div>
        {personaMessage && <p className="help-text persona-inline-msg">{personaMessage}</p>}
        {personaStatus?.heartbeat?.last_run_utc && (
          <p className="help-text">
            Last heartbeat (UTC): {personaStatus.heartbeat.last_run_utc}
            {personaStatus.heartbeat.last_memory_updated ? ' · MEMORY updated' : ''}
          </p>
        )}
        <div className="form-group" style={{ marginTop: '1rem' }}>
          <label htmlFor="heartbeatInterval">Heartbeat interval (minutes)</label>
          <input
            id="heartbeatInterval"
            type="number"
            min={5}
            max={720}
            className="holo-input"
            style={{ maxWidth: '120px' }}
            value={heartbeatIntervalMin}
            onChange={(e) => setHeartbeatIntervalMin(Number(e.target.value) || 30)}
          />
        </div>
        <label className="persona-check">
          <input
            type="checkbox"
            checked={heartbeatEnabled}
            onChange={(e) => setHeartbeatEnabled(e.target.checked)}
          />
          Enable heartbeat maintenance
        </label>
      </div>

      <div className="form-actions">
        <button
          type="button"
          className="btn btn-reset-defaults"
          onClick={handleResetToDefaults}
          disabled={isSaving}
        >
          Reset to defaults
        </button>
        <div className="form-actions-trailing">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setAppMode('dashboard')}
            disabled={isSaving}
          >
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {saveStatus === 'success' && (
        <div className="status-message success slide-enter">Pixel settings saved.</div>
      )}
      {saveStatus === 'error' && (
        <div className="status-message error slide-enter">Something went wrong. Check connection to the hub.</div>
      )}
    </form>
  );

  if (embedded) {
    return (
      <div className="settings-panel-inner fade-in">
        {header}
        {formBody}
      </div>
    );
  }

  return (
    <div className="settings-container fade-in">
      {header}
      {formBody}
    </div>
  );
};

export default BotSettings;
