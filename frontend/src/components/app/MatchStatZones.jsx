import React from 'react';
import { BarChart3 } from 'lucide-react';

export function MatchStatZones({ teamStats, opponentStats, venue }) {
  if (!teamStats?.length && !opponentStats?.length) return null;

  const playerVenue = (venue || 'home').toLowerCase();
  const oppVenue = playerVenue === 'home' ? 'away' : 'home';

  const StatBar = ({ label, value, max, color }) => {
    const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
    return (
      <div style={{ marginBottom: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>
          <span>{label}</span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{value}</span>
        </div>
        <div style={{ height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 3, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.6s ease' }} />
        </div>
      </div>
    );
  };

  const avgStat = (stats, key) => {
    const vals = stats.map(s => {
      const v = s[key];
      return typeof v === 'string' ? parseFloat(v) : v;
    }).filter(v => v != null && !isNaN(v));
    return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length * 10) / 10 : 0;
  };

  const teamAvg = teamStats?.length ? {
    possession: avgStat(teamStats, 'possession'),
    shots: avgStat(teamStats, 'totalShots'),
    shotsOnTarget: avgStat(teamStats, 'shotsOnTarget'),
    insideBox: avgStat(teamStats, 'shotsInsideBox'),
    outsideBox: avgStat(teamStats, 'shotsOutsideBox'),
    passes: avgStat(teamStats, 'totalPasses'),
    passAcc: avgStat(teamStats, 'passAccuracy'),
  } : null;

  const oppAvg = opponentStats?.length ? {
    possession: avgStat(opponentStats, 'possession'),
    shots: avgStat(opponentStats, 'totalShots'),
    shotsOnTarget: avgStat(opponentStats, 'shotsOnTarget'),
    insideBox: avgStat(opponentStats, 'shotsInsideBox'),
    outsideBox: avgStat(opponentStats, 'shotsOutsideBox'),
    passes: avgStat(opponentStats, 'totalPasses'),
    passAcc: avgStat(opponentStats, 'passAccuracy'),
  } : null;

  return (
    <div className="stat-box" style={{ borderColor: 'rgba(99,102,241,0.2)', background: 'rgba(99,102,241,0.03)' }} data-testid="match-stat-zones">
      <div className="stat-label flex items-center gap-2" style={{ marginBottom: 12 }}>
        <BarChart3 style={{ width: 12, height: 12, color: '#6366f1' }} /> Match Stat Zones (Venue-Filtered)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: teamAvg && oppAvg ? '1fr 1fr' : '1fr', gap: 16 }}>
        {teamAvg && (
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#10b981', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>Team ({playerVenue})</div>
            <StatBar label="Possession" value={`${teamAvg.possession}%`} max={100} color="#10b981" />
            <StatBar label="Total Shots" value={teamAvg.shots} max={20} color="#10b981" />
            <StatBar label="Shots On Target" value={teamAvg.shotsOnTarget} max={10} color="#10b981" />
            <StatBar label="Inside Box" value={teamAvg.insideBox} max={15} color="#6366f1" />
            <StatBar label="Outside Box" value={teamAvg.outsideBox} max={10} color="#8b5cf6" />
            <StatBar label="Total Passes" value={teamAvg.passes} max={700} color="#10b981" />
            <StatBar label="Pass Accuracy" value={`${teamAvg.passAcc}%`} max={100} color="#10b981" />
          </div>
        )}
        {oppAvg && (
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#f43f5e', marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>Opponent ({oppVenue})</div>
            <StatBar label="Possession" value={`${oppAvg.possession}%`} max={100} color="#f43f5e" />
            <StatBar label="Total Shots" value={oppAvg.shots} max={20} color="#f43f5e" />
            <StatBar label="Shots On Target" value={oppAvg.shotsOnTarget} max={10} color="#f43f5e" />
            <StatBar label="Inside Box" value={oppAvg.insideBox} max={15} color="#ef4444" />
            <StatBar label="Outside Box" value={oppAvg.outsideBox} max={10} color="#dc2626" />
            <StatBar label="Total Passes" value={oppAvg.passes} max={700} color="#f43f5e" />
            <StatBar label="Pass Accuracy" value={`${oppAvg.passAcc}%`} max={100} color="#f43f5e" />
          </div>
        )}
      </div>
    </div>
  );
}
