import React from 'react';

const SetupWifi = ({ state, setters }) => {
  const { wifiNetworks, wifiScanMessage, isScanningWifi } = state;
  const { setSsid, setPassword, setSetupStep } = setters;

  return (
    <div className="setup-section slide-enter">
      <h3 className="section-title">Select Wi-Fi Network</h3>
      {wifiScanMessage && (
        <p className="help-text" style={{ marginBottom: '1rem' }}>
          {wifiScanMessage}
        </p>
      )}
      {isScanningWifi ? (
        <div className="scanning-container">
          <div className="pulse-ring mx-auto"></div>
          <p>Scanning surroundings...</p>
        </div>
      ) : (
        <div className="device-list wifi-list">
          {wifiNetworks.map((net, i) => (
            <div 
              key={i} 
              className="device-item"
              onClick={() => {
                setSsid(net);
                setPassword('');
                setSetupStep('password');
              }}
            >
              <div className="device-icon wifi-icon">📶</div>
              <div className="device-info">
                <span className="device-name">{net}</span>
              </div>
            </div>
          ))}
          {wifiNetworks.length === 0 && (
            <div className="empty-state">No networks found.</div>
          )}
          
          <div 
            className="device-item manual-network"
            onClick={() => { 
              setSsid(''); 
              setPassword(''); 
              setSetupStep('password'); 
            }}
          >
            <div className="device-icon">➕</div>
            <div className="device-info">
              <span className="device-name">Join Other Network...</span>
            </div>
          </div>
        </div>
      )}

      <button 
        className="btn btn-secondary btn-block mt-4"
        onClick={() => setSetupStep('device')}
      >
        Back to Bot Selection
      </button>
    </div>
  );
};

export default SetupWifi;
