import React from 'react';
import './SetupFlow.css';
import SetupDevice from './SetupFlow/SetupDevice';
import SetupWifi from './SetupFlow/SetupWifi';
import SetupPassword from './SetupFlow/SetupPassword';
import SetupWaiting from './SetupFlow/SetupWaiting';
import SetupSoulWalkthrough from './SetupFlow/SetupSoulWalkthrough';
import SetupWalkthroughComplete from './SetupFlow/SetupWalkthroughComplete';

const stepHeadline = (step) => {
  switch (step) {
    case 'waiting':
      return 'Connecting to hub';
    case 'soul':
      return 'First-run soul';
    case 'complete':
      return 'Almost done';
    default:
      return 'Add New Bot';
  }
};

const stepSubtitle = (step) => {
  switch (step) {
    case 'waiting':
      return 'Hang tight while your hardware reaches this OmniBot hub.';
    case 'soul':
      return 'Help the AI learn who your bot is using BOOTSTRAP.md.';
    case 'complete':
      return 'Optional: open settings or jump to the Intelligence Feed.';
    default:
      return 'Connect your hardware to the OmniBot Hub.';
  }
};

const SetupOrchestrator = ({ state, setters, actions }) => {
  const { setupStep } = state;
  const { exitSetupToDashboard } = actions;

  return (
    <div className="setup-container">
      <div
        className={`setup-card ${setupStep === 'soul' ? 'setup-card--soul-panel' : ''}`}
      >
        <div className="setup-header">
          <h1>{stepHeadline(setupStep)}</h1>
          <p className="setup-subtitle">{stepSubtitle(setupStep)}</p>
        </div>

        <div className="setup-body">
          {setupStep === 'device' && (
            <SetupDevice state={state} setters={setters} actions={actions} />
          )}

          {setupStep === 'wifi' && <SetupWifi state={state} setters={setters} actions={actions} />}

          {setupStep === 'password' && (
            <SetupPassword state={state} setters={setters} actions={actions} />
          )}

          {setupStep === 'waiting' && <SetupWaiting state={state} setters={setters} />}

          {setupStep === 'soul' && <SetupSoulWalkthrough state={state} actions={actions} />}

          {setupStep === 'complete' && <SetupWalkthroughComplete actions={actions} />}
        </div>

        <div className="setup-footer">
          <button className="text-btn" type="button" onClick={exitSetupToDashboard}>
            Cancel & Return
          </button>
        </div>
      </div>
    </div>
  );
};

export default SetupOrchestrator;
