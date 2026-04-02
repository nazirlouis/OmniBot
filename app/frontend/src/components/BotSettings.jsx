import React, { useState, useEffect } from 'react';
import './BotSettings.css';
import './SettingsShell.css';
import { getBotSettings, updateBotSettings, resetBotSettingsToDefault, deleteBot } from './setupService';

const BotSettings = ({ setAppMode, embedded = false }) => {
  const [deviceId] = useState('default_bot');
  const [model, setModel] = useState('gemini-3.1-flash-lite-preview');
  const [systemInstruction, setSystemInstruction] = useState('');
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await getBotSettings(deviceId);
        setModel(data.model);
        setSystemInstruction(data.system_instruction);
        setVisionEnabled(Boolean(data.vision_enabled));
      } catch (err) {
        console.error('Failed to fetch settings', err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchSettings();
  }, [deviceId]);

  const handleSave = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    setSaveStatus(null);
    try {
      const res = await updateBotSettings(deviceId, {
        model,
        system_instruction: systemInstruction,
        vision_enabled: visionEnabled,
      });
      const saved = res.settings || {};
      setModel(saved.model);
      setSystemInstruction(saved.system_instruction);
      setVisionEnabled(Boolean(saved.vision_enabled));
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to save settings', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemoveBot = async () => {
    if (
      !window.confirm(
        `Remove "${deviceId}" from this hub?\n\n` +
          'This deletes saved Pixel settings for this bot, clears chat history in memory, and disconnects the bot if it is online. The hub will reject reconnects until you save Pixel bot settings again (or reset to defaults). Hub-wide settings (API keys, Maps location) are not removed.'
      )
    ) {
      return;
    }
    setIsSaving(true);
    setSaveStatus(null);
    try {
      await deleteBot(deviceId);
      setAppMode('dashboard');
    } catch (err) {
      console.error('Failed to remove bot', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  const handleResetToDefaults = async () => {
    if (
      !window.confirm(
        'Reset Pixel model, system instructions, and vision to defaults? Hub clock and Maps location are not changed.'
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
      setSystemInstruction(saved.system_instruction);
      setVisionEnabled(Boolean(saved.vision_enabled));
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to reset settings', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
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
        <p className="help-text">When on, video frames are sent to Gemini with audio. Stored per bot.</p>
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
        <label htmlFor="sysInstruction">System instructions</label>
        <textarea
          id="sysInstruction"
          value={systemInstruction}
          onChange={(e) => setSystemInstruction(e.target.value)}
          className="holo-textarea"
          rows="6"
        />
        <p className="help-text">Personality and behavior for this bot.</p>
      </div>

      <div className="form-group danger-zone">
        <label>Remove bot from hub</label>
        <p className="help-text">
          Disconnects this bot if connected and deletes its saved model, instructions, and vision preference. The hub will not accept this device until you save Pixel settings here again or use Reset to defaults. Wi‑Fi on the device is unchanged.
        </p>
        <button
          type="button"
          className="btn btn-remove-bot"
          onClick={handleRemoveBot}
          disabled={isSaving}
        >
          Remove bot from hub
        </button>
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
