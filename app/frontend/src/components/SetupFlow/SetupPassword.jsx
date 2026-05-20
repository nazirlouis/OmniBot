import React from 'react';

const SetupPassword = ({ state, setters, actions }) => {
  const { ssid, password, isProvisioning, selectedDevice } = state;
  const { setSsid, setPassword, setSetupStep } = setters;
  const { handleProvision } = actions;

  // Capture once: was the user given a pre-selected SSID, or are they typing it manually?
  // Without this, the SSID input unmounts after the first keystroke (because the original
  // `{!ssid && ...}` hides it as soon as ssid becomes truthy), which makes the form feel
  // like it's auto-advancing.
  const [needsSsidInput] = React.useState(!ssid);

  const onSubmit = (e) => {
    e.preventDefault();
    if (!selectedDevice || !ssid) return;
    handleProvision(e);
  };

  return (
    <form className="setup-section slide-enter" onSubmit={onSubmit}>
      <h3 className="section-title">
        Connecting to {ssid ? <span className="highlight-text">"{ssid}"</span> : 'Network'}
      </h3>

      {needsSsidInput && (
        <div className="input-field">
          <label>Network Name (SSID)</label>
          <input
            type="text"
            value={ssid}
            onChange={e => setSsid(e.target.value)}
            required
            autoFocus
            placeholder="Enter exact network name"
            className="text-input"
          />
        </div>
      )}

      <div className="input-field mt-3">
        <label>Wi-Fi Password</label>
        <input
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          autoFocus={!needsSsidInput}
          placeholder="Leave blank if open network"
          className="text-input"
        />
      </div>
      
      <div className="button-group mt-4">
        <button 
          type="button" 
          onClick={() => setSetupStep('wifi')} 
          className="btn btn-secondary flex-1"
        >
          Back
        </button>
        <button 
          type="submit" 
          disabled={!ssid || isProvisioning} 
          className="btn btn-primary flex-2"
        >
          {isProvisioning ? (
            <><span className="spinner"></span> Provisioning...</>
          ) : (
            'Send to Bot'
          )}
        </button>
      </div>
    </form>
  );
};

export default SetupPassword;
