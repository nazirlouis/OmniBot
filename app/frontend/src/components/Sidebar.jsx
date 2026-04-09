import React from 'react';
import './Sidebar.css';

function formatLastSeen(iso) {
  if (!iso || typeof iso !== 'string') return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return null;
  }
}

const Sidebar = ({
  bots,
  botStatus,
  lastPingById,
  selectedBotId,
  setSelectedBotId,
  appMode,
  setAppMode,
  setSetupStep,
  setSettingsTab,
  settingsTab = 'pixel',
  onEnterSetup,
}) => {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <h1>OmniBot</h1>
      </div>

      <div className="sidebar-section">
        <h3>Bots</h3>

        {bots.length === 0 ? (
          <p className="sidebar-empty-bots">
            No bots yet. Use <strong>Add New Bot</strong> below to provision Pixel, or run the hub on your PC for
            Bluetooth setup. Bots appear here after they connect or you save hub settings for them.
          </p>
        ) : (
          <ul className="sidebar-bot-list">
            {bots.map((b) => {
              const id = b.device_id;
              const status = botStatus[id] || 'offline';
              const ping = lastPingById[id];
              const lastHub = formatLastSeen(b.last_seen);
              const selected = selectedBotId === id;
              return (
                <li key={id}>
                  <div
                    className={`bot-card ${status} ${selected ? 'selected' : ''}`}
                    role="button"
                    tabIndex={0}
                    title={
                      status === 'offline'
                        ? 'Offline — open Pixel settings or reconnect the device'
                        : 'Open Intelligence Feed'
                    }
                    onClick={() => {
                      setSelectedBotId(id);
                      setAppMode('dashboard');
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelectedBotId(id);
                        setAppMode('dashboard');
                      }
                    }}
                  >
                    <div className="bot-card-header">
                      <div className="bot-icon">
                        <svg
                          width="24"
                          height="24"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          className="holo-icon"
                        >
                          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                        </svg>
                      </div>
                      <div className="bot-info">
                        <span className="bot-name">{b.display_name || id}</span>
                        <span className="bot-status-text">
                          {status === 'online' && 'Ready'}
                          {status === 'working' && 'Processing'}
                          {status === 'offline' && 'Offline'}
                        </span>
                      </div>
                    </div>
                    {status !== 'offline' && ping && (
                      <div className="bot-ping">Last seen: {ping}</div>
                    )}
                    {status === 'offline' && lastHub && (
                      <div className="bot-ping bot-ping-muted">Last on hub: {lastHub}</div>
                    )}
                    <button
                      type="button"
                      className="bot-settings-btn"
                      title={`${b.display_name || id} settings`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedBotId(id);
                        setSettingsTab('pixel');
                        setAppMode('settings');
                      }}
                    >
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <circle cx="12" cy="12" r="3" />
                        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
                      </svg>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="sidebar-actions">
        <button
          type="button"
          className={`nav-btn ${appMode === 'settings' && settingsTab === 'hub' ? 'active' : ''}`}
          title="Hub and API keys"
          onClick={() => {
            setSettingsTab('hub');
            setAppMode('settings');
          }}
        >
          <span className="nav-icon">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </span>
          Hub settings
        </button>
        <button
          className={`nav-btn ${appMode === 'setup' ? 'active' : ''}`}
          onClick={() => {
            if (onEnterSetup) onEnterSetup();
            else {
              setAppMode('setup');
              setSetupStep('device');
            }
          }}
        >
          <span className="nav-icon">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="16" />
              <line x1="8" y1="12" x2="16" y2="12" />
            </svg>
          </span>
          Add New Bot
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;
