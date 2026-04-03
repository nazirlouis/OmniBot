import { useMemo, useState } from 'react';
import { postHubSettings, postHubAppSettings } from './setupService';
import { getCountryOptions } from './countrySelectOptions';
import './FirstRunSetup.css';
import './BotSettings.css';
import './SetupFlow.css';

const AI_STUDIO_URL = 'https://aistudio.google.com/apikey';
const MAPS_PLATFORM_URL = 'https://developers.google.com/maps/documentation/javascript/get-api-key';

const TIMEZONE_OPTIONS = [
  { value: 'EST5EDT,M3.2.0/2,M11.1.0/2', label: 'US Eastern (EST/EDT)' },
  { value: 'CST6CDT,M3.2.0/2,M11.1.0/2', label: 'US Central (CST/CDT)' },
  { value: 'MST7MDT,M3.2.0/2,M11.1.0/2', label: 'US Mountain (MST/MDT)' },
  { value: 'PST8PDT,M3.2.0/2,M11.1.0/2', label: 'US Pacific (PST/PDT)' },
  { value: 'UTC0', label: 'UTC' },
];

const STEP_LABELS = ['Gemini', 'Maps key', 'Clock & location'];

/**
 * First-time hub setup: Gemini (required), optional Google Maps API key, then clock / Maps address.
 */
export default function FirstRunSetup({ onConfigured }) {
  const [step, setStep] = useState(0);
  const [geminiSaved, setGeminiSaved] = useState(false);

  const [geminiKey, setGeminiKey] = useState('');
  const [mapsKey, setMapsKey] = useState('');

  const [locForm, setLocForm] = useState({
    timezone_rule: TIMEZONE_OPTIONS[0].value,
    maps_grounding_enabled: false,
    maps_street: '',
    maps_state: '',
    maps_postal_code: '',
    maps_country: '',
  });

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [mapsGeocodeMessage, setMapsGeocodeMessage] = useState(null);

  const countryOptions = useMemo(() => getCountryOptions(), []);
  const hasTimezoneOption = TIMEZONE_OPTIONS.some((tz) => tz.value === locForm.timezone_rule);
  /** From Maps step; if no key was provided, address fields are unavailable. */
  const hasMapsApiKey = useMemo(() => Boolean(mapsKey.trim()), [mapsKey]);

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

  const goNextFromMaps = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload = {};
      if (mapsKey.trim()) payload.google_maps_api_key = mapsKey.trim();
      if (Object.keys(payload).length > 0) {
        await postHubSettings(payload);
      }
      setMapsGeocodeMessage(null);
      setStep(2);
    } catch (err) {
      setError(err.message || 'Could not save Maps keys.');
    } finally {
      setSaving(false);
    }
  };

  const skipMaps = (e) => {
    e.preventDefault();
    setError(null);
    setMapsGeocodeMessage(null);
    setStep(2);
  };

  const finishLocation = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setMapsGeocodeMessage(null);
    try {
      const res = await postHubAppSettings({
        timezone_rule: locForm.timezone_rule,
        maps_grounding_enabled: locForm.maps_grounding_enabled,
        maps_street: hasMapsApiKey ? locForm.maps_street.trim() : '',
        maps_state: hasMapsApiKey ? locForm.maps_state.trim() : '',
        maps_postal_code: hasMapsApiKey ? locForm.maps_postal_code.trim() : '',
        maps_country: hasMapsApiKey ? (locForm.maps_country || '').trim() : '',
      });
      const geo = res.maps_geocode;
      if (geo && geo.ok === false && geo.error) {
        setMapsGeocodeMessage(geo.error);
        setSaving(false);
        return;
      }
      onConfigured();
    } catch (err) {
      setError(err.message || 'Could not save clock and location.');
    } finally {
      setSaving(false);
    }
  };

  const skipLocation = (e) => {
    e.preventDefault();
    onConfigured();
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
              {error && step === 0 && <p className="first-run-error">{error}</p>}
              <button type="submit" className="first-run-submit" disabled={saving}>
                {saving ? 'Saving…' : 'Next'}
              </button>
            </form>
          </>
        )}

        {step === 1 && (
          <>
            <h1 className="first-run-title">Google Maps API key</h1>
            <p className="first-run-lead">
              Optional: one key for the in-dashboard map widget, static map images, and Gemini Maps grounding. You can
              skip and set it later under <strong>Hub settings</strong>.
            </p>
            <p className="first-run-hint">
              Create a key in{' '}
              <a href={MAPS_PLATFORM_URL} target="_blank" rel="noreferrer">
                Google Maps Platform
              </a>{' '}
              (enable the APIs you need: Maps JavaScript, Maps Static, Places, etc.).
            </p>

            <form className="first-run-form first-run-form--stack" onSubmit={goNextFromMaps}>
              <div className="form-group">
                <label htmlFor="first-run-maps-key">Google Maps API key</label>
                <input
                  id="first-run-maps-key"
                  type="password"
                  autoComplete="off"
                  className="holo-input"
                  value={mapsKey}
                  onChange={(e) => setMapsKey(e.target.value)}
                  placeholder="Same key for JS, static maps, and related APIs"
                  disabled={saving}
                />
              </div>
              {error && step === 1 && <p className="first-run-error">{error}</p>}
              <div className="first-run-actions">
                <button type="button" className="btn btn-secondary" disabled={saving} onClick={skipMaps}>
                  Skip
                </button>
                <button type="submit" className="first-run-submit first-run-submit--inline" disabled={saving}>
                  {saving ? 'Saving…' : 'Next'}
                </button>
              </div>
            </form>
          </>
        )}

        {step === 2 && (
          <>
            <h1 className="first-run-title">Clock &amp; Maps location</h1>
            <p className="first-run-lead">
              Set your hub timezone for device clock sync and, if you use Maps grounding, your address so &quot;near
              me&quot; answers match your location.
            </p>

            <form className="first-run-form first-run-form--stack" onSubmit={finishLocation}>
              <div className="form-group">
                <label htmlFor="first-run-tz">Timezone (clock sync)</label>
                <div className="select-wrapper">
                  <select
                    id="first-run-tz"
                    value={locForm.timezone_rule}
                    onChange={(e) => setLocForm((f) => ({ ...f, timezone_rule: e.target.value }))}
                    className="holo-select"
                    disabled={saving}
                  >
                    {!hasTimezoneOption && (
                      <option value={locForm.timezone_rule}>Custom ({locForm.timezone_rule})</option>
                    )}
                    {TIMEZONE_OPTIONS.map((tz) => (
                      <option key={tz.value} value={tz.value}>
                        {tz.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="first-run-grounding">Google Maps grounding (Gemini)</label>
                <div className="select-wrapper">
                  <select
                    id="first-run-grounding"
                    value={locForm.maps_grounding_enabled ? 'on' : 'off'}
                    onChange={(e) =>
                      setLocForm((f) => ({ ...f, maps_grounding_enabled: e.target.value === 'on' }))
                    }
                    className="holo-select"
                    disabled={saving}
                  >
                    <option value="off">Off</option>
                    <option value="on">On</option>
                  </select>
                </div>
                <p className="help-text">
                  When on, local &quot;near me&quot; queries use your address below. Requires geocoding (Nominatim / OSM).
                </p>
              </div>

              <div
                className={
                  hasMapsApiKey ? 'first-run-address-block' : 'first-run-address-block first-run-address-block--disabled'
                }
              >
                <div className="form-group">
                  <label htmlFor="first-run-street">Address</label>
                  {!hasMapsApiKey && (
                    <p className="help-text first-run-address-block__hint">
                      Add a Google Maps API key in the previous step to enter your address.
                    </p>
                  )}
                  <input
                    id="first-run-street"
                    type="text"
                    className="holo-input"
                    value={locForm.maps_street}
                    onChange={(e) => setLocForm((f) => ({ ...f, maps_street: e.target.value }))}
                    placeholder="Street, or postal + region"
                    autoComplete="off"
                    disabled={saving || !hasMapsApiKey}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="first-run-state">State / region (optional)</label>
                  <input
                    id="first-run-state"
                    type="text"
                    className="holo-input"
                    value={locForm.maps_state}
                    onChange={(e) => setLocForm((f) => ({ ...f, maps_state: e.target.value }))}
                    disabled={saving || !hasMapsApiKey}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="first-run-postal">Postal / ZIP (optional)</label>
                  <input
                    id="first-run-postal"
                    type="text"
                    className="holo-input"
                    value={locForm.maps_postal_code}
                    onChange={(e) => setLocForm((f) => ({ ...f, maps_postal_code: e.target.value }))}
                    disabled={saving || !hasMapsApiKey}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="first-run-country">Country</label>
                  <div className="select-wrapper">
                    <select
                      id="first-run-country"
                      value={locForm.maps_country}
                      onChange={(e) => setLocForm((f) => ({ ...f, maps_country: e.target.value }))}
                      className="holo-select"
                      disabled={saving || !hasMapsApiKey}
                    >
                    <option value="">Select country</option>
                    {countryOptions.map((c) => (
                      <option key={c.code} value={c.code}>
                        {c.name}
                      </option>
                    ))}
                    </select>
                  </div>
                </div>
              </div>

              {mapsGeocodeMessage && (
                <div className="status-message error slide-enter first-run-geocode-msg">
                  Location lookup failed: {mapsGeocodeMessage}
                </div>
              )}
              {error && step === 2 && <p className="first-run-error">{error}</p>}

              <div className="first-run-actions">
                <button type="button" className="btn btn-secondary" disabled={saving} onClick={skipLocation}>
                  Skip
                </button>
                <button type="submit" className="first-run-submit first-run-submit--inline" disabled={saving}>
                  {saving ? 'Saving…' : 'Finish'}
                </button>
              </div>
            </form>

            <div className="first-run-nav">
              <button type="button" className="text-btn first-run-back" disabled={saving} onClick={() => setStep(1)}>
                ← Back
              </button>
            </div>
          </>
        )}

        {step === 1 && (
          <div className="first-run-nav">
            <button type="button" className="text-btn first-run-back" disabled={saving} onClick={() => setStep(0)}>
              ← Back
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
