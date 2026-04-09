import React from 'react';

const SetupSoulWalkthrough = ({ state, actions }) => {
  const { walkthroughDeviceId, soulWalkthroughStarted } = state;
  const { onStartSoulWalkthrough, onSkipSoulWalkthrough } = actions;
  const disabled = !walkthroughDeviceId;

  return (
    <div className="setup-section fade-in setup-soul-intro">
      <h3 className="section-title">Meet your bot&apos;s mind</h3>
      <p className="help-text setup-soul-copy">
        Next, the hub runs a <strong>first-run ritual</strong>: the AI reads <code>BOOTSTRAP.md</code> and works
        with you to fill in <strong>SOUL</strong>, <strong>IDENTITY</strong>, and <strong>USER</strong> so your
        bot knows who it is and who you are. Chat appears in the feed beside this card.
      </p>
      <p className="help-text">
        When you&apos;re happy with the ritual, the model calls <strong>bootstrap_complete</strong> and this
        walkthrough continues automatically.
      </p>
      <div className="button-group mt-4 setup-soul-actions">
        <button
          type="button"
          className="btn btn-primary btn-block"
          disabled={disabled || soulWalkthroughStarted}
          onClick={onStartSoulWalkthrough}
        >
          {soulWalkthroughStarted ? (
            <>
              <span className="spinner" /> Ritual started — watch the feed
            </>
          ) : (
            'Begin soul ritual'
          )}
        </button>
        <button type="button" className="btn btn-secondary btn-block" onClick={onSkipSoulWalkthrough}>
          Skip for now
        </button>
      </div>
    </div>
  );
};

export default SetupSoulWalkthrough;
