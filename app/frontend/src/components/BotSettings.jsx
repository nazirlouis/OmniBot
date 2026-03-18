import React, { useState, useEffect } from 'react';
import './BotSettings.css';
import { getBotSettings, updateBotSettings } from './setupService';

const BotSettings = ({ setAppMode }) => {
  const [deviceId, setDeviceId] = useState('Pixel_Default'); // In a real app we'd pass the actual connected bot's MAC address
  const [model, setModel] = useState('gemini-3.1-flash-lite-preview');
  const [systemInstruction, setSystemInstruction] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null); // 'success' | 'error' | null

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await getBotSettings(deviceId);
        setModel(data.model);
        setSystemInstruction(data.system_instruction);
      } catch (err) {
        console.error("Failed to fetch settings", err);
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
      await updateBotSettings(deviceId, {
        model,
        system_instruction: systemInstruction
      });
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000); // Clear success message after 3s
    } catch (err) {
      console.error("Failed to save settings", err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="settings-container">
        <div className="scanning-container">
          <div className="pulse-ring mx-auto"></div>
          <p>Loading AI Core Settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-container fade-in">
      <div className="settings-header">
        <h2>Optics & Intelligence Configuration</h2>
        <div className="bot-identifier">
          <span className="id-label">TARGET:</span> {deviceId}
        </div>
      </div>

      <form className="settings-form" onSubmit={handleSave}>
        
        <div className="form-group">
          <label htmlFor="modelSelect">Generative Model Core</label>
          <div className="select-wrapper">
            <select 
              id="modelSelect"
              value={model} 
              onChange={(e) => setModel(e.target.value)}
              className="holo-select"
            >
              <option value="gemini-3.1-flash-lite-preview">Gemini 3.1 Flash Lite (Default/Fast)</option>
              <option value="gemini-3-flash-preview">Gemini 3 Flash</option>
              <option value="gemini-3.1-pro-preview">Gemini 3.1 Pro (Advanced Reasoning)</option>
            </select>
          </div>
          <p className="help-text">Select the neural engine powering this bot's responses.</p>
        </div>

        <div className="form-group">
          <label htmlFor="sysInstruction">System Instructions (Personality & Context)</label>
          <textarea 
            id="sysInstruction"
            value={systemInstruction}
            onChange={(e) => setSystemInstruction(e.target.value)}
            className="holo-textarea"
            rows="6"
          />
          <p className="help-text">Give the bot specific context on how to behave, what it sees, and how to respond.</p>
        </div>

        <div className="form-actions">
          <button 
            type="button" 
            className="btn btn-secondary" 
            onClick={() => setAppMode('dashboard')}
          >
            Cancel
          </button>
          <button 
            type="submit" 
            className="btn btn-primary"
            disabled={isSaving}
          >
            {isSaving ? "Syncing..." : "Sync to Core"}
          </button>
        </div>

        {saveStatus === 'success' && (
          <div className="status-message success slide-enter">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
            Settings saved and synced successfully.
          </div>
        )}
        
        {saveStatus === 'error' && (
          <div className="status-message error slide-enter">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            Failed to sync settings. Check core connection.
          </div>
        )}
      </form>
    </div>
  );
};

export default BotSettings;
