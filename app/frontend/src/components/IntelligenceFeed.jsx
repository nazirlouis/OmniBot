import React, { useEffect, useRef, useState } from 'react';
import './IntelligenceFeed.css';
import {
  loadMapsPlacesForContextual,
  resolveMapsJsApiKey
} from '../mapsContextualLoader';

const normalizeWeatherCondition = (condition) => {
  return String(condition ?? '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/-/g, '_');
};

const prettyWeatherCondition = (condition) => {
  const c = normalizeWeatherCondition(condition);
  switch (c) {
    case 'sunny':
      return 'Sunny';
    case 'cloudy':
      return 'Cloudy';
    case 'partially_cloudy':
      return 'Partly Cloudy';
    case 'raining':
      return 'Raining';
    case 'snowing':
      return 'Snowing';
    default:
      return c ? c.replace(/_/g, ' ') : 'Weather';
  }
};

const parseShowWeatherFromText = (text) => {
  // Expected tool-call text shape from App.jsx:
  // show_weather(condition: cloudy, temperature: 72)
  const raw = String(text ?? '');
  if (!/show_weather/i.test(raw)) return null;

  const conditionMatch = raw.match(/condition\s*:\s*([^,)]+)/i);
  const temperatureMatch = raw.match(/temperature\s*:\s*([-+]?\d*\.?\d+)/i);

  const condition = conditionMatch ? conditionMatch[1].trim() : '';
  const temperature = temperatureMatch ? Number(temperatureMatch[1]) : 0;

  if (!condition && !Number.isFinite(temperature)) return null;
  return { condition, temperature };
};

const MapsGroundingSources = ({ sources }) => {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="maps-grounding-sources">
      <p className="maps-grounding-line">
        {sources.map((s, i) => (
          <span key={s.uri}>
            {i > 0 && <span>, </span>}
            <a href={s.uri} target="_blank" rel="noopener noreferrer">
              {s.title || 'Place'}
            </a>
          </span>
        ))}
      </p>
      <p className="gmp-attribution" translate="no">
        Google Maps
      </p>
    </div>
  );
};

const MapsContextualWidget = ({ contextToken }) => {
  const hostRef = useRef(null);
  const [phase, setPhase] = useState('loading');

  useEffect(() => {
    const token = String(contextToken || '').trim();
    if (!token) return undefined;

    let cancelled = false;
    const hostEl = hostRef.current;

    (async () => {
      try {
        const apiKey = await resolveMapsJsApiKey();
        if (!apiKey) {
          if (!cancelled) setPhase('no_key');
          return;
        }
        await loadMapsPlacesForContextual(apiKey);
        if (cancelled || !hostEl) return;
        hostEl.replaceChildren();
        const el = document.createElement('gmp-place-contextual');
        el.contextToken = token;
        hostEl.appendChild(el);
        if (!cancelled) setPhase('ready');
      } catch {
        if (!cancelled) setPhase('error');
      }
    })();

    return () => {
      cancelled = true;
      hostEl?.replaceChildren();
    };
  }, [contextToken]);

  if (!String(contextToken || '').trim()) return null;

  return (
    <div className="maps-contextual-wrap">
      {phase === 'loading' && (
        <p className="maps-widget-hint">Loading map widget…</p>
      )}
      {phase === 'no_key' && (
        <p className="maps-widget-hint maps-widget-hint-warn">
          Set <code>GOOGLE_MAPS_JS_API_KEY</code> in the hub backend environment (or{' '}
          <code>VITE_GOOGLE_MAPS_JS_API_KEY</code> in the dashboard) to show the contextual Google Maps
          widget. Use a key with the Maps JavaScript API enabled.
        </p>
      )}
      {phase === 'error' && (
        <p className="maps-widget-hint maps-widget-hint-warn">
          Could not load the Google Maps contextual widget.
        </p>
      )}
      <div ref={hostRef} className="maps-contextual-host" />
    </div>
  );
};

const MapsGroundingBlock = ({ sources, widgetContextToken }) => {
  const hasSources = sources && sources.length > 0;
  const token = String(widgetContextToken || '').trim();
  if (!hasSources && !token) return null;
  return (
    <div className="maps-grounding-block">
      {hasSources && <MapsGroundingSources sources={sources} />}
      {token && <MapsContextualWidget key={token} contextToken={token} />}
      {token && !hasSources && (
        <p className="gmp-attribution" translate="no">
          Google Maps
        </p>
      )}
    </div>
  );
};

const WeatherOverlay = ({ condition, temperature, durationMs = 5000 }) => {
  const [animating, setAnimating] = useState(true);

  useEffect(() => {
    setAnimating(true);
    const t = setTimeout(() => setAnimating(false), durationMs);
    return () => clearTimeout(t);
  }, [condition, temperature, durationMs]);

  const kind = normalizeWeatherCondition(condition);
  const tempF = Number.isFinite(Number(temperature)) ? Number(temperature) : 0;
  const tempRounded = Math.round(tempF);

  const showSun = kind === 'sunny' || kind === 'partially_cloudy';
  const showCloud =
    kind === 'cloudy' || kind === 'partially_cloudy' || kind === 'raining' || kind === 'snowing';
  const showRain = kind === 'raining';
  const showSnow = kind === 'snowing';

  return (
    <div className={`weather-overlay ${animating ? 'animating' : 'stopped'}`}>
      <svg
        className="weather-svg"
        viewBox="0 0 200 120"
        aria-label={`Weather: ${prettyWeatherCondition(kind)}`}
        role="img"
      >
        <g className="weather-ring">
          <circle cx="100" cy="60" r="46" fill="none" stroke="rgba(88, 166, 255, 0.35)" strokeWidth="4" />
        </g>

        <g className={`sun ${showSun ? '' : 'is-hidden'} ${animating ? '' : 'is-stopped'}`}>
          <circle cx="78" cy="48" r="20" fill="rgba(248, 186, 73, 0.95)" />
          <g className="sun-rays">
            <line x1="78" y1="18" x2="78" y2="30" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="78" y1="66" x2="78" y2="78" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="48" y1="48" x2="60" y2="48" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="96" y1="48" x2="108" y2="48" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="55" y1="28" x2="63" y2="36" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="93" y1="60" x2="101" y2="68" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="55" y1="68" x2="63" y2="60" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
            <line x1="93" y1="36" x2="101" y2="28" stroke="rgba(248, 186, 73, 0.95)" strokeWidth="5" strokeLinecap="round" />
          </g>
        </g>

        <g className={`cloud ${showCloud ? '' : 'is-hidden'} ${kind === 'partially_cloudy' ? 'partially' : ''}`}>
          <g className="cloud-bob">
            <ellipse cx="112" cy="62" rx="40" ry="22" fill="rgba(160, 200, 255, 0.85)" />
            <ellipse cx="90" cy="58" rx="26" ry="18" fill="rgba(160, 200, 255, 0.85)" />
            <ellipse cx="136" cy="54" rx="24" ry="16" fill="rgba(160, 200, 255, 0.85)" />
            <path
              d="M62,76 C64,58 82,50 98,56 C104,40 125,38 135,52 C154,52 168,63 168,78 L62,78 Z"
              fill="rgba(132, 182, 255, 0.88)"
            />
          </g>
        </g>

        <g className={`rain ${showRain ? '' : 'is-hidden'}`}>
          <g className="rain-group">
            <line className="rain-drop d1" x1="110" y1="82" x2="100" y2="110" stroke="rgba(90, 160, 255, 0.9)" strokeWidth="4" strokeLinecap="round" />
            <line className="rain-drop d2" x1="130" y1="82" x2="120" y2="110" stroke="rgba(90, 160, 255, 0.9)" strokeWidth="4" strokeLinecap="round" />
            <line className="rain-drop d3" x1="150" y1="82" x2="140" y2="110" stroke="rgba(90, 160, 255, 0.9)" strokeWidth="4" strokeLinecap="round" />
            <line className="rain-drop d4" x1="90" y1="82" x2="80" y2="110" stroke="rgba(90, 160, 255, 0.9)" strokeWidth="4" strokeLinecap="round" />
          </g>
        </g>

        <g className={`snow ${showSnow ? '' : 'is-hidden'}`}>
          <g className="snow-group">
            <circle className="snowflake f1" cx="115" cy="86" r="5" fill="rgba(240, 245, 255, 0.95)" />
            <circle className="snowflake f2" cx="135" cy="82" r="4" fill="rgba(240, 245, 255, 0.95)" />
            <circle className="snowflake f3" cx="153" cy="90" r="4.5" fill="rgba(240, 245, 255, 0.95)" />
            <circle className="snowflake f4" cx="95" cy="90" r="4" fill="rgba(240, 245, 255, 0.95)" />
            <circle className="snowflake f5" cx="75" cy="84" r="3.5" fill="rgba(240, 245, 255, 0.95)" />
          </g>
        </g>
      </svg>

      <div className="weather-overlay-meta">
        <div className="weather-temp">{tempRounded}°F</div>
        <div className="weather-condition">{prettyWeatherCondition(kind)}</div>
      </div>
    </div>
  );
};

const IntelligenceFeed = ({
  logs,
  wsStatus,
  textMessage,
  setTextMessage,
  isSendingText,
  onSendTextCommand
}) => {
  const logEndRef = useRef(null);
  const textInputRef = useRef(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  useEffect(() => {
    if (!isSendingText) {
      textInputRef.current?.focus();
    }
  }, [isSendingText]);

  return (
    <div className="intelligence-feed">
      <header className="feed-header">
        <h2>Intelligence Feed</h2>
        <div className="feed-header-actions">
          <div className={`ws-badge ${wsStatus}`}>
            <div className="status-dot"></div>
            {wsStatus === 'connected' ? 'Core Connected' : 'Core Disconnected'}
          </div>
        </div>
      </header>
      
      <div className="feed-content">
        {logs.length === 0 ? (
          <div className="empty-feed">
            <div className="pulse-ring"></div>
            <p>Waiting for sensory data...</p>
          </div>
        ) : (
          <div className="messages-area">
            {logs.map((log) => (
              <div key={log.id} className={`message-bubble ${log.sender}`}>
                <div className="message-meta">
                  <span className="message-sender">
                    {log.sender === 'system' && 'OmniBot Core'}
                    {log.sender === 'esp32' && 'Pixel Bot'}
                    {log.sender === 'video' && 'Pixel Bot (Video)'}
                    {log.sender === 'audio' && 'Pixel Bot (Audio)'}
                    {log.sender === 'ai' && 'Gemini AI'}
                    {log.sender === 'tool' && '⚡ Tool Call'}
                    {log.sender === 'user' && 'You'}
                    {log.sender === 'error' && 'System Error'}
                  </span>
                  <span className="message-time">{log.time}</span>
                </div>
                <div className="message-text">
                  {log.sender === 'video' ? (
                    <video
                      className="message-video"
                      src={log.text}
                      controls
                      autoPlay
                      loop
                      muted
                      playsInline
                    />
                  ) : log.sender === 'audio' ? (
                    <audio
                      className="message-audio"
                      src={log.text}
                      controls
                    />
                  ) : /show_weather/i.test(log.text) ? (
                    (() => {
                      const parsed = log.toolType === 'show_weather' ? null : parseShowWeatherFromText(log.text);
                      const condition = log.toolType === 'show_weather' ? log.weatherCondition : parsed?.condition;
                      const temperature = log.toolType === 'show_weather' ? log.weatherTemperature : parsed?.temperature;
                      return (
                        <WeatherOverlay
                          condition={condition}
                          temperature={temperature ?? 0}
                          durationMs={log.durationMs ?? 5000}
                        />
                      );
                    })()
                  ) : log.sender === 'tool' ? (
                    <code className="tool-call-code">{log.text}</code>
                  ) : (
                    <>
                      {log.text}
                      {log.sender === 'ai' && (
                        <MapsGroundingBlock
                          sources={log.mapsSources}
                          widgetContextToken={log.mapsWidgetContextToken}
                        />
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}
      </div>

      <form className="text-command-bar" onSubmit={onSendTextCommand}>
        <input
          ref={textInputRef}
          type="text"
          className="text-command-input"
          placeholder="Type a message to Pixel..."
          value={textMessage}
          onChange={(e) => setTextMessage(e.target.value)}
        />
        <button
          type="submit"
          className="text-command-send"
          disabled={isSendingText || !textMessage.trim()}
        >
          {isSendingText ? 'Sending...' : 'Send'}
        </button>
      </form>
    </div>
  );
};

export default IntelligenceFeed;
