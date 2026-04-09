import React from 'react';

const SetupWalkthroughComplete = ({ actions }) => {
  const { onConfigureSettings, onGoToFeed } = actions;

  return (
    <div className="setup-section slide-enter">
      <h3 className="section-title">Setup checkpoint</h3>
      <p className="help-text">
        Your bot is on the hub. You can tune voice, vision, heartbeat, and persona files whenever you like.
      </p>
      <div className="button-group mt-4 setup-complete-actions">
        <button type="button" className="btn btn-primary flex-2" onClick={onConfigureSettings}>
          Configure bot settings
        </button>
        <button type="button" className="btn btn-secondary flex-1" onClick={onGoToFeed}>
          Go to feed
        </button>
      </div>
    </div>
  );
};

export default SetupWalkthroughComplete;
