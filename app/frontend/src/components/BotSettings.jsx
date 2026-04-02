import React, { useMemo, useState, useEffect } from 'react';
import './BotSettings.css';
import { getBotSettings, updateBotSettings, resetBotSettingsToDefault } from './setupService';
import { getCountryOptions, normalizeSavedCountryCode } from './countrySelectOptions';
import { resolveMapsJsApiKey, loadMapsPlacesForContextual } from '../mapsContextualLoader';

const TIMEZONE_OPTIONS = [
  { value: 'EST5EDT,M3.2.0/2,M11.1.0/2', label: 'US Eastern (EST/EDT)' },
  { value: 'CST6CDT,M3.2.0/2,M11.1.0/2', label: 'US Central (CST/CDT)' },
  { value: 'MST7MDT,M3.2.0/2,M11.1.0/2', label: 'US Mountain (MST/MDT)' },
  { value: 'PST8PDT,M3.2.0/2,M11.1.0/2', label: 'US Pacific (PST/PDT)' },
  { value: 'UTC0', label: 'UTC' }
];

const BotSettings = ({ setAppMode }) => {
  const [deviceId, setDeviceId] = useState('default_bot');
  const [model, setModel] = useState('gemini-3.1-flash-lite-preview');
  const [systemInstruction, setSystemInstruction] = useState('');
  const [timezoneRule, setTimezoneRule] = useState('EST5EDT,M3.2.0/2,M11.1.0/2');
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [mapsGroundingEnabled, setMapsGroundingEnabled] = useState(false);
  const [mapsStreet, setMapsStreet] = useState('');
  const [mapsState, setMapsState] = useState('');
  const [mapsPostalCode, setMapsPostalCode] = useState('');
  const [mapsCountry, setMapsCountry] = useState('');
  const [mapsLatitude, setMapsLatitude] = useState(null);
  const [mapsLongitude, setMapsLongitude] = useState(null);
  const [mapsDisplayName, setMapsDisplayName] = useState('');
  const [mapsGeocodeMessage, setMapsGeocodeMessage] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null); // 'success' | 'error' | null
  const hasTimezoneOption = TIMEZONE_OPTIONS.some((tz) => tz.value === timezoneRule);
  const countryOptions = useMemo(() => getCountryOptions(), []);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await getBotSettings(deviceId);
        setModel(data.model);
        setSystemInstruction(data.system_instruction);
        setTimezoneRule(data.timezone_rule || 'EST5EDT,M3.2.0/2,M11.1.0/2');
        setVisionEnabled(Boolean(data.vision_enabled));
        setMapsGroundingEnabled(Boolean(data.maps_grounding_enabled));
        setMapsStreet(data.maps_street || '');
        setMapsState(data.maps_state || '');
        setMapsPostalCode(data.maps_postal_code || '');
        setMapsCountry(normalizeSavedCountryCode(data.maps_country) || '');
        setMapsLatitude(data.maps_latitude ?? null);
        setMapsLongitude(data.maps_longitude ?? null);
        setMapsDisplayName(data.maps_display_name || '');
        setMapsGeocodeMessage(null);
      } catch (err) {
        console.error("Failed to fetch settings", err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchSettings();
  }, [deviceId]);

  useEffect(() => {
    let autocomplete = null;

    const initAutocomplete = async () => {
      try {
        const apiKey = await resolveMapsJsApiKey();
        if (!apiKey) return;
        await loadMapsPlacesForContextual(apiKey);
        
        const inputElem = document.getElementById('mapsStreet');
        if (!inputElem) return;
        
        if (window.google?.maps?.places) {
          autocomplete = new window.google.maps.places.Autocomplete(inputElem, {
            fields: ['formatted_address', 'name'],
            types: ['address'],
          });
          
          autocomplete.addListener('place_changed', () => {
            const place = autocomplete.getPlace();
            
            if (place.formatted_address) {
              setMapsStreet(place.formatted_address);
              setMapsState('');
              setMapsPostalCode('');
              setMapsCountry('');
            } else if (place.name) {
              setMapsStreet(place.name);
            }
          });
        }
      } catch (err) {
        console.warn('Maps Autocomplete disabled or unavailable:', err);
      }
    };
    
    if (!isLoading) {
      // Small timeout to ensure DOM input renders after isLoading=false
      setTimeout(initAutocomplete, 100);
    }
  }, [isLoading]);

  const handleSave = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    setSaveStatus(null);
    setMapsGeocodeMessage(null);
    
    // Fallback: forcefully read the physical DOM value in case Google Autocomplete mutated the input without firing React state
    const currentAddressInputDom = document.getElementById('mapsStreet')?.value;
    const finalAddress = currentAddressInputDom || mapsStreet;
    
    try {
      const res = await updateBotSettings(deviceId, {
        model,
        system_instruction: systemInstruction,
        timezone_rule: timezoneRule,
        vision_enabled: visionEnabled,
        maps_grounding_enabled: mapsGroundingEnabled,
        maps_street: finalAddress,
        maps_state: mapsState,
        maps_postal_code: mapsPostalCode,
        maps_country: mapsCountry,
        maps_latitude: mapsLatitude,
        maps_longitude: mapsLongitude,
        maps_display_name: mapsDisplayName || null
      });
      const saved = res.settings || {};
      applySettingsFromResponse(saved);
      const geo = res.maps_geocode;
      if (geo && geo.ok === false && geo.error) {
        setMapsGeocodeMessage(geo.error);
      }
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000); // Clear success message after 3s
    } catch (err) {
      console.error("Failed to save settings", err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  const applySettingsFromResponse = (saved) => {
    setModel(saved.model);
    setSystemInstruction(saved.system_instruction);
    setTimezoneRule(saved.timezone_rule || 'EST5EDT,M3.2.0/2,M11.1.0/2');
    setVisionEnabled(Boolean(saved.vision_enabled));
    setMapsGroundingEnabled(Boolean(saved.maps_grounding_enabled));
    setMapsStreet(saved.maps_street || '');
    setMapsState(saved.maps_state || '');
    setMapsPostalCode(saved.maps_postal_code || '');
    setMapsCountry(normalizeSavedCountryCode(saved.maps_country) || '');
    setMapsLatitude(saved.maps_latitude ?? null);
    setMapsLongitude(saved.maps_longitude ?? null);
    setMapsDisplayName(saved.maps_display_name || '');
    setMapsGeocodeMessage(null);
  };

  const handleResetToDefaults = async () => {
    if (
      !window.confirm(
        'Reset all settings for this bot to defaults? Your current values will be replaced on the server.'
      )
    ) {
      return;
    }
    setIsSaving(true);
    setSaveStatus(null);
    setMapsGeocodeMessage(null);
    try {
      const res = await resetBotSettingsToDefault(deviceId);
      if (res.status !== 'success' || !res.settings) {
        throw new Error(res.detail || 'Reset failed');
      }
      applySettingsFromResponse(res.settings);
      setSaveStatus('success');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to reset settings', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="settings-container">
        <div className="scanning-container">
          <div className="pulse-ring mx-auto"></div>
          <p>Loading AI Core Settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-container fade-in">
      <div className="settings-header">
        <h2>Optics & Intelligence Configuration</h2>
        <div className="bot-identifier">
          <span className="id-label">TARGET:</span> {deviceId}
        </div>
      </div>

      <form className="settings-form" onSubmit={handleSave}>
        
        <div className="form-group">
          <label htmlFor="visionSelect">Vision Input to Model</label>
          <div className="select-wrapper">
            <select
              id="visionSelect"
              value={visionEnabled ? 'on' : 'off'}
              onChange={(e) => setVisionEnabled(e.target.value === 'on')}
              className="holo-select"
            >
              <option value="off">Off</option>
              <option value="on">On</option>
            </select>
          </div>
          <p className="help-text">Default is Off. This setting is remembered per bot.</p>
        </div>

        <div className="form-group">
          <label htmlFor="timezoneSelect">Timezone (Clock Sync)</label>
          <div className="select-wrapper">
            <select
              id="timezoneSelect"
              value={timezoneRule}
              onChange={(e) => setTimezoneRule(e.target.value)}
              className="holo-select"
            >
              {!hasTimezoneOption && (
                <option value={timezoneRule}>Custom ({timezoneRule})</option>
              )}
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz.value} value={tz.value}>
                  {tz.label}
                </option>
              ))}
            </select>
          </div>
          <p className="help-text">Used by Pixel for NTP sync and RTC display time.</p>
        </div>

        <div className="form-group">
          <label htmlFor="mapsGroundingSelect">Google Maps grounding (Gemini)</label>
          <div className="select-wrapper">
            <select
              id="mapsGroundingSelect"
              value={mapsGroundingEnabled ? 'on' : 'off'}
              onChange={(e) => setMapsGroundingEnabled(e.target.value === 'on')}
              className="holo-select"
            >
              <option value="off">Off</option>
              <option value="on">On</option>
            </select>
          </div>
          <p className="help-text">
            When on, Pixel uses your postal location for local {'"near me"'} answers via Google Maps grounding.
            The Gemini API does not allow Maps and Google Search in the same session, so{' '}
            <strong>Search grounding is off</strong> while this is on. Enter your specific address or postal code; geocoding uses
            OpenStreetMap Nominatim (set <code>NOMINATIM_USER_AGENT</code> in backend .env). Countries from{' '}
            <code>i18n-iso-countries</code>.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="mapsStreet">Address</label>
          <input
            id="mapsStreet"
            type="text"
            className="holo-input"
            value={mapsStreet}
            onChange={(e) => setMapsStreet(e.target.value)}
            placeholder="e.g. 5423 Suffex Green Ln NW, Atlanta, GA 30339"
            autoComplete="off"
          />
          <p className="help-text">Google Maps will automatically format your full address here for accurate geocoding.</p>
        </div>



        {(mapsLatitude != null && mapsLongitude != null) && (
          <div className="form-group maps-resolved-block">
            <p className="help-text maps-resolved-title">Resolved for Maps tool</p>
            <p className="maps-coords">
              {Number(mapsLatitude).toFixed(5)}, {Number(mapsLongitude).toFixed(5)}
            </p>
            {mapsDisplayName && (
              <p className="maps-display-name">{mapsDisplayName}</p>
            )}
          </div>
        )}

        {mapsGeocodeMessage && (
          <div className="status-message error slide-enter maps-geocode-error">
            Location lookup failed: {mapsGeocodeMessage}
          </div>
        )}

        <div className="form-group">
          <label htmlFor="modelSelect">Generative Model Core</label>
          <div className="select-wrapper">
            <select 
              id="modelSelect"
              value={model} 
              onChange={(e) => setModel(e.target.value)}
              className="holo-select"
            >
              <option value="gemini-3.1-flash-lite-preview">Gemini 3.1 Flash Lite (Default/Fast)</option>
              <option value="gemini-3-flash-preview">Gemini 3 Flash</option>
              <option value="gemini-3.1-pro-preview">Gemini 3.1 Pro (Advanced Reasoning)</option>
            </select>
          </div>
          <p className="help-text">Select the neural engine powering this bot's responses.</p>
        </div>

        <div className="form-group">
          <label htmlFor="sysInstruction">System Instructions (Personality & Context)</label>
          <textarea 
            id="sysInstruction"
            value={systemInstruction}
            onChange={(e) => setSystemInstruction(e.target.value)}
            className="holo-textarea"
            rows="6"
          />
          <p className="help-text">Give the bot specific context on how to behave, what it sees, and how to respond.</p>
        </div>

        <div className="form-actions">
          <button
            type="button"
            className="btn btn-reset-defaults"
            onClick={handleResetToDefaults}
            disabled={isSaving}
          >
            Reset to defaults
          </button>
          <div className="form-actions-trailing">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setAppMode('dashboard')}
              disabled={isSaving}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSaving}
            >
              {isSaving ? 'Syncing...' : 'Sync to Core'}
            </button>
          </div>
        </div>

        {saveStatus === 'success' && (
          <div className="status-message success slide-enter">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
            Settings saved and synced successfully.
          </div>
        )}
        
        {saveStatus === 'error' && (
          <div className="status-message error slide-enter">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            Failed to sync settings. Check core connection.
          </div>
        )}
      </form>
    </div>
  );
};

export default BotSettings;
