import React from 'react';
import { Zap, RefreshCw, Bell, LogOut } from 'lucide-react';

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
            onClick={() => { setActiveSport('soccer'); setScanPrediction(null); setScanResults([]); }}
            data-testid="sport-soccer-btn"
          >
            Soccer
          </button>
          <button
            className={`sport-btn ${activeSport === 'basketball' ? 'active' : ''}`}
            onClick={() => { setActiveSport('basketball'); setScanPrediction(null); setScanResults([]); }}
            data-testid="sport-basketball-btn"
          >
            Basketball
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
