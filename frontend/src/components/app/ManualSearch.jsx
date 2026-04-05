import React, { useState, useEffect, useRef } from 'react';
import { Search, ChevronDown, ChevronRight, Loader2, User, X } from 'lucide-react';
import { getManualLeagues, getManualTeams, searchManualPlayer } from '../../api';

const PROP_TYPES = ['pass_attempts', 'shots', 'shots_on_target', 'tackles', 'saves', 'interceptions', 'key_passes', 'clearances', 'blocks', 'crosses', 'dribbles', 'fouls_drawn'];
const PROP_LABELS = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'Shots on Target',
  tackles: 'Tackles', saves: 'GK Saves', interceptions: 'Interceptions',
  key_passes: 'Key Passes', clearances: 'Clearances', blocks: 'Blocks',
  crosses: 'Crosses', dribbles: 'Dribbles', fouls_drawn: 'Fouls Drawn',
};

export function ManualSearch({ onResult, activeSport }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState('league'); // league → team → player → prop
  const [leagues, setLeagues] = useState([]);
  const [teams, setTeams] = useState([]);
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [playerFilter, setPlayerFilter] = useState('');
  const filterRef = useRef(null);

  // Selections
  const [selectedLeague, setSelectedLeague] = useState(null);
  const [selectedTeam, setSelectedTeam] = useState(null);
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [selectedOpponent, setSelectedOpponent] = useState(null);
  const [propType, setPropType] = useState('');
  const [line, setLine] = useState('');
  const [venue, setVenue] = useState('home');

  // Load leagues on first open
  useEffect(() => {
    if (open && leagues.length === 0) {
      getManualLeagues().then(d => setLeagues(d.leagues || [])).catch(() => {});
    }
  }, [open]);

  async function handleLeagueSelect(lg) {
    setSelectedLeague(lg);
    setStep('team');
    setLoading(true);
    try {
      const d = await getManualTeams(lg.id);
      setTeams(d.teams || []);
    } catch { setTeams([]); }
    setLoading(false);
  }

  async function handleTeamSelect(tm) {
    setSelectedTeam(tm);
    setStep('player');
    setPlayerFilter('');
    setLoading(true);
    try {
      const d = await searchManualPlayer(tm.teamId, selectedLeague.id, '');
      setPlayers(d.players || []);
    } catch { setPlayers([]); }
    setLoading(false);
  }

  async function handlePlayerSearch(query) {
    setPlayerFilter(query);
    if (query.length < 2) return;
    setLoading(true);
    try {
      const d = await searchManualPlayer(selectedTeam.teamId, selectedLeague.id, query);
      setPlayers(d.players || []);
    } catch { setPlayers([]); }
    setLoading(false);
  }

  function handlePlayerSelect(p) {
    setSelectedPlayer(p);
    setStep('prop');
  }

  function handleSubmit() {
    if (!selectedPlayer || !propType || !line || !selectedTeam) return;
    // Find opponent from remaining teams
    const oppTeam = selectedOpponent;
    onResult({
      playerName: selectedPlayer.name,
      playerId: selectedPlayer.id,
      teamName: selectedTeam.name,
      teamId: selectedTeam.teamId,
      opponentName: oppTeam?.name || '',
      opponentId: oppTeam?.teamId || 0,
      propType,
      line: parseFloat(line),
      venue,
      leagueId: selectedLeague.id,
      leagueName: selectedLeague.name,
      position: selectedPlayer.position,
      sport: 'soccer',
    });
    // Reset
    handleReset();
  }

  function handleReset() {
    setOpen(false);
    setStep('league');
    setSelectedLeague(null);
    setSelectedTeam(null);
    setSelectedPlayer(null);
    setSelectedOpponent(null);
    setPropType('');
    setLine('');
    setVenue('home');
    setPlayerFilter('');
    setPlayers([]);
    setTeams([]);
  }

  function handleBack() {
    if (step === 'prop') { setStep('player'); setSelectedPlayer(null); }
    else if (step === 'player') { setStep('team'); setSelectedTeam(null); setPlayers([]); }
    else if (step === 'team') { setStep('league'); setSelectedLeague(null); setTeams([]); }
  }

  // Breadcrumb
  const crumbs = [];
  if (selectedLeague) crumbs.push(selectedLeague.name);
  if (selectedTeam) crumbs.push(selectedTeam.name);
  if (selectedPlayer) crumbs.push(selectedPlayer.name);

  // Filtered players
  const displayPlayers = playerFilter.length >= 2
    ? players
    : players.slice(0, 25);

  // Opponent list = all teams except selected
  const opponentOptions = teams.filter(t => t.teamId !== selectedTeam?.teamId);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        data-testid="manual-search-toggle"
        style={{
          width: '100%', padding: '12px 16px', borderRadius: 12,
          border: '1.5px dashed rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.02)',
          color: 'var(--text-muted)', fontSize: 12, fontWeight: 700,
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
          transition: 'all 0.2s', letterSpacing: '0.04em', marginTop: 12,
        }}
      >
        <Search style={{ width: 14, height: 14 }} />
        MANUAL SEARCH
      </button>
    );
  }

  return (
    <div data-testid="manual-search-panel" style={{
      marginTop: 12, borderRadius: 14,
      border: '1.5px solid rgba(16,185,129,0.12)', background: 'rgba(0,0,0,0.3)',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 14px', borderBottom: '1px solid rgba(255,255,255,0.04)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Search style={{ width: 14, height: 14, color: 'var(--accent)' }} />
          <span style={{ fontSize: 10, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Manual Search
          </span>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {step !== 'league' && (
            <button onClick={handleBack} data-testid="manual-back-btn" style={{
              background: 'none', border: 'none', color: 'var(--text-muted)',
              fontSize: 10, fontWeight: 700, cursor: 'pointer', padding: '2px 6px',
            }}>BACK</button>
          )}
          <button onClick={handleReset} data-testid="manual-close-btn" style={{
            background: 'none', border: 'none', cursor: 'pointer', padding: 2,
          }}>
            <X style={{ width: 14, height: 14, color: 'var(--text-muted)' }} />
          </button>
        </div>
      </div>

      {/* Breadcrumb */}
      {crumbs.length > 0 && (
        <div style={{
          padding: '6px 14px', fontSize: 9, color: 'var(--text-muted)',
          display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap',
          borderBottom: '1px solid rgba(255,255,255,0.03)',
        }}>
          {crumbs.map((c, i) => (
            <React.Fragment key={i}>
              {i > 0 && <ChevronRight style={{ width: 8, height: 8 }} />}
              <span style={{ fontWeight: 700, color: i === crumbs.length - 1 ? 'var(--accent)' : 'var(--text-muted)' }}>{c}</span>
            </React.Fragment>
          ))}
        </div>
      )}

      <div style={{ padding: '8px 14px 14px', maxHeight: 340, overflowY: 'auto' }}>
        {/* STEP: League */}
        {step === 'league' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            {leagues.map(lg => (
              <button
                key={lg.id}
                onClick={() => handleLeagueSelect(lg)}
                data-testid={`league-${lg.id}`}
                style={{
                  padding: '10px 8px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.06)',
                  background: 'rgba(255,255,255,0.02)', color: '#fff', fontSize: 11, fontWeight: 700,
                  cursor: 'pointer', textAlign: 'center', transition: 'all 0.15s',
                }}
              >{lg.name}</button>
            ))}
          </div>
        )}

        {/* STEP: Team */}
        {step === 'team' && (
          loading ? (
            <div style={{ textAlign: 'center', padding: 20 }}><Loader2 className="animate-spin" style={{ width: 18, height: 18, color: 'var(--accent)' }} /></div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
              {teams.map(tm => (
                <button
                  key={tm.teamId}
                  onClick={() => handleTeamSelect(tm)}
                  data-testid={`team-${tm.teamId}`}
                  style={{
                    padding: '8px 6px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.05)',
                    background: 'rgba(255,255,255,0.02)', color: '#fff', fontSize: 10, fontWeight: 600,
                    cursor: 'pointer', textAlign: 'left', display: 'flex', alignItems: 'center', gap: 6,
                    transition: 'all 0.15s', overflow: 'hidden',
                  }}
                >
                  {tm.logo && <img src={tm.logo} alt="" style={{ width: 18, height: 18, borderRadius: 3 }} />}
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tm.name}</span>
                </button>
              ))}
            </div>
          )
        )}

        {/* STEP: Player */}
        {step === 'player' && (
          <>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
              borderRadius: 8, border: '1px solid rgba(255,255,255,0.06)',
              background: 'rgba(255,255,255,0.02)', marginBottom: 8,
            }}>
              <Search style={{ width: 14, height: 14, color: 'var(--text-muted)', flexShrink: 0 }} />
              <input
                ref={filterRef}
                type="text"
                value={playerFilter}
                onChange={e => handlePlayerSearch(e.target.value)}
                placeholder="Search player name..."
                autoFocus
                data-testid="manual-player-search"
                style={{
                  flex: 1, background: 'transparent', border: 'none', outline: 'none',
                  color: '#fff', fontSize: 12, fontFamily: 'inherit',
                }}
              />
            </div>
            {loading ? (
              <div style={{ textAlign: 'center', padding: 16 }}><Loader2 className="animate-spin" style={{ width: 16, height: 16, color: 'var(--accent)' }} /></div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {displayPlayers.map(p => (
                  <button
                    key={p.id}
                    onClick={() => handlePlayerSelect(p)}
                    data-testid={`player-${p.id}`}
                    style={{
                      padding: '8px 10px', borderRadius: 8, border: 'none',
                      background: 'rgba(255,255,255,0.02)', color: '#fff', fontSize: 11, fontWeight: 600,
                      cursor: 'pointer', textAlign: 'left', display: 'flex', alignItems: 'center', gap: 8,
                      transition: 'background 0.15s',
                    }}
                  >
                    {p.photo ? (
                      <img src={p.photo} alt="" style={{ width: 24, height: 24, borderRadius: 12, objectFit: 'cover' }} />
                    ) : (
                      <User style={{ width: 16, height: 16, color: 'var(--text-muted)' }} />
                    )}
                    <div>
                      <div style={{ fontWeight: 700 }}>{p.name}</div>
                      <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>
                        {p.position}{p.number ? ` · #${p.number}` : ''}
                      </div>
                    </div>
                  </button>
                ))}
                {displayPlayers.length === 0 && !loading && (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', textAlign: 'center', padding: 12 }}>
                    {playerFilter.length < 2 ? 'Type at least 2 characters' : 'No players found'}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* STEP: Prop Config */}
        {step === 'prop' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {/* Player info */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
              borderRadius: 8, background: 'rgba(16,185,129,0.04)', border: '1px solid rgba(16,185,129,0.1)',
            }}>
              {selectedPlayer.photo ? (
                <img src={selectedPlayer.photo} alt="" style={{ width: 32, height: 32, borderRadius: 16 }} />
              ) : (
                <User style={{ width: 20, height: 20, color: 'var(--accent)' }} />
              )}
              <div>
                <div style={{ fontSize: 13, fontWeight: 800, color: '#fff' }}>{selectedPlayer.name}</div>
                <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>
                  {selectedTeam.name} · {selectedPlayer.position} · {selectedLeague.name}
                </div>
              </div>
            </div>

            {/* Opponent Select */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Opponent</div>
              <select
                value={selectedOpponent?.teamId || ''}
                onChange={e => {
                  const t = opponentOptions.find(t => t.teamId === parseInt(e.target.value));
                  setSelectedOpponent(t || null);
                }}
                data-testid="manual-opponent-select"
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(0,0,0,0.4)',
                  color: '#fff', fontSize: 12, fontFamily: 'inherit',
                }}
              >
                <option value="">Select opponent...</option>
                {opponentOptions.map(t => (
                  <option key={t.teamId} value={t.teamId}>{t.name}</option>
                ))}
              </select>
            </div>

            {/* Venue */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Venue</div>
              <div style={{ display: 'flex', gap: 6 }}>
                {['home', 'away'].map(v => (
                  <button
                    key={v}
                    onClick={() => setVenue(v)}
                    data-testid={`manual-venue-${v}`}
                    style={{
                      flex: 1, padding: '8px 0', borderRadius: 8, fontSize: 11, fontWeight: 800,
                      textTransform: 'uppercase', letterSpacing: '0.06em', cursor: 'pointer',
                      border: '1.5px solid',
                      borderColor: venue === v ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
                      background: venue === v ? 'var(--accent-dim)' : 'transparent',
                      color: venue === v ? 'var(--accent)' : 'var(--text-muted)',
                    }}
                  >{v}</button>
                ))}
              </div>
            </div>

            {/* Prop Type */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Prop Type</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {PROP_TYPES.map(pt => (
                  <button
                    key={pt}
                    onClick={() => setPropType(pt)}
                    data-testid={`manual-prop-${pt}`}
                    style={{
                      padding: '6px 10px', borderRadius: 6, fontSize: 9, fontWeight: 700,
                      cursor: 'pointer', border: '1px solid',
                      borderColor: propType === pt ? 'var(--accent)' : 'rgba(255,255,255,0.06)',
                      background: propType === pt ? 'var(--accent-dim)' : 'transparent',
                      color: propType === pt ? 'var(--accent)' : 'var(--text-secondary)',
                      textTransform: 'uppercase', letterSpacing: '0.04em',
                    }}
                  >{PROP_LABELS[pt]}</button>
                ))}
              </div>
            </div>

            {/* Line */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Line</div>
              <input
                type="number"
                step="0.5"
                value={line}
                onChange={e => setLine(e.target.value)}
                placeholder="e.g. 30.5"
                data-testid="manual-line-input"
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(0,0,0,0.4)',
                  color: '#fff', fontSize: 14, fontWeight: 700, fontFamily: 'inherit',
                }}
              />
            </div>

            {/* Submit */}
            <button
              onClick={handleSubmit}
              disabled={!propType || !line || !selectedOpponent}
              data-testid="manual-run-prediction"
              style={{
                width: '100%', padding: '12px 0', borderRadius: 10, border: 'none',
                background: (!propType || !line || !selectedOpponent) ? 'rgba(255,255,255,0.06)' : 'var(--accent)',
                color: (!propType || !line || !selectedOpponent) ? 'var(--text-muted)' : '#000',
                fontSize: 13, fontWeight: 900, cursor: (!propType || !line || !selectedOpponent) ? 'not-allowed' : 'pointer',
                letterSpacing: '0.04em', transition: 'all 0.2s',
              }}
            >
              RUN PREDICTION
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
