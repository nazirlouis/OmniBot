import React from 'react';
import BotSettings from './BotSettings';
import HubSettings from './HubSettings';
import './SettingsShell.css';

const SettingsShell = ({ setAppMode, tab, setTab }) => {
  return (
    <div className="settings-container fade-in">
      <div className="settings-header">
        <h2>Settings</h2>
        <button type="button" className="btn btn-secondary settings-shell-back" onClick={() => setAppMode('dashboard')}>
          Back
        </button>
      </div>

      <div className="settings-tabs" role="tablist" aria-label="Settings sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'pixel'}
          className={`settings-tab ${tab === 'pixel' ? 'active' : ''}`}
          onClick={() => setTab('pixel')}
        >
          Pixel bot
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'hub'}
          className={`settings-tab ${tab === 'hub' ? 'active' : ''}`}
          onClick={() => setTab('hub')}
        >
          Hub / application
        </button>
      </div>

      {tab === 'pixel' && <BotSettings setAppMode={setAppMode} embedded />}
      {tab === 'hub' && <HubSettings />}
    </div>
  );
};

export default SettingsShell;
