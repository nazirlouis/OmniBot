import React, { useState, useEffect, useMemo } from 'react';
import './BotSettings.css';
import {
  getHubSettings,
  postHubSettings,
  getHubAppSettings,
  postHubAppSettings,
} from './setupService';
import { getCountryOptions, normalizeSavedCountryCode } from './countrySelectOptions';
import { resolveMapsJsApiKey, loadMapsPlacesForContextual } from '../mapsContextualLoader';

const TIMEZONE_OPTIONS = [
  { value: 'EST5EDT,M3.2.0/2,M11.1.0/2', label: 'US Eastern (EST/EDT)' },
  { value: 'CST6CDT,M3.2.0/2,M11.1.0/2', label: 'US Central (CST/CDT)' },
  { value: 'MST7MDT,M3.2.0/2,M11.1.0/2', label: 'US Mountain (MST/MDT)' },
  { value: 'PST8PDT,M3.2.0/2,M11.1.0/2', label: 'US Pacific (PST/PDT)' },
  { value: 'UTC0', label: 'UTC' },
];

const HubSettings = () => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState(null);
  const [saveStatus, setSaveStatus] = useState(null);
  const [form, setForm] = useState({
    gemini_api_key: '',
    google_maps_js_api_key: '',
    google_maps_static_api_key: '',
    nominatim_user_agent: '',
  });

  const [locSaving, setLocSaving] = useState(false);
  const [locSaveStatus, setLocSaveStatus] = useState(null);
  const [mapsGeocodeMessage, setMapsGeocodeMessage] = useState(null);
  const [locForm, setLocForm] = useState({
    timezone_rule: TIMEZONE_OPTIONS[0].value,
    maps_grounding_enabled: false,
    maps_street: '',
    maps_state: '',
    maps_postal_code: '',
    maps_country: '',
    maps_latitude: null,
    maps_longitude: null,
    maps_display_name: '',
  });

  const countryOptions = useMemo(() => getCountryOptions(), []);
  const hasTimezoneOption = TIMEZONE_OPTIONS.some((tz) => tz.value === locForm.timezone_rule);

  const load = async () => {
    try {
      const [v, app] = await Promise.all([getHubSettings(), getHubAppSettings()]);
      setView(v);
      setForm((f) => ({
        ...f,
        nominatim_user_agent: v.nominatim_user_agent || '',
      }));
      setLocForm({
        timezone_rule: app.timezone_rule || TIMEZONE_OPTIONS[0].value,
        maps_grounding_enabled: Boolean(app.maps_grounding_enabled),
        maps_street: app.maps_street || '',
        maps_state: app.maps_state || '',
        maps_postal_code: app.maps_postal_code || '',
        maps_country: normalizeSavedCountryCode(app.maps_country) || '',
        maps_latitude: app.maps_latitude ?? null,
        maps_longitude: app.maps_longitude ?? null,
        maps_display_name: app.maps_display_name || '',
      });
      setMapsGeocodeMessage(null);
    } catch (e) {
      console.error(e);
      setSaveStatus('error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    let autocomplete = null;
    const initAutocomplete = async () => {
      try {
        const apiKey = await resolveMapsJsApiKey();
        if (!apiKey) return;
        await loadMapsPlacesForContextual(apiKey);
        const inputElem = document.getElementById('hubMapsStreet');
        if (!inputElem) return;
        if (window.google?.maps?.places) {
          autocomplete = new window.google.maps.places.Autocomplete(inputElem, {
            fields: ['formatted_address', 'name'],
            types: ['address'],
          });
          autocomplete.addListener('place_changed', () => {
            const place = autocomplete.getPlace();
            if (place.formatted_address) {
              setLocForm((f) => ({
                ...f,
                maps_street: place.formatted_address,
                maps_state: '',
                maps_postal_code: '',
                maps_country: '',
              }));
            } else if (place.name) {
              setLocForm((f) => ({ ...f, maps_street: place.name }));
            }
          });
        }
      } catch (err) {
        console.warn('Maps Autocomplete unavailable:', err);
      }
    };
    if (!loading) {
      const t = setTimeout(initAutocomplete, 100);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [loading]);

  const clearKey = async (field) => {
    setSaving(true);
    setSaveStatus(null);
    try {
      const res = await postHubSettings({ [field]: '' });
      setView(res.settings);
      setForm((f) => ({ ...f, [field]: '' }));
      setSaveStatus('success');
    } catch (err) {
      console.error(err);
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitKeys = async (e) => {
    e.preventDefault();
    setSaving(true);
    setSaveStatus(null);
    try {
      const payload = {};
      if (form.gemini_api_key.trim()) payload.gemini_api_key = form.gemini_api_key.trim();
      if (form.google_maps_js_api_key.trim()) {
        payload.google_maps_js_api_key = form.google_maps_js_api_key.trim();
      }
      if (form.google_maps_static_api_key.trim()) {
        payload.google_maps_static_api_key = form.google_maps_static_api_key.trim();
      }
      if (!view?.nominatim_user_agent_from_env) {
        const nextN = form.nominatim_user_agent.trim();
        const prevN = (view?.nominatim_user_agent || '').trim();
        if (nextN !== prevN) {
          payload.nominatim_user_agent = nextN;
        }
      }
      if (Object.keys(payload).length === 0) {
        setSaving(false);
        setSaveStatus(null);
        return;
      }
      const res = await postHubSettings(payload);
      setView(res.settings);
      setForm((f) => ({
        ...f,
        gemini_api_key: '',
        google_maps_js_api_key: '',
        google_maps_static_api_key: '',
      }));
      setSaveStatus('success');
    } catch (err) {
      console.error(err);
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitLocation = async (e) => {
    e.preventDefault();
    setLocSaving(true);
    setLocSaveStatus(null);
    setMapsGeocodeMessage(null);
    const streetDom = document.getElementById('hubMapsStreet')?.value;
    const maps_street = (streetDom || locForm.maps_street || '').trim();
    try {
      const res = await postHubAppSettings({
        timezone_rule: locForm.timezone_rule,
        maps_grounding_enabled: locForm.maps_grounding_enabled,
        maps_street,
        maps_state: locForm.maps_state.trim(),
        maps_postal_code: locForm.maps_postal_code.trim(),
        maps_country: locForm.maps_country.trim(),
      });
      const saved = res.settings || {};
      setLocForm({
        timezone_rule: saved.timezone_rule || TIMEZONE_OPTIONS[0].value,
        maps_grounding_enabled: Boolean(saved.maps_grounding_enabled),
        maps_street: saved.maps_street || '',
        maps_state: saved.maps_state || '',
        maps_postal_code: saved.maps_postal_code || '',
        maps_country: normalizeSavedCountryCode(saved.maps_country) || '',
        maps_latitude: saved.maps_latitude ?? null,
        maps_longitude: saved.maps_longitude ?? null,
        maps_display_name: saved.maps_display_name || '',
      });
      const geo = res.maps_geocode;
      if (geo && geo.ok === false && geo.error) {
        setMapsGeocodeMessage(geo.error);
      }
      setLocSaveStatus('success');
    } catch (err) {
      console.error(err);
      setLocSaveStatus('error');
    } finally {
      setLocSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="settings-panel-inner">
        <div className="scanning-container">
          <div className="pulse-ring mx-auto"></div>
          <p>Loading hub settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-panel-inner fade-in">
      <div className="settings-subheader">
        <h2>Hub / application</h2>
      </div>

      <p className="help-text" style={{ marginTop: 0 }}>
        API keys live in <code>hub_secrets.json</code>. Clock and Maps location live in <code>hub_app_settings.json</code>.
        {view?.data_dir && (
          <>
            {' '}
            Data dir:{' '}
            <code className="bot-identifier" style={{ display: 'inline', padding: '0.15rem 0.4rem' }}>
              {view.data_dir}
            </code>
          </>
        )}
      </p>

      <h3 className="hub-section-title">API keys</h3>
      {view?.nominatim_user_agent_from_env && (
        <p className="help-text">Nominatim user agent is set via <code>NOMINATIM_USER_AGENT</code> (overrides file).</p>
      )}

      <form className="settings-form" onSubmit={handleSubmitKeys}>
        <div className="form-group">
          <label htmlFor="hubGeminiKey">Gemini API key</label>
          {view?.gemini_api_key_configured && <p className="help-text">Stored: {view.gemini_api_key_masked}</p>}
          <input
            id="hubGeminiKey"
            type="password"
            autoComplete="off"
            className="holo-input"
            value={form.gemini_api_key}
            onChange={(e) => setForm((f) => ({ ...f, gemini_api_key: e.target.value }))}
            placeholder={view?.gemini_api_key_configured ? 'Enter new key to replace' : 'Enter API key'}
          />
          {view?.gemini_api_key_configured && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ alignSelf: 'flex-start', marginTop: '0.35rem' }}
              disabled={saving}
              onClick={() => clearKey('gemini_api_key')}
            >
              Remove stored Gemini key
            </button>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsJs">Google Maps JavaScript API key</label>
          {view?.google_maps_js_api_key_configured && (
            <p className="help-text">Stored: {view.google_maps_js_api_key_masked}</p>
          )}
          <input
            id="hubMapsJs"
            type="password"
            autoComplete="off"
            className="holo-input"
            value={form.google_maps_js_api_key}
            onChange={(e) => setForm((f) => ({ ...f, google_maps_js_api_key: e.target.value }))}
            placeholder="Maps JS (browser widget + screenshots)"
          />
          {view?.google_maps_js_api_key_configured && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ alignSelf: 'flex-start', marginTop: '0.35rem' }}
              disabled={saving}
              onClick={() => clearKey('google_maps_js_api_key')}
            >
              Remove stored JS key
            </button>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsStatic">Google Maps Static API key (optional)</label>
          {view?.google_maps_static_api_key_configured && (
            <p className="help-text">Stored: {view.google_maps_static_api_key_masked}</p>
          )}
          <input
            id="hubMapsStatic"
            type="password"
            autoComplete="off"
            className="holo-input"
            value={form.google_maps_static_api_key}
            onChange={(e) => setForm((f) => ({ ...f, google_maps_static_api_key: e.target.value }))}
            placeholder="Optional; static map tiles / geocoding fallback"
          />
          {view?.google_maps_static_api_key_configured && (
            <button
              type="button"
              className="btn btn-secondary"
              style={{ alignSelf: 'flex-start', marginTop: '0.35rem' }}
              disabled={saving}
              onClick={() => clearKey('google_maps_static_api_key')}
            >
              Remove stored Static key
            </button>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="hubNominatim">Nominatim user agent</label>
          <input
            id="hubNominatim"
            type="text"
            className="holo-input"
            value={form.nominatim_user_agent}
            onChange={(e) => setForm((f) => ({ ...f, nominatim_user_agent: e.target.value }))}
            placeholder="App name + contact (OSM policy)"
            disabled={Boolean(view?.nominatim_user_agent_from_env)}
          />
        </div>

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save API keys'}
            </button>
          </div>
        </div>
        {saveStatus === 'success' && <div className="status-message success slide-enter">API keys saved.</div>}
        {saveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save API keys.</div>
        )}
      </form>

      <h3 className="hub-section-title">Clock &amp; Maps location</h3>
      <p className="help-text">
        Used for Pixel clock sync and Gemini Maps grounding (hub-wide, not per Pixel tab). Saving clears in-memory chat
        history on the hub.
      </p>

      <form className="settings-form" onSubmit={handleSubmitLocation}>
        <div className="form-group">
          <label htmlFor="hubTimezoneSelect">Timezone (clock sync)</label>
          <div className="select-wrapper">
            <select
              id="hubTimezoneSelect"
              value={locForm.timezone_rule}
              onChange={(e) => setLocForm((f) => ({ ...f, timezone_rule: e.target.value }))}
              className="holo-select"
            >
              {!hasTimezoneOption && <option value={locForm.timezone_rule}>Custom ({locForm.timezone_rule})</option>}
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz.value} value={tz.value}>
                  {tz.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsGrounding">Google Maps grounding (Gemini)</label>
          <div className="select-wrapper">
            <select
              id="hubMapsGrounding"
              value={locForm.maps_grounding_enabled ? 'on' : 'off'}
              onChange={(e) => setLocForm((f) => ({ ...f, maps_grounding_enabled: e.target.value === 'on' }))}
              className="holo-select"
            >
              <option value="off">Off</option>
              <option value="on">On</option>
            </select>
          </div>
          <p className="help-text">
            When on, local &quot;near me&quot; queries use Maps grounding. Gemini cannot mix Maps and Search in one request.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsStreet">Address</label>
          <input
            id="hubMapsStreet"
            type="text"
            className="holo-input"
            value={locForm.maps_street}
            onChange={(e) => setLocForm((f) => ({ ...f, maps_street: e.target.value }))}
            placeholder="Street address or postal + region"
            autoComplete="off"
          />
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsState">State / region (optional)</label>
          <input
            id="hubMapsState"
            type="text"
            className="holo-input"
            value={locForm.maps_state}
            onChange={(e) => setLocForm((f) => ({ ...f, maps_state: e.target.value }))}
          />
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsPostal">Postal / ZIP (optional)</label>
          <input
            id="hubMapsPostal"
            type="text"
            className="holo-input"
            value={locForm.maps_postal_code}
            onChange={(e) => setLocForm((f) => ({ ...f, maps_postal_code: e.target.value }))}
          />
        </div>

        <div className="form-group">
          <label htmlFor="hubMapsCountry">Country</label>
          <div className="select-wrapper">
            <select
              id="hubMapsCountry"
              value={locForm.maps_country}
              onChange={(e) => setLocForm((f) => ({ ...f, maps_country: e.target.value }))}
              className="holo-select"
            >
              <option value="">Select country</option>
              {countryOptions.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {locForm.maps_latitude != null && locForm.maps_longitude != null && (
          <div className="form-group maps-resolved-block">
            <p className="help-text maps-resolved-title">Resolved for Maps tool</p>
            <p className="maps-coords">
              {Number(locForm.maps_latitude).toFixed(5)}, {Number(locForm.maps_longitude).toFixed(5)}
            </p>
            {locForm.maps_display_name && <p className="maps-display-name">{locForm.maps_display_name}</p>}
          </div>
        )}

        {mapsGeocodeMessage && (
          <div className="status-message error slide-enter maps-geocode-error">
            Location lookup failed: {mapsGeocodeMessage}
          </div>
        )}

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={locSaving}>
              {locSaving ? 'Saving...' : 'Save clock &amp; location'}
            </button>
          </div>
        </div>
        {locSaveStatus === 'success' && (
          <div className="status-message success slide-enter">Clock and location saved.</div>
        )}
        {locSaveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save clock and location.</div>
        )}
      </form>
    </div>
  );
};

export default HubSettings;
