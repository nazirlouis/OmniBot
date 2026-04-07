import React, { useState, useEffect } from 'react';
import './BotSettings.css';
import {
  getHubSettings,
  postHubSettings,
  getHubAppSettings,
  postHubAppSettings,
} from './setupService';

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
    nominatim_user_agent: '',
  });

  const [locSaving, setLocSaving] = useState(false);
  const [locSaveStatus, setLocSaveStatus] = useState(null);
  const [locForm, setLocForm] = useState({
    timezone_rule: TIMEZONE_OPTIONS[0].value,
  });

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
      });
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
    try {
      const res = await postHubAppSettings({
        timezone_rule: locForm.timezone_rule,
      });
      const saved = res.settings || {};
      setLocForm({
        timezone_rule: saved.timezone_rule || TIMEZONE_OPTIONS[0].value,
      });
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
        API keys live in <code>hub_secrets.json</code>. Hub clock settings live in <code>hub_app_settings.json</code>.
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

      <h3 className="hub-section-title">Clock</h3>
      <p className="help-text">
        Used for Pixel clock sync (hub-wide, not per Pixel tab). Saving clears in-memory chat
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

        <div className="form-actions">
          <div className="form-actions-trailing">
            <button type="submit" className="btn btn-primary" disabled={locSaving}>
              {locSaving ? 'Saving...' : 'Save clock'}
            </button>
          </div>
        </div>
        {locSaveStatus === 'success' && (
          <div className="status-message success slide-enter">Clock saved.</div>
        )}
        {locSaveStatus === 'error' && (
          <div className="status-message error slide-enter">Failed to save clock.</div>
        )}
      </form>
    </div>
  );
};

export default HubSettings;
