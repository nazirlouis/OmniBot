import React, { useEffect, useRef, useState } from 'react';
import './IntelligenceFeed.css';

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
            {logs.map((log) => {
              return (
              <div
                key={log.id}
                className={`message-bubble ${log.sender}`}
              >
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
                  ) : log.sender === 'tool' ? (
                    <code className="tool-call-code">{log.text}</code>
                  ) : (
                    <>
                      {log.text}
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
            )})}
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
