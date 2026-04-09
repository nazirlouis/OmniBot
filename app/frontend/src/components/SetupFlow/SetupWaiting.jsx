import React from 'react';

const SetupWaiting = ({ state, setters }) => {
  const { selectedDevice } = state;
  const { setSetupStep } = setters;
  const name = selectedDevice?.name || 'your bot';

  return (
    <div className="setup-section fade-in">
      <h3 className="section-title">Waiting for {name}</h3>
      <div className="setup-waiting-body">
        <div className="setup-waiting-spinner" aria-hidden="true">
          <span className="spinner setup-waiting-spinner-icon" />
        </div>
        <p className="help-text setup-waiting-lead">
          Wi‑Fi credentials were sent. When Pixel joins this hub, the next step starts automatically.
        </p>
        <p className="help-text">
          On the device, confirm it shows as connected. This usually takes under a minute.
        </p>
      </div>
      <div className="button-group mt-4">
        <button type="button" onClick={() => setSetupStep('password')} className="btn btn-secondary flex-1">
          Back
        </button>
      </div>
    </div>
  );
};

export default SetupWaiting;
