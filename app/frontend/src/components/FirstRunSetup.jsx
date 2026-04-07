import { useState } from 'react';
import { postHubSettings, postHubAppSettings } from './setupService';
import './FirstRunSetup.css';
import './BotSettings.css';
import './SetupFlow.css';

const AI_STUDIO_URL = 'https://aistudio.google.com/apikey';

const TIMEZONE_OPTIONS = [
  { value: 'EST5EDT,M3.2.0/2,M11.1.0/2', label: 'US Eastern (EST/EDT)' },
  { value: 'CST6CDT,M3.2.0/2,M11.1.0/2', label: 'US Central (CST/CDT)' },
  { value: 'MST7MDT,M3.2.0/2,M11.1.0/2', label: 'US Mountain (MST/MDT)' },
  { value: 'PST8PDT,M3.2.0/2,M11.1.0/2', label: 'US Pacific (PST/PDT)' },
  { value: 'UTC0', label: 'UTC' },
];

const STEP_LABELS = ['Gemini', 'Clock'];

export default function FirstRunSetup({ onConfigured }) {
  const [step, setStep] = useState(0);
  const [geminiSaved, setGeminiSaved] = useState(false);
  const [geminiKey, setGeminiKey] = useState('');
  const [timezoneRule, setTimezoneRule] = useState(TIMEZONE_OPTIONS[0].value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const goNextFromGemini = async (e) => {
    e.preventDefault();
    const trimmed = geminiKey.trim();
    if (!trimmed && !geminiSaved) {
      setError('Paste your Gemini API key to continue.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (trimmed) {
        await postHubSettings({ gemini_api_key: trimmed });
        setGeminiSaved(true);
        setGeminiKey('');
      }
      setStep(1);
    } catch (err) {
      setError(err.message || 'Could not save. Is the backend running?');
    } finally {
      setSaving(false);
    }
  };

  const finishSetup = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await postHubAppSettings({ timezone_rule: timezoneRule });
      onConfigured();
    } catch (err) {
      setError(err.message || 'Could not save clock settings.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="first-run">
      <div className={`first-run-card${step > 0 ? ' first-run-card--wide' : ''}`}>
        <p className="first-run-steps" aria-live="polite">
          Step {step + 1} of {STEP_LABELS.length}: {STEP_LABELS[step]}
        </p>
        <div className="first-run-step-dots" aria-hidden>
          {STEP_LABELS.map((label, i) => (
            <span key={label} className={`first-run-dot${i === step ? ' first-run-dot--active' : ''}`} />
          ))}
        </div>

        {step === 0 && (
          <>
            <h1 className="first-run-title">Welcome to OmniBot</h1>
            <p className="first-run-lead">
              Add your <strong>Google Gemini API key</strong>. It is stored on this machine in{' '}
              <code className="first-run-code">hub_secrets.json</code>.
            </p>
            <p className="first-run-hint">
              Get a key from{' '}
              <a href={AI_STUDIO_URL} target="_blank" rel="noreferrer">
                Google AI Studio
              </a>
              .
            </p>
            <form className="first-run-form" onSubmit={goNextFromGemini}>
              <label className="first-run-label" htmlFor="first-run-gemini">
                Gemini API key
              </label>
              <input
                id="first-run-gemini"
                type="password"
                autoComplete="off"
                className="first-run-input"
                value={geminiKey}
                onChange={(e) => setGeminiKey(e.target.value)}
                placeholder={geminiSaved ? 'Enter a new key to replace the saved one' : 'Paste your API key'}
                disabled={saving}
              />
              {geminiSaved && (
                <p className="first-run-hint first-run-hint--tight">
                  A key is already saved. Leave blank and click Next to continue, or enter a new key to replace it.
                </p>
              )}
              {error && <p className="first-run-error">{error}</p>}
              <button type="submit" className="first-run-submit" disabled={saving}>
                {saving ? 'Saving…' : 'Next'}
              </button>
            </form>
          </>
        )}

        {step === 1 && (
          <>
            <h1 className="first-run-title">Clock</h1>
            <p className="first-run-lead">Choose your hub timezone for Pixel clock sync.</p>
            <form className="first-run-form first-run-form--stack" onSubmit={finishSetup}>
              <div className="form-group">
                <label htmlFor="first-run-tz">Timezone (clock sync)</label>
                <div className="select-wrapper">
                  <select
                    id="first-run-tz"
                    value={timezoneRule}
                    onChange={(e) => setTimezoneRule(e.target.value)}
                    className="holo-select"
                    disabled={saving}
                  >
                    {TIMEZONE_OPTIONS.map((tz) => (
                      <option key={tz.value} value={tz.value}>
                        {tz.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {error && <p className="first-run-error">{error}</p>}
              <div className="first-run-actions">
                <button type="button" className="btn btn-secondary" disabled={saving} onClick={onConfigured}>
                  Skip
                </button>
                <button type="submit" className="first-run-submit first-run-submit--inline" disabled={saving}>
                  {saving ? 'Saving…' : 'Finish'}
                </button>
              </div>
            </form>
            <div className="first-run-nav">
              <button type="button" className="text-btn first-run-back" disabled={saving} onClick={() => setStep(0)}>
                ← Back
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
