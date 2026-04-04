import React from 'react';
import { Zap, RefreshCw, Bell, LogOut } from 'lucide-react';

function CartoonSoccer({ active }) {
  return (
    <svg width="20" height="20" viewBox="0 0 100 100" style={{ transition: 'transform 0.3s cubic-bezier(0.34,1.56,0.64,1)', transform: active ? 'scale(1.15)' : 'scale(0.9)' }}>
      <circle cx="50" cy="50" r="46" fill={active ? '#fff' : '#666'} stroke={active ? '#333' : '#444'} strokeWidth="4" />
      <polygon points="50,15 63,26 58,42 42,42 37,26" fill={active ? '#222' : '#444'} stroke={active ? '#333' : '#555'} strokeWidth="1" />
      <polygon points="80,38 82,55 68,62 58,50 66,36" fill={active ? '#222' : '#444'} stroke={active ? '#333' : '#555'} strokeWidth="1" />
      <polygon points="20,38 18,55 32,62 42,50 34,36" fill={active ? '#222' : '#444'} stroke={active ? '#333' : '#555'} strokeWidth="1" />
      <polygon points="30,74 40,64 50,68 60,64 70,74 62,86 38,86" fill={active ? '#222' : '#444'} stroke={active ? '#333' : '#555'} strokeWidth="1" />
      {active && <circle cx="50" cy="50" r="46" fill="none" stroke="rgba(16,185,129,0.4)" strokeWidth="3">
        <animate attributeName="r" values="46;48;46" dur="2s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.4;0;0.4" dur="2s" repeatCount="indefinite" />
      </circle>}
    </svg>
  );
}

function CartoonBasketball({ active }) {
  return (
    <svg width="20" height="20" viewBox="0 0 100 100" style={{ transition: 'transform 0.3s cubic-bezier(0.34,1.56,0.64,1)', transform: active ? 'scale(1.15)' : 'scale(0.9)' }}>
      <circle cx="50" cy="50" r="46" fill={active ? '#f97316' : '#886040'} stroke={active ? '#c2410c' : '#664830'} strokeWidth="4" />
      <ellipse cx="50" cy="50" rx="1.5" ry="46" fill="none" stroke={active ? '#9a3412' : '#553020'} strokeWidth="3" />
      <ellipse cx="50" cy="50" rx="46" ry="1.5" fill="none" stroke={active ? '#9a3412' : '#553020'} strokeWidth="3" />
      <path d="M 10 25 Q 30 40 50 35 Q 70 30 90 25" fill="none" stroke={active ? '#9a3412' : '#553020'} strokeWidth="2.5" />
      <path d="M 10 75 Q 30 60 50 65 Q 70 70 90 75" fill="none" stroke={active ? '#9a3412' : '#553020'} strokeWidth="2.5" />
      {active && <circle cx="50" cy="50" r="46" fill="none" stroke="rgba(16,185,129,0.4)" strokeWidth="3">
        <animate attributeName="r" values="46;48;46" dur="2s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.4;0;0.4" dur="2s" repeatCount="indefinite" />
      </circle>}
    </svg>
  );
}

export function Header({
  activeSport, setActiveSport, apiStatus,
  notifications, showNotifications, setShowNotifications, setNotifications,
  setActiveTab, setTrackingView, setScanPrediction, setScanResults,
  handleLogout,
}) {
  return (
    <header className="header">
      <div className="header-top-row">
        <div className="header-logo">
          <div className="logo-icon"><Zap /></div>
          <div className="logo-text" data-testid="app-logo">Reverse<span>Picks</span></div>
        </div>
        <div className="header-actions">
          <div className="sport-selector" data-testid="sport-selector">
            <button
              className={`sport-btn ${activeSport === 'soccer' ? 'active' : ''}`}
              onClick={() => { setActiveSport('soccer'); setScanPrediction({}); setScanResults([]); }}
              data-testid="sport-soccer-btn"
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <CartoonSoccer active={activeSport === 'soccer'} />
              <span>Soccer</span>
            </button>
            <button
              className={`sport-btn ${activeSport === 'basketball' ? 'active' : ''}`}
              onClick={() => { setActiveSport('basketball'); setScanPrediction({}); setScanResults([]); }}
              data-testid="sport-basketball-btn"
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <CartoonBasketball active={activeSport === 'basketball'} />
              <span>Basketball</span>
            </button>
          </div>
          <div style={{ position: 'relative' }}>
            <button className="icon-btn" onClick={() => setShowNotifications(!showNotifications)} data-testid="notification-bell">
              <Bell />
              {notifications.filter(n => !n.read).length > 0 && (
                <div className="notif-badge" data-testid="notif-count">{notifications.filter(n => !n.read).length}</div>
              )}
            </button>
            {showNotifications && (
              <div className="notif-dropdown" data-testid="notif-dropdown">
                <div className="notif-dropdown-header">
                  <span style={{ fontWeight: 800, fontSize: 13 }}>Notifications</span>
                  {notifications.length > 0 && (
                    <button style={{ fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 700 }}
                      onClick={() => { setNotifications(prev => prev.map(n => ({ ...n, read: true }))); }}>
                      Mark all read
                    </button>
                  )}
                </div>
                <div className="notif-list">
                  {notifications.length === 0 ? (
                    <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>No notifications yet</div>
                  ) : notifications.slice(0, 20).map(n => (
                    <div key={n.id} className={`notif-item ${n.read ? 'read' : 'unread'}`} onClick={() => {
                      setNotifications(prev => prev.map(x => x.id === n.id ? { ...x, read: true } : x));
                      setActiveTab('tracking');
                      setTrackingView(n.result === 'hit' ? 'won' : n.result === 'push' ? 'pushed' : n.result === 'miss' ? 'lost' : 'live');
                      setShowNotifications(false);
                    }}>
                      <div className={`notif-result ${n.result}`}>{n.result === 'hit' ? 'HIT' : n.result === 'push' ? 'PUSH' : 'MISS'}</div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700, fontSize: 12 }}>{n.playerName}</div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                          {n.recommendation?.toUpperCase()} {n.line} {n.propType} — Actual: {n.actualValue}
                        </div>
                      </div>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>{new Date(n.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <button className="icon-btn" onClick={handleLogout} data-testid="logout-btn" title="Logout">
            <LogOut />
          </button>
        </div>
      </div>
      <div className="header-status-row">
        <div className="api-badge">
          <div className={`api-dot ${apiStatus}`} data-testid="api-status-dot" />
          <span>API</span>
        </div>
        <div className="version-badge">v2.3</div>
        <button className="header-refresh-btn" onClick={() => window.location.reload()} data-testid="refresh-btn">
          <RefreshCw style={{ width: 12, height: 12 }} />
          <span>Refresh</span>
        </button>
      </div>
    </header>
  );
}
