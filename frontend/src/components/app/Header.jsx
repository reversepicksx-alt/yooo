import React from 'react';
import { Zap, RefreshCw, Bell, LogOut } from 'lucide-react';

function CartoonSoccer({ active }) {
  return (
    <svg width="22" height="22" viewBox="0 0 100 100" style={{ filter: active ? 'drop-shadow(0 0 6px rgba(16,185,129,0.5))' : 'none', transition: 'filter 0.3s' }}>
      <circle cx="50" cy="50" r="46" fill={active ? '#fff' : '#888'} stroke={active ? '#222' : '#555'} strokeWidth="3" />
      <polygon points="50,18 62,28 58,42 42,42 38,28" fill={active ? '#1a1a2e' : '#555'} />
      <polygon points="26,52 22,38 34,30 44,40 38,54" fill={active ? '#1a1a2e' : '#555'} />
      <polygon points="74,52 78,38 66,30 56,40 62,54" fill={active ? '#1a1a2e' : '#555'} />
      <polygon points="36,68 42,56 58,56 64,68 56,78 44,78" fill={active ? '#1a1a2e' : '#555'} />
      <circle cx="42" cy="40" r="4" fill={active ? '#fff' : '#888'}>
        <animate attributeName="r" values="4;3.5;4" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle cx="58" cy="40" r="4" fill={active ? '#fff' : '#888'}>
        <animate attributeName="r" values="4;3.5;4" dur="2s" repeatCount="indefinite" begin="0.2s" />
      </circle>
      <circle cx="42" cy="39" r="1.8" fill={active ? '#1a1a2e' : '#333'} />
      <circle cx="58" cy="39" r="1.8" fill={active ? '#1a1a2e' : '#333'} />
      <path d="M 45 50 Q 50 55 55 50" stroke={active ? '#1a1a2e' : '#555'} strokeWidth="2" fill="none" strokeLinecap="round">
        {active && <animate attributeName="d" values="M 45 50 Q 50 55 55 50;M 45 49 Q 50 56 55 49;M 45 50 Q 50 55 55 50" dur="3s" repeatCount="indefinite" />}
      </path>
    </svg>
  );
}

function CartoonBasketball({ active }) {
  return (
    <svg width="22" height="22" viewBox="0 0 100 100" style={{ filter: active ? 'drop-shadow(0 0 6px rgba(16,185,129,0.5))' : 'none', transition: 'filter 0.3s' }}>
      <circle cx="50" cy="50" r="46" fill={active ? '#f97316' : '#886040'} stroke={active ? '#c2410c' : '#664830'} strokeWidth="3" />
      <path d="M 50 4 Q 50 50 50 96" stroke={active ? '#7c2d12' : '#553020'} strokeWidth="2.5" fill="none" />
      <path d="M 4 50 Q 50 50 96 50" stroke={active ? '#7c2d12' : '#553020'} strokeWidth="2.5" fill="none" />
      <path d="M 15 20 Q 50 35 85 20" stroke={active ? '#7c2d12' : '#553020'} strokeWidth="2" fill="none" />
      <path d="M 15 80 Q 50 65 85 80" stroke={active ? '#7c2d12' : '#553020'} strokeWidth="2" fill="none" />
      <circle cx="40" cy="40" r="4.5" fill="#fff">
        <animate attributeName="r" values="4.5;4;4.5" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle cx="60" cy="40" r="4.5" fill="#fff">
        <animate attributeName="r" values="4.5;4;4.5" dur="2s" repeatCount="indefinite" begin="0.2s" />
      </circle>
      <circle cx="40" cy="39" r="2" fill={active ? '#1a1a2e' : '#333'} />
      <circle cx="60" cy="39" r="2" fill={active ? '#1a1a2e' : '#333'} />
      <path d="M 44 52 Q 50 58 56 52" stroke="#fff" strokeWidth="2.5" fill="none" strokeLinecap="round">
        {active && <animate attributeName="d" values="M 44 52 Q 50 58 56 52;M 44 51 Q 50 59 56 51;M 44 52 Q 50 58 56 52" dur="3s" repeatCount="indefinite" />}
      </path>
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
      <div className="header-logo">
        <div className="logo-icon"><Zap /></div>
        <div className="logo-text" data-testid="app-logo">Reverse<span>Picks</span></div>
      </div>
      <div className="header-right">
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
        <div className="api-badge">
          <div className={`api-dot ${apiStatus}`} data-testid="api-status-dot" />
          <span>API</span>
        </div>
        <div className="version-badge">v2.3</div>
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
        <button className="icon-btn" onClick={() => window.location.reload()} data-testid="refresh-btn">
          <RefreshCw />
        </button>
        <button className="icon-btn" onClick={handleLogout} data-testid="logout-btn" title="Logout">
          <LogOut />
        </button>
      </div>
    </header>
  );
}
