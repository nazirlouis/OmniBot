import React, { useEffect, useMemo, useRef, useState } from 'react';
import './IntelligenceFeed.css';

/** Renders assistant text with clickable [n](url) grounding citations from Gemini. */
function AiMessageText({ text }) {
  if (text == null || text === '') return null;
  const s = String(text);
  const parts = [];
  const re = /(\[[0-9]+\]\([^)]+\))/g;
  let last = 0;
  let m;
  let key = 0;
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) {
      parts.push(<span key={`t${key++}`}>{s.slice(last, m.index)}</span>);
    }
    const inner = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(m[1]);
    if (inner) {
      parts.push(
        <a key={`a${key++}`} href={inner[2]} target="_blank" rel="noopener noreferrer">
          [{inner[1]}]
        </a>
      );
    } else {
      parts.push(<span key={`e${key++}`}>{m[1]}</span>);
    }
    last = m.index + m[0].length;
  }
  if (last < s.length) {
    parts.push(<span key={`t${key++}`}>{s.slice(last)}</span>);
  }
  return <>{parts}</>;
}

const SearchGroundingBlock = ({ sources, queries }) => {
  const hasSources = sources && sources.length > 0;
  const hasQueries = queries && queries.length > 0;
  if (!hasSources && !hasQueries) return null;
  return (
    <div className="maps-grounding-block">
      {hasSources && (
        <div className="maps-grounding-sources">
          <p className="maps-grounding-line">
            {sources.map((s, i) => (
              <span key={s.uri}>
                {i > 0 && <span>, </span>}
                <a href={s.uri} target="_blank" rel="noopener noreferrer">
                  {s.title || 'Web source'}
                </a>
              </span>
            ))}
          </p>
          <p className="gmp-attribution" translate="no">
            Google Search
          </p>
        </div>
      )}
      {hasQueries && (
        <p className="maps-widget-hint">
          Search queries: {queries.join(' | ')}
        </p>
      )}
    </div>
  );
};

function formatToolArguments(args) {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

const WAKE_LISTEN_UI = {
  wake_required: {
    label: 'Wake phrase',
    hint: 'Hub is waiting for your wake phrase (e.g. hey_jarvis).',
  },
  follow_up: {
    label: 'Just speak',
    hint: 'After the last reply: you can talk without the wake phrase until this window ends (hub VAD).',
  },
  streaming: {
    label: 'Live mic',
    hint: 'Your voice is being streamed to the model for this turn.',
  },
};

const IntelligenceFeed = ({
  logs,
  toolCalls = [],
  hubActivityLog = [],
  selectedBotId = 'default_bot',
  livePreviewSrc = null,
  wakeListenMode = null,
  wsStatus,
  textMessage,
  setTextMessage,
  isSendingText,
  onSendTextCommand,
}) => {
  const logEndRef = useRef(null);
  const textInputRef = useRef(null);
  const [hubLogOpen, setHubLogOpen] = useState(false);

  // Browser-side voice chat. Web Speech APIs are wired in two halves:
  //   - SpeechRecognition (input): mic button toggles; on a final result we
  //     populate the chat input and auto-submit, so the rest of the chat
  //     flow (Ollama + Box-3 display animations) is identical to typing.
  //   - SpeechSynthesis (output): new assistant messages are spoken via the
  //     native TTS available on the host OS (macOS uses its built-in voices
  //     — no cloud, no API key, no Gemini Live needed).
  // Falls back gracefully when either API is unavailable in the browser.
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef(null);
  const lastSpokenIdxRef = useRef(-1);
  const sendOnFinalResultRef = useRef(false);

  // Loud one-shot marker so we can verify the latest JS is loaded.
  // If you don't see this on page load, the browser is serving a
  // cached older bundle.
  useEffect(() => {
    console.warn('[voice] IntelligenceFeed mounted — voice rev 2026-05-14');
  }, []);
  // Persist the user's mute preference across reloads. Defaults to ON
  // (audible) so the voice loop "just works" out of the box.
  const [browserTtsEnabled, setBrowserTtsEnabled] = useState(() => {
    try {
      return window.localStorage.getItem('omnibot_browser_tts') !== 'off';
    } catch {
      return true;
    }
  });

  // Piper voice picker. The dashboard lists every .onnx model present
  // in the hub's piper_voices/ directory. Selection persists across
  // reloads. When Piper isn't installed (or no models present) the
  // /api/tts/voices endpoint returns available=false and the picker
  // hides itself, leaving the browser TTS fallback path in charge.
  const [piperVoices, setPiperVoices] = useState([]);
  const [piperAvailable, setPiperAvailable] = useState(false);
  const [selectedPiperVoice, setSelectedPiperVoice] = useState(() => {
    try {
      return window.localStorage.getItem('omnibot_piper_voice') || '';
    } catch {
      return '';
    }
  });
  useEffect(() => {
    let cancelled = false;
    fetch('/api/tts/voices')
      .then((r) => r.ok ? r.json() : Promise.reject(r.status))
      .then((data) => {
        if (cancelled) return;
        setPiperAvailable(!!data.available);
        setPiperVoices(data.voices || []);
        if (!selectedPiperVoice && data.default) {
          setSelectedPiperVoice(data.default);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        console.warn('[voice] /api/tts/voices failed:', err);
        setPiperAvailable(false);
      });
    return () => { cancelled = true; };
  }, [selectedPiperVoice]);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return; // No SpeechRecognition in this browser — mic button hidden below.
    const r = new SR();
    r.continuous = false;
    r.interimResults = true;
    r.lang = 'en-US';
    r.onresult = (e) => {
      // Concatenate all chunks; show interim text live in the input box.
      let txt = '';
      let final = false;
      for (let i = 0; i < e.results.length; i++) {
        txt += e.results[i][0].transcript;
        if (e.results[i].isFinal) final = true;
      }
      setTextMessage(txt.trim());
      if (final) sendOnFinalResultRef.current = true;
    };
    r.onend = () => {
      setIsListening(false);
      if (sendOnFinalResultRef.current) {
        sendOnFinalResultRef.current = false;
        // Submit the form programmatically so the chat flow matches typed
        // messages exactly (same /api/text-command POST, same Box-3 events).
        setTimeout(() => {
          const btn = document.querySelector('.text-command-send');
          if (btn && !btn.disabled) btn.click();
        }, 50);
      }
    };
    r.onerror = (e) => {
      console.warn('[voice] SpeechRecognition error', e.error);
      setIsListening(false);
    };
    recognitionRef.current = r;
  }, [setTextMessage]);

  const voiceAvailable =
    typeof window !== 'undefined' &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);

  const toggleListening = () => {
    const r = recognitionRef.current;
    if (!r) return;
    if (isListening) {
      try { r.stop(); } catch {}
      setIsListening(false);
    } else {
      setTextMessage('');
      try {
        r.start();
        setIsListening(true);
      } catch (err) {
        console.warn('[voice] start failed', err);
      }
    }
  };

  const chatLogs = useMemo(
    () =>
      logs.filter(
        (log) => log.sender !== 'tool' && log.sender !== 'system' && log.sender !== 'error'
      ),
    [logs]
  );

  const toolCallsForBot = useMemo(
    () => toolCalls.filter((t) => (t.deviceId || 'default_bot') === selectedBotId),
    [toolCalls, selectedBotId]
  );

  const hubLogCount = toolCallsForBot.length + hubActivityLog.length;

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatLogs]);

  // Speak each new assistant reply once via the browser's native TTS.
  // Tracks last-spoken length in a ref so re-renders don't replay old
  // messages. On first mount we mark the current history as already-spoken
  // so the whole log doesn't recite on page load. Falls back silently when
  // SpeechSynthesis is unavailable.
  // Helper: speak text with the system's native voice. Handles the
  // common gotcha where window.speechSynthesis.getVoices() returns
  // empty on first call — voices load asynchronously, and any speak()
  // before they're populated is silently dropped on some Chromium
  // builds. We also explicitly cancel any previous utterance to avoid
  // the queue piling up across rapid replies.
  // Currently-playing piper audio element, kept in a ref so the next
  // speak can stop+replace the previous one (matches the SpeechSynthesis
  // cancel-and-speak pattern).
  const piperAudioRef = useRef(null);

  // Token of the currently-in-flight speak() invocation. A new call
  // increments this; any older pipeline still running checks the ref
  // against its captured token and bails on mismatch. Lets stopSpeaking
  // and rapid back-to-back replies actually halt synth requests that
  // haven't returned from Piper yet.
  const speakTokenRef = useRef(0);

  // Sentence-streamed Piper TTS. The model's reply can be a 200-token
  // paragraph; synthesizing it as ONE Piper request means audio start
  // has to wait for the full paragraph synth (~3–8s on Mac CPU). We
  // split on sentence boundaries (. ! ? plus newlines), request each
  // chunk in parallel, and play them strictly in order — first audio
  // starts within ~1s of the first sentence finishing on Piper.
  const splitSentences = (text) => {
    // Split keeping the punctuation attached to its sentence.
    // Falls back to one big chunk if no boundaries are found.
    const out = [];
    const re = /[^.!?\n]+[.!?\n]+|[^.!?\n]+$/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      const s = m[0].trim();
      if (s) out.push(s);
    }
    return out.length ? out : [text];
  };

  const speak = async (text) => {
    if (!text) return;
    const myToken = ++speakTokenRef.current;

    // Stop any in-flight piper playback so rapid replies don't overlap.
    if (piperAudioRef.current) {
      try { piperAudioRef.current.pause(); } catch {}
      try { piperAudioRef.current.src = ''; } catch {}
      piperAudioRef.current = null;
    }

    const sentences = splitSentences(text);

    // Kick off all Piper requests in parallel — they'll come back in
    // arbitrary order, but we await them in sequence below so playback
    // stays correctly ordered. This pipelines synth + playback: while
    // sentence N is playing, sentences N+1..end are still synthesizing.
    const synthPromises = sentences.map((s) =>
      fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: s, voice: selectedPiperVoice || undefined }),
      })
        .then(async (res) => (res.ok ? URL.createObjectURL(await res.blob()) : null))
        .catch(() => null)
    );

    try {
      let piperFailed = false;
      for (let i = 0; i < synthPromises.length; i++) {
        // If a newer speak() superseded us, abandon the rest.
        if (speakTokenRef.current !== myToken) return;
        const url = await synthPromises[i];
        if (speakTokenRef.current !== myToken) {
          if (url) URL.revokeObjectURL(url);
          return;
        }
        if (!url) { piperFailed = true; continue; }
        const audio = new Audio(url);
        piperAudioRef.current = audio;
        if (i === 0) console.warn('[voice] piper playback started');
        audio.onerror = (e) => console.warn('[voice] piper playback error', e);
        // Wait for this clip to finish before starting the next.
        await new Promise((resolve) => {
          let done = false;
          const finish = () => {
            if (done) return;
            done = true;
            try { URL.revokeObjectURL(url); } catch {}
            resolve();
          };
          audio.onended = finish;
          audio.onerror = finish;
          audio.play().catch(finish);
        });
      }
      if (piperFailed && speakTokenRef.current === myToken) {
        // At least one chunk's synth failed entirely; for those we
        // didn't play anything. Fall back to browser TTS for the
        // remainder so the user still hears the reply.
        console.warn('[voice] piper had failures; falling back to browser TTS for tail');
        speakWithBrowserTts(text);
      }
    } catch (err) {
      console.warn('[voice] piper pipeline failed, falling back to browser TTS:', err);
      if (speakTokenRef.current === myToken) speakWithBrowserTts(text);
    }
  };

  // Cut off whatever is currently being said. Hits both playback paths
  // because we don't know which one is active at any given moment.
  const stopSpeaking = () => {
    // Bump the token so any pending sentence chunks in the streamed
    // speak() pipeline drop their next .play() on the floor.
    speakTokenRef.current += 1;
    if (piperAudioRef.current) {
      try { piperAudioRef.current.pause(); } catch {}
      try { piperAudioRef.current.src = ''; } catch {}
      piperAudioRef.current = null;
    }
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch {}
    }
  };

  const speakWithBrowserTts = (text) => {
    if (!('speechSynthesis' in window)) {
      console.warn('[voice] speechSynthesis not available');
      return;
    }
    const synth = window.speechSynthesis;
    const doSpeak = () => {
      const all = synth.getVoices();
      // User-selected voice wins; fall back to first en-* voice.
      const chosen =
        all.find((v) => v.name === selectedVoiceName) ||
        all.find((v) => v.lang?.startsWith('en')) ||
        all[0];
      const u = new SpeechSynthesisUtterance(text);
      if (chosen) u.voice = chosen;
      u.rate = 1.05;
      u.pitch = 1.0;
      u.volume = 1.0;
      u.onstart = () => console.warn('[voice] tts started, voice =', chosen?.name);
      u.onerror = (ev) => console.warn('[voice] tts error', ev.error);
      synth.cancel(); // clear any in-flight queue
      synth.speak(u);
    };
    if (synth.getVoices().length > 0) {
      doSpeak();
    } else {
      // First-load case: wait for the voiceschanged event, then speak.
      const handler = () => {
        synth.removeEventListener('voiceschanged', handler);
        doSpeak();
      };
      synth.addEventListener('voiceschanged', handler);
      // Some Chromium versions never fire voiceschanged — fall back after 500 ms.
      setTimeout(() => {
        synth.removeEventListener('voiceschanged', handler);
        doSpeak();
      }, 500);
    }
  };

  // Speak the LATEST AI reply when a chat turn completes. We detect
  // completion via the isSendingText prop going true → false, which the
  // parent already toggles around the /api/text-command request. Watching
  // chatLogs.length doesn't work: replies stream in, so the entry exists
  // with empty .text on the first update and length never re-increments
  // as text fills in.
  const wasSendingRef = useRef(false);
  const lastSpokenLogsLenRef = useRef(0);

  useEffect(() => {
    const justFinished = wasSendingRef.current && !isSendingText;
    wasSendingRef.current = isSendingText;
    if (!justFinished) return;
    if (!browserTtsEnabled) return;
    if (!('speechSynthesis' in window)) return;
    if (chatLogs.length <= lastSpokenLogsLenRef.current) return;

    // Find the latest AI entry with non-empty text — the just-finished reply.
    let lastAi = null;
    for (let i = chatLogs.length - 1; i >= 0; i--) {
      if (chatLogs[i].sender === 'ai' && chatLogs[i].text) {
        lastAi = chatLogs[i];
        break;
      }
    }
    if (lastAi) {
      // Strip:
      //  - markdown link footnotes  [1](url)
      //  - asterisk-wrapped stage directions like *face_animation* or
      //    *waves hand* — the model emits these as italics-style asides
      //    and they read awkwardly when spoken aloud
      //  - lingering backtick code spans `like_this`
      const clean = String(lastAi.text)
        .replace(/\[[0-9]+\]\([^)]*\)/g, '')
        .replace(/\*[^*]+\*/g, '')
        .replace(/`[^`]+`/g, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
      console.warn('[voice] queueing tts for AI reply:', clean.slice(0, 60));
      speak(clean);
    } else {
      console.warn('[voice] turn finished but no ai entry with text found');
    }
    lastSpokenLogsLenRef.current = chatLogs.length;
  }, [isSendingText, chatLogs, browserTtsEnabled]);

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
          <button
            type="button"
            className={`feed-tool-log-btn ${hubLogOpen ? 'active' : ''}`}
            onClick={() => setHubLogOpen((o) => !o)}
            title="Tool calls, hub system messages, and errors"
          >
            Hub log
            {hubLogCount > 0 && <span className="feed-tool-log-badge">{hubLogCount}</span>}
          </button>
          {wakeListenMode && WAKE_LISTEN_UI[wakeListenMode] ? (
            <div
              className={`wake-listen-pill wake-listen-pill--${wakeListenMode}`}
              title={WAKE_LISTEN_UI[wakeListenMode].hint}
            >
              <span className="wake-listen-pill-dot" aria-hidden />
              {WAKE_LISTEN_UI[wakeListenMode].label}
            </div>
          ) : null}
          <div className={`ws-badge ${wsStatus}`}>
            <div className="status-dot"></div>
            {wsStatus === 'connected' ? 'Core Connected' : 'Core Disconnected'}
          </div>
        </div>
      </header>

      {livePreviewSrc ? (
        <div className="live-model-preview" aria-label="Video frames sent to the model">
          <div className="live-model-preview-head">
            <span>Live to model</span>
            <span className="live-model-preview-hint">JPEG mirrors Gemini Live input (~1/s)</span>
          </div>
          <div className="live-model-preview-frame">
            <img src={livePreviewSrc} alt="Latest frame sent to the model" />
          </div>
        </div>
      ) : null}

      {hubLogOpen && (
        <div className="tool-log-panel" aria-label="Hub activity log">
          <div className="tool-log-panel-head">
            <span>Hub activity</span>
            <span className="tool-log-hint">Newest at the bottom</span>
          </div>
          <div className="tool-log-scroll">
            <h3 className="tool-log-section-title">Tool calls — {selectedBotId}</h3>
            {toolCallsForBot.length === 0 ? (
              <p className="tool-log-empty">No tool calls for this bot yet.</p>
            ) : (
              toolCallsForBot.map((tc) => (
                <div key={tc.id} className="tool-log-entry">
                  <div className="tool-log-entry-top">
                    <time className="tool-log-time">{tc.time}</time>
                    <code className="tool-log-fn">{tc.functionName}</code>
                  </div>
                  <details className="tool-log-details">
                    <summary>Arguments</summary>
                    <pre className="tool-log-args">{formatToolArguments(tc.arguments)}</pre>
                  </details>
                </div>
              ))
            )}

            <h3 className="tool-log-section-title">System and errors</h3>
            {hubActivityLog.length === 0 ? (
              <p className="tool-log-empty">No system messages or errors yet.</p>
            ) : (
              hubActivityLog.map((ev) => (
                <div
                  key={ev.id}
                  className={`hub-activity-entry hub-activity-entry--${ev.sender}`}
                >
                  <div className="hub-activity-meta">
                    <time className="tool-log-time">{ev.time}</time>
                    <span className="hub-activity-label">
                      {ev.sender === 'error' ? 'Error' : 'System'}
                    </span>
                  </div>
                  <p className="hub-activity-text">{ev.text}</p>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      <div className="feed-content">
        {chatLogs.length === 0 ? (
          <div className="empty-feed">
            <div className="pulse-ring"></div>
            <p>Waiting for sensory data...</p>
          </div>
        ) : (
          <div className="messages-area">
            {chatLogs.map((log) => {
              return (
                <div key={log.id} className={`message-bubble ${log.sender}`}>
                  <div className="message-meta">
                    <span className="message-sender">
                      {log.sender === 'esp32' && 'Pixel Bot'}
                      {log.sender === 'video' && 'Pixel Bot (Video)'}
                      {log.sender === 'audio' && 'Pixel Bot (Audio)'}
                      {log.sender === 'ai' && 'Gemini AI'}
                      {log.sender === 'user' && 'You'}
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
                      <audio className="message-audio" src={log.text} controls />
                    ) : (
                      <>
                        {log.sender === 'ai' ? (
                          <AiMessageText text={log.text} />
                        ) : (
                          log.text
                        )}
                        {log.sender === 'ai' && (
                          <>
                            <SearchGroundingBlock
                              sources={log.searchSources}
                              queries={log.searchQueries}
                            />
                          </>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
            <div ref={logEndRef} />
          </div>
        )}
      </div>

      <form className="text-command-bar" onSubmit={onSendTextCommand}>
        {voiceAvailable && (
          <button
            type="button"
            className="text-command-mic"
            onClick={toggleListening}
            disabled={isSendingText}
            title={isListening ? 'Stop listening' : 'Tap to speak'}
            aria-label={isListening ? 'Stop listening' : 'Tap to speak'}
            style={{
              background: isListening ? '#e74c3c' : 'transparent',
              color: isListening ? '#fff' : 'inherit',
              border: '1px solid currentColor',
              borderRadius: 6,
              padding: '0 10px',
              cursor: isSendingText ? 'not-allowed' : 'pointer',
              marginRight: 6,
            }}
          >
            {isListening ? '● REC' : '🎤'}
          </button>
        )}
        {piperAvailable && piperVoices.length > 0 && (
          <select
            value={selectedPiperVoice}
            onChange={(e) => {
              const name = e.target.value;
              setSelectedPiperVoice(name);
              try { window.localStorage.setItem('omnibot_piper_voice', name); } catch {}
              // Preview the newly-chosen Piper voice immediately so the
              // user hears it without having to send a chat first.
              if (browserTtsEnabled) {
                // speak() always sends the current selectedPiperVoice
                // via the request body; we just kick a request.
                speak('This is how I sound.');
              }
            }}
            title="Piper neural voice"
            style={{
              maxWidth: 240,
              marginRight: 6,
              border: '1px solid currentColor',
              borderRadius: 6,
              padding: '4px 6px',
              background: 'transparent',
              cursor: 'pointer',
            }}
          >
            {piperVoices.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name} ({v.size_mb} MB)
              </option>
            ))}
          </select>
        )}
        {('speechSynthesis' in window) && (
          <button
            type="button"
            className="text-command-tts-toggle"
            onClick={() => {
              const next = !browserTtsEnabled;
              setBrowserTtsEnabled(next);
              try {
                window.localStorage.setItem('omnibot_browser_tts', next ? 'on' : 'off');
              } catch {}
              if (next) {
                // Immediate audible confirmation that TTS is wired up
                // (uses the same Piper-first path the chat replies do).
                speak('Audio enabled.');
              } else {
                window.speechSynthesis?.cancel();
              }
            }}
            title={browserTtsEnabled ? 'Mute reply audio' : 'Unmute reply audio'}
            aria-label={browserTtsEnabled ? 'Mute reply audio' : 'Unmute reply audio'}
            style={{
              background: 'transparent',
              border: '1px solid currentColor',
              borderRadius: 6,
              padding: '0 10px',
              cursor: 'pointer',
              marginRight: 6,
              opacity: browserTtsEnabled ? 1 : 0.5,
            }}
          >
            {browserTtsEnabled ? '🔊' : '🔇'}
          </button>
        )}
        <button
          type="button"
          className="text-command-tts-stop"
          onClick={stopSpeaking}
          title="Stop speaking"
          aria-label="Stop speaking"
          style={{
            background: 'transparent',
            border: '1px solid currentColor',
            borderRadius: 6,
            padding: '0 10px',
            cursor: 'pointer',
            marginRight: 6,
          }}
        >
          ⏹
        </button>
        <input
          ref={textInputRef}
          type="text"
          className="text-command-input"
          placeholder={isListening ? 'Listening…' : 'Type a message to Pixel…'}
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
