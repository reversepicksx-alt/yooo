import React, { useMemo, useRef, useState } from 'react';
import {
  View, Text, Image, TouchableOpacity, StyleSheet, Share, Platform, ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { Pick } from '@/lib/api';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'PASSES', passes: 'PASSES', shots: 'SHOTS', shots_on_target: 'SOT',
  tackles: 'TACKLES', key_passes: 'KEY PASSES', saves: 'SAVES', interceptions: 'INTS',
  blocks: 'BLOCKS', dribbles: 'DRIBBLES', crosses: 'CROSSES', clearances: 'CLEARANCES',
  goals: 'GOALS', assists: 'ASSISTS', fouls_drawn: 'FOULS WON', fouls_committed: 'FOULS',
  duels_won: 'DUELS', yellow_cards: 'YC', shots_assisted: 'SHOT ASSISTS', goalie_saves: 'SAVES',
};

function isSettled(p: Pick) {
  return p.matchStatus === 'final'
    || p.status === 'settled'
    || ['hit', 'miss', 'push', 'won', 'lost', 'dnp'].includes(p.result ?? '');
}

function isLive(p: Pick) {
  // Settled picks are never live, even if stale fields say otherwise.
  if (isSettled(p)) return false;
  // A pick is only live if there is concrete in-match evidence.
  // Trusting backend status='live' alone caused false positives for future matches.
  const hasLiveSignal =
    (p.matchStatus === 'live' && ((p.elapsed != null && p.elapsed > 0) || p.currentValue != null || (p.pace != null && p.pace > 0)))
    || (p.status === 'live' && ((p.elapsed != null && p.elapsed > 0) || p.currentValue != null || (p.pace != null && p.pace > 0)))
    || (p.elapsed != null && p.elapsed > 0)
    || (p.currentValue != null)
    || (p.pace != null && p.pace > 0);
  return hasLiveSignal;
}

function isPending(p: Pick) {
  return !isSettled(p) && !isLive(p);
}

function pickWon(p: Pick) { return p.result === 'hit' || p.result === 'won' || p.status === 'won'; }
function pickLost(p: Pick) { return p.result === 'miss' || p.result === 'lost' || p.status === 'lost'; }
function pickPush(p: Pick) { return p.result === 'push'; }
function pickDnp(p: Pick) { return p.result === 'dnp'; }

function getRecDir(p: Pick): 'OVER' | 'UNDER' | null {
  const rec = p.recommendation;
  if (rec === 'OVER' || rec === 'UNDER') return rec;
  const proj = p.projection ?? p.projectedValue;
  if (proj != null && p.line > 0) return proj > p.line ? 'OVER' : 'UNDER';
  return null;
}

function formatNumber(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return Number(n).toFixed(n % 1 === 0 ? 0 : 1);
}

function propUnit(propType?: string) {
  if (!propType) return '';
  if (['pass_attempts', 'passes', 'tackles', 'clearances', 'duels_won', 'saves', 'goalie_saves', 'interceptions', 'blocks'].includes(propType)) return 'passes';
  if (['shots', 'shots_on_target', 'shots_assisted'].includes(propType)) return 'shots';
  if (['goals', 'assists'].includes(propType)) return '';
  return 'units';
}

function formatMatchTime(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const isTomorrow = d.toDateString() === tomorrow.toDateString();
  const timeStr = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  if (isToday) return `Today, ${timeStr}`;
  if (isTomorrow) return `Tomorrow, ${timeStr}`;
  const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  return `${dateStr}, ${timeStr}`;
}

export default function OwnerPickCard({
  pick,
  onPress,
  onTrack,
  onDelete,
  onPlayerPress,
}: {
  pick: Pick;
  onPress?: () => void;
  onTrack?: () => void;
  onDelete?: () => void;
  onPlayerPress?: (pick: Pick) => void;
}) {
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const push = pickPush(pick);
  const dnp = pickDnp(pick);
  const settled = isSettled(pick);
  const live = isLive(pick);
  const pending = isPending(pick);

  const dir = getRecDir(pick);
  const isOver = dir === 'OVER';
  const recColor = isOver ? Colors.success : Colors.error;

  const lineValue = typeof pick.line === 'number' ? pick.line : null;
  const projValue = pick.projection ?? pick.projectedValue ?? null;
  const actualValue = pick.actualValue ?? pick.currentValue ?? null;
  const livePace = pick.pace ?? null;

  // NOW: live/actual value; for settled picks show actual value.
  const nowValue = settled ? actualValue : (pick.currentValue ?? pick.actualValue ?? null);
  const nowLabel = settled ? 'FINAL' : live ? 'NOW' : 'NOW';

  // PACE only when actually live; otherwise PROJ (or DNP).
  const paceValue = live ? (livePace ?? projValue) : projValue;
  const paceLabel = dnp ? 'DNP' : settled ? 'PROJ' : live ? 'PACE' : 'PROJ';
  const paceColor = live ? Colors.primary : Colors.text;

  const hitPct = pick.hitPct ?? null;

  const progress = useMemo(() => {
    if (lineValue == null || nowValue == null || lineValue <= 0) return null;
    return Math.max(0, Math.min(100, (nowValue / (lineValue * 2)) * 100));
  }, [lineValue, nowValue]);

  // Match context
  const venueLower = (pick.venue || '').toLowerCase();
  const venueKnown = venueLower === 'home' || venueLower === 'away';
  const homeTeamName = pick.homeTeam || (venueKnown ? (venueLower === 'away' ? pick.opponentName : pick.teamName) : '') || '';
  const awayTeamName = pick.awayTeam || (venueKnown ? (venueLower === 'away' ? pick.teamName : pick.opponentName) : '') || '';
  const finalHome = pick.finalHomeGoals;
  const finalAway = pick.finalAwayGoals;
  const hasScore = finalHome != null && finalAway != null;
  const hasActualPoss = pick.homePoss != null && pick.awayPoss != null;
  const hasProjPoss = pick.projHomePoss != null && pick.projAwayPoss != null;
  const trustOrient = !!(pick.homeTeam && pick.awayTeam) || venueKnown;

  const showScoreLine = trustOrient && (
    ((settled || live) && hasScore)
    || hasActualPoss
    || hasProjPoss
  );

  const statusBadge = useMemo(() => {
    if (won) return <View style={[styles.badge, { backgroundColor: Colors.success }]}><Text style={styles.badgeText}>HIT</Text></View>;
    if (lost) return <View style={[styles.badge, { backgroundColor: Colors.error }]}><Text style={styles.badgeText}>MISS</Text></View>;
    if (push) return <View style={[styles.badge, { backgroundColor: Colors.push }]}><Text style={styles.badgeText}>PUSH</Text></View>;
    if (dnp) return <View style={[styles.badge, { backgroundColor: Colors.dnp }]}><Text style={styles.badgeText}>DNP</Text></View>;
    if (live) return (
      <View style={styles.badgeLive}>
        <View style={styles.pulseDot} />
        <Text style={styles.badgeLiveText}>LIVE</Text>
      </View>
    );
    if (pending) return <View style={[styles.badge, { backgroundColor: 'rgba(255,255,255,0.12)' }]}><Text style={[styles.badgeText, { color: '#fff' }]}>PENDING</Text></View>;
    return null;
  }, [won, lost, push, dnp, live, pending]);

  // Share image generation
  const [sharing, setSharing] = useState(false);
  const [photoFailed, setPhotoFailed] = useState(false);
  const webRef = useRef<HTMLDivElement | null>(null);

  const venueText = pick.venue === 'away' ? 'AWAY' : 'HOME';
  const teamVenue = `${pick.teamName || 'Team'} · ${venueText}`;
  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ').toUpperCase() || 'PROP';
  const elapsed = pick.elapsed ?? (pick as any).matchMinute ?? null;
  const matchTime = !settled ? formatMatchTime(pick.fixtureDate) : '';
  const scriptType = dir ? `${dir} ${propLabel}` : propLabel;

  // Game script for pending pre-match card.
  const gs = pick.gameScript;
  const hasGameScript = !!gs && !!gs.dominant && pending;
  const gsColor = (gs?.color as string) || '#60A5FA';
  const gsIconMap: Record<string, string> = {
    low_scoring: 'shield', high_scoring: 'flame', open_close: 'analytics',
    home_blowout: 'trending-up', away_blowout: 'trending-down',
  };
  const gsIcon = (gsIconMap[(gs?.dominant as string) || ''] || 'analytics') as any;
  const gsScenarios = (gs?.scenarios as any[] | undefined) || [];

  const shareText = useMemo(() => {
    const dir = getRecDir(pick) ?? pick.recommendation ?? '';
    const venue = pick.venue === 'away' ? 'AWAY' : 'HOME';
    let text = `${pick.playerName} ${dir} ${pick.line} ${propLabel} (${venue})`;
    if (matchTime) text += ` — ${matchTime}`;
    if (nowValue != null) text += ` — ${nowLabel} ${formatNumber(nowValue)}`;
    if (lineValue != null) text += ` / Line ${formatNumber(lineValue)}`;
    if (hitPct != null) text += ` · ${Math.round(hitPct)}% hit prob`;
    text += ' via Reverse Picks';
    return text;
  }, [pick, nowValue, lineValue, hitPct, propLabel, nowLabel, matchTime]);

  const handleShare = async () => {
    setSharing(true);
    try {
      if (Platform.OS === 'web') {
        await shareWebImage();
      } else {
        await shareFallbackText();
      }
    } catch (e) {
      // ignored
    } finally {
      setSharing(false);
    }
  };

  const shareFallbackText = async () => {
    try {
      await Share.share({ message: shareText, title: `${pick.playerName} prop pick` });
    } catch {
      // user cancelled
    }
  };

  const shareWebImage = async () => {
    // Create a hidden DOM card for capture.
    const container = document.createElement('div');
    container.style.position = 'fixed';
    container.style.left = '-9999px';
    container.style.top = '0';
    container.style.width = '360px';
    container.style.fontFamily = 'Inter, -apple-system, BlinkMacSystemFont, sans-serif';
    document.body.appendChild(container);

    const cardHTML = renderShareableCardHTML(pick, {
      won, lost, push, dnp, live, pending, dir, isOver, recColor,
      nowValue, nowLabel, paceValue, paceLabel, paceColor, hitPct, lineValue,
      progress, hasScore, finalHome, finalAway, homeTeamName, awayTeamName,
      hasActualPoss, hasProjPoss, showScoreLine, propLabel, elapsed, venueText,
      matchTime, scriptType,
    });
    container.innerHTML = cardHTML;

    // Wait for images to load.
    await Promise.all(
      Array.from(container.querySelectorAll('img')).map(
        (img) => new Promise<void>((resolve) => { img.onload = () => resolve(); img.onerror = () => resolve(); })
      )
    );
    await new Promise((r) => setTimeout(r, 150));

    const html2canvas = (await import('html2canvas')).default;
    const canvas = await html2canvas(container.firstElementChild as HTMLElement, {
      scale: 3,
      backgroundColor: '#0F0F0F',
      useCORS: true,
      logging: false,
    });
    document.body.removeChild(container);

    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!blob) return;

    const file = new File([blob], `reverse-picks-${pick.pickId || 'share'}.png`, { type: 'image/png' });

    if (typeof navigator !== 'undefined' && (navigator as any).canShare && (navigator as any).canShare({ files: [file] })) {
      await navigator.share({
        files: [file],
        title: `${pick.playerName} prop pick`,
        text: shareText,
      });
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    }
  };

  return (
    <TouchableOpacity
      activeOpacity={onPress ? 0.85 : 1}
      onPress={onPress}
      style={[styles.card, won && styles.cardWon, lost && styles.cardLost]}
    >
      <View style={styles.topRow}>
        <View style={styles.identity}>
          {pick.ownerPlayerPhoto ? (
            <Image
              source={{ uri: pick.ownerPlayerPhoto }}
              style={styles.photo}
              onError={() => setPhotoFailed(true)}
            />
          ) : null}
          {(!pick.ownerPlayerPhoto || photoFailed) && (
            <View style={[styles.photoPlaceholder, pick.ownerPlayerPhoto && photoFailed ? styles.photoOverlay : null]}>
              <Text style={styles.photoInitial}>{pick.playerName?.charAt(0) || '?'}</Text>
            </View>
          )}
          <View style={styles.nameBlock}>
            <TouchableOpacity activeOpacity={0.7} onPress={() => onPlayerPress?.(pick)} disabled={!onPlayerPress}>
              <Text style={styles.playerName} numberOfLines={1}>{pick.playerName}</Text>
            </TouchableOpacity>
            <View style={styles.teamRow}>
              {pick.ownerTeamLogo ? (
                <Image source={{ uri: pick.ownerTeamLogo }} style={styles.teamLogo} />
              ) : null}
              <Text style={styles.teamVenue} numberOfLines={1}>{teamVenue}</Text>
            </View>
          </View>
        </View>
        <View style={styles.actions}>
          {Platform.OS === 'web' && onDelete && (
            // @ts-ignore raw DOM button intentional
            <button
              type="button"
              onClick={(e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
              onPointerDown={(e: React.PointerEvent) => e.stopPropagation()}
              onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
              onTouchStart={(e: React.TouchEvent) => e.stopPropagation()}
              style={{ all: 'unset', cursor: 'pointer', padding: '3px 5px', borderRadius: 5, display: 'inline-flex', alignItems: 'center' }}
              aria-label="Delete pick"
            >
              <Ionicons name="trash-outline" size={16} color={Colors.error} />
            </button>
          )}
          <TouchableOpacity onPress={handleShare} style={styles.shareBtn} activeOpacity={0.7}>
            {sharing ? (
              <ActivityIndicator size="small" color={Colors.primary} />
            ) : (
              <Ionicons name="share-outline" size={16} color={Colors.primary} />
            )}
          </TouchableOpacity>
          {statusBadge}
        </View>
      </View>

      <View style={styles.statsRow}>
        {!pending && (
          <View style={styles.stat}>
            <Text style={styles.statLabel}>{nowLabel}</Text>
            <Text style={[styles.statValue, { color: nowValue != null && lineValue != null ? (
              (isOver && nowValue > lineValue) || (!isOver && nowValue < lineValue) ? Colors.success : Colors.error
            ) : Colors.text }]}>
              {formatNumber(nowValue)}
            </Text>
          </View>
        )}
        <View style={styles.stat}>
          <Text style={styles.statLabel}>LINE</Text>
          <Text style={styles.statValue}>{formatNumber(lineValue)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>{paceLabel}</Text>
          <Text style={[styles.statValue, { color: paceColor }]}>{formatNumber(paceValue)}</Text>
        </View>
        {live && projValue != null && (
          <View style={styles.stat}>
            <Text style={styles.statLabel}>PROJ</Text>
            <Text style={[styles.statValue, { color: Colors.text }]}>{formatNumber(projValue)}</Text>
          </View>
        )}
        {!pending && (
          <View style={styles.stat}>
            <Text style={styles.statLabel}>HIT%</Text>
            <Text style={styles.statValue}>{hitPct != null ? `${Math.round(hitPct)}%` : '—'}</Text>
          </View>
        )}
      </View>

      {progress != null && (
        <View style={styles.trackBarOuter}>
          <View style={[styles.trackBarFill, { width: `${progress}%`, backgroundColor: recColor }]} />
          <View style={[styles.trackBarMarker, { left: '50%' }]} />
        </View>
      )}

      <View style={styles.bottomRow}>
        <View style={styles.bottomItem}>
          <Ionicons name={matchTime ? "calendar-outline" : "time-outline"} size={11} color={Colors.textTertiary} />
          <Text style={styles.bottomText}>
            {matchTime || (elapsed != null ? `${elapsed}'` : (live ? 'LIVE' : '—'))}
          </Text>
        </View>
        <Text style={[styles.bottomText, { color: recColor, fontWeight: '800' }]}>{scriptType}</Text>
      </View>

      {hasGameScript && (
        <View style={[styles.gsBanner, { borderColor: gsColor + '44' }]}>
          <View style={[styles.gsBannerStripe, { backgroundColor: gsColor }]} />
          <View style={styles.gsBannerBody}>
            <View style={styles.gsBannerHeader}>
              <Ionicons name={gsIcon} size={13} color={gsColor} />
              <Text style={[styles.gsBannerLabel, { color: gsColor }]}>GAME SCRIPT</Text>
              <Text style={styles.gsBannerProb}>{Math.round(Number((gs as any)?.dominant_probability || 0) * 100)}%</Text>
            </View>
            <Text style={[styles.gsBannerTitle, { color: gsColor }]}>{(gs?.key_finding as string) || ''}</Text>
            {gsScenarios.length > 1 && (
              <View style={styles.gsBannerScenarios}>
                {gsScenarios.slice(0, 3).map((s: any, i: number) => (
                  <View key={i} style={styles.gsBannerChip}>
                    <Text style={styles.gsBannerChipName}>{s.name}</Text>
                    <Text style={[styles.gsBannerChipPct, { color: gsColor }]}>{Math.round((s.probability || 0) * 100)}%</Text>
                  </View>
                ))}
              </View>
            )}
            {(gs as any)?.expected_total_goals != null && (
              <Text style={styles.gsBannerSub}>Expected {(gs as any)?.expected_total_goals} total goals</Text>
            )}
          </View>
        </View>
      )}

      {live && !won && !lost && pick.sport === 'soccer' && onTrack && (
        <TouchableOpacity onPress={onTrack} style={styles.trackBtn} activeOpacity={0.7}>
          <Ionicons name="pulse" size={12} color={Colors.primary} />
          <Text style={styles.trackBtnText}>Track Live</Text>
        </TouchableOpacity>
      )}

      {showScoreLine && (
        <View style={styles.matchCtxBlock}>
          <Text style={styles.matchCtxLine} numberOfLines={1} ellipsizeMode="tail">
            {hasScore ? (
              <>{settled ? 'FT ' : 'LIVE '}{homeTeamName} {finalHome}–{finalAway} {awayTeamName}</>
            ) : (
              <>{homeTeamName} vs {awayTeamName}</>
            )}
            {hasActualPoss ? ` · ${Math.round(pick.homePoss!)}%/${Math.round(pick.awayPoss!)}%` : ''}
            {hasProjPoss && !hasActualPoss ? ` · Proj ${Math.round(pick.projHomePoss!)}%/${Math.round(pick.projAwayPoss!)}%` : ''}
            {pick.fixtureId != null ? ` · #${pick.fixtureId}` : ''}
          </Text>
        </View>
      )}

      {settled && (
        <View style={styles.storyBlock}>
          <View style={[styles.storyDot, { backgroundColor: won ? Colors.success : lost ? Colors.error : push ? Colors.push : Colors.dnp }]} />
          <Text style={styles.storyText} numberOfLines={2}>
            {buildSettledStory(pick, { won, lost, push, dnp })}
          </Text>
        </View>
      )}
    </TouchableOpacity>
  );
}

function buildSettledStory(
  pick: Pick,
  { won, lost, push, dnp }: { won: boolean; lost: boolean; push: boolean; dnp: boolean }
) {
  const line = typeof pick.line === 'number' ? pick.line : null;
  const actual = pick.actualValue ?? pick.currentValue ?? null;
  const proj = pick.projection ?? pick.projectedValue ?? null;
  const poss = pick.homePoss != null && pick.awayPoss != null
    ? `${Math.round(pick.homePoss)}%/${Math.round(pick.awayPoss)}%`
    : null;
  const unit = propUnit(pick.propType);

  let narrative = '';
  if (dnp) {
    narrative = 'Did not play — voided.';
  } else if (push) {
    narrative = 'Finished exactly on the line.';
  } else if (won && actual != null && line != null) {
    const margin = Math.abs(actual - line);
    narrative = `Beat the line by ${margin.toFixed(1)} ${unit}.`;
  } else if (lost && actual != null && line != null) {
    const margin = Math.abs(actual - line);
    narrative = `Fell short by ${margin.toFixed(1)} ${unit}.`;
  } else if (proj != null && line != null) {
    const edge = Math.abs(proj - line);
    narrative = `Pre-game edge was ${edge.toFixed(1)} ${unit}.`;
  }
  if (poss && (won || lost)) narrative += ` Match possession: ${poss}.`;
  return narrative;
}

// Hidden DOM card used for html2canvas capture on web.
function renderShareableCardHTML(
  pick: Pick,
  state: Record<string, any>
) {
  const {
    won, lost, push, dnp, live, pending, dir, isOver, recColor,
    nowValue, nowLabel, paceValue, paceLabel, paceColor, hitPct, lineValue,
    progress, hasScore, finalHome, finalAway, homeTeamName, awayTeamName,
    hasActualPoss, hasProjPoss, showScoreLine, propLabel, elapsed, venueText,
    matchTime, scriptType,
  } = state;
  const titleColor = won ? '#39FF14' : lost ? '#FF3B30' : '#FFFFFF';
  const badgeHTML = won
    ? `<div style="background:#39FF14;color:#000;padding:3px 8px;border-radius:6px;font-size:9px;font-weight:900">HIT</div>`
    : lost
    ? `<div style="background:#FF3B30;color:#000;padding:3px 8px;border-radius:6px;font-size:9px;font-weight:900">MISS</div>`
    : push
    ? `<div style="background:#0A84FF;color:#000;padding:3px 8px;border-radius:6px;font-size:9px;font-weight:900">PUSH</div>`
    : dnp
    ? `<div style="background:#FF9500;color:#000;padding:3px 8px;border-radius:6px;font-size:9px;font-weight:900">DNP</div>`
    : live
    ? `<div style="display:flex;align-items:center;background:rgba(255,59,48,0.16);border:1px solid rgba(255,59,48,0.45);padding:2px 6px;border-radius:6px"><span style="width:6px;height:6px;border-radius:3px;background:#FF3B30;margin-right:4px"></span><span style="color:#FF3B30;font-size:9px;font-weight:900">LIVE</span></div>`
    : pending
    ? `<div style="background:rgba(255,255,255,0.12);color:#fff;padding:3px 8px;border-radius:6px;font-size:9px;font-weight:900">PENDING</div>`
    : '';

  const nowColor = nowValue != null && lineValue != null
    ? ((isOver && nowValue > lineValue) || (!isOver && nowValue < lineValue) ? '#39FF14' : '#FF3B30')
    : '#FFFFFF';
  const paceColorHex = paceColor || '#FFFFFF';
  const hitColor = hitPct != null ? '#FFFFFF' : 'rgba(255,255,255,0.5)';

  const progressHTML = progress != null
    ? `<div style="position:relative;height:6px;background:rgba(255,255,255,0.08);border-radius:3px;margin-top:12px"><div style="position:absolute;left:0;top:0;bottom:0;border-radius:3px;width:${progress}%;background:${recColor}"></div><div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:2px;background:rgba(255,255,255,0.6)"></div></div>`
    : '';

  const matchCtxHTML = showScoreLine
    ? `<div style="margin-top:10px;font-size:11px;color:rgba(255,255,255,0.55);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
        ${hasScore ? `${won || lost || push ? 'FT ' : 'LIVE '}${homeTeamName} ${finalHome}–${finalAway} ${awayTeamName}` : `${homeTeamName} vs ${awayTeamName}`}
        ${hasActualPoss ? ` · ${Math.round(pick.homePoss!)}%/${Math.round(pick.awayPoss!)}%` : ''}
        ${hasProjPoss && !hasActualPoss ? ` · Proj ${Math.round(pick.projHomePoss!)}%/${Math.round(pick.projAwayPoss!)}%` : ''}
        ${pick.fixtureId != null ? ` · #${pick.fixtureId}` : ''}
      </div>`
    : '';

  const story = buildSettledStory(pick, { won, lost, push, dnp });
  const storyHTML = (won || lost || push || dnp)
    ? `<div style="display:flex;align-items:flex-start;margin-top:10px"><div style="width:6px;height:6px;border-radius:3px;background:${won ? '#39FF14' : lost ? '#FF3B30' : push ? '#0A84FF' : '#FF9500'};margin-top:5px;margin-right:6px;flex-shrink:0"></div><div style="font-size:11px;color:rgba(255,255,255,0.65);line-height:1.35">${story}</div></div>`
    : '';

  const photoUrl = pick.ownerPlayerPhoto || '';
  const teamLogoUrl = pick.ownerTeamLogo || '';
  const photoHTML = photoUrl
    ? `<img src="${photoUrl}" style="width:48px;height:48px;border-radius:24px;border:2px solid rgba(57,255,20,0.35);object-fit:cover;background:#1A1A1A" crossorigin="anonymous" />`
    : `<div style="width:48px;height:48px;border-radius:24px;background:#1A1A1A;border:2px solid rgba(57,255,20,0.35);display:flex;align-items:center;justify-content:center;color:#39FF14;font-size:20px;font-weight:800">${pick.playerName?.charAt(0) || '?'}</div>`;
  const teamLogoHTML = teamLogoUrl ? `<img src="${teamLogoUrl}" style="width:14px;height:14px;margin-right:5px;object-fit:contain" crossorigin="anonymous" />` : '';

  return `
    <div style="width:340px;background:#0F0F0F;border-radius:16px;border:1px solid rgba(57,255,20,0.18);padding:14px;box-shadow:0 4px 12px rgba(0,0,0,0.4)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <div style="display:flex;align-items:center;flex:1;min-width:0">
          ${photoHTML}
          <div style="margin-left:12px;flex:1;min-width:0">
            <div style="color:${titleColor};font-size:17px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${pick.playerName}</div>
            <div style="display:flex;align-items:center;margin-top:3px">
              ${teamLogoHTML}
              <div style="color:rgba(255,255,255,0.5);font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${pick.teamName || 'Team'} · ${venueText}</div>
            </div>
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-left:10px">
          ${badgeHTML}
        </div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        ${!pending ? `<div style="text-align:center;flex:1"><div style="color:rgba(255,255,255,0.4);font-size:9px;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">${nowLabel}</div><div style="color:${nowColor};font-size:18px;font-weight:800">${formatNumber(nowValue)}</div></div>` : ''}
        <div style="text-align:center;flex:1"><div style="color:rgba(255,255,255,0.4);font-size:9px;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">LINE</div><div style="color:#fff;font-size:18px;font-weight:800">${formatNumber(lineValue)}</div></div>
        <div style="text-align:center;flex:1"><div style="color:rgba(255,255,255,0.4);font-size:9px;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">${paceLabel}</div><div style="color:${paceColorHex};font-size:18px;font-weight:800">${formatNumber(paceValue)}</div></div>
        ${!pending ? `<div style="text-align:center;flex:1"><div style="color:rgba(255,255,255,0.4);font-size:9px;font-weight:700;letter-spacing:0.5px;margin-bottom:3px">HIT%</div><div style="color:${hitColor};font-size:18px;font-weight:800">${hitPct != null ? `${Math.round(hitPct)}%` : '—'}</div></div>` : ''}
      </div>
      ${progressHTML}
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px">
        <div style="display:flex;align-items:center;color:rgba(255,255,255,0.35);font-size:11px">
          <span style="margin-right:4px">${matchTime ? '📅' : '⏱'}</span>${matchTime || (elapsed != null ? `${elapsed}'` : (live ? 'LIVE' : '—'))}
        </div>
        <div style="color:${recColor || 'rgba(255,255,255,0.5)'};font-size:11px;font-weight:800">${scriptType || propLabel}</div>
      </div>
      ${matchCtxHTML}
      ${storyHTML}
      <div style="margin-top:12px;text-align:center;color:rgba(255,255,255,0.3);font-size:10px;font-weight:700;letter-spacing:0.5px">REVERSE PICKS</div>
    </div>
  `;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#0F0F0F',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.18)',
    padding: 12,
    marginBottom: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.35,
    shadowRadius: 6,
    elevation: 5,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.45)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.35)' },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  identity: { flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 10 },
  photo: {
    width: 44,
    height: 44,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: 'rgba(57,255,20,0.35)',
    backgroundColor: '#1A1A1A',
  },
  photoPlaceholder: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: '#1A1A1A',
    borderWidth: 1.5,
    borderColor: 'rgba(57,255,20,0.35)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  photoOverlay: {
    position: 'absolute',
    left: 0,
    top: 0,
  },
  photoInitial: { color: Colors.primary, fontSize: 18, fontWeight: '800' },
  nameBlock: { marginLeft: 10, flex: 1 },
  playerName: { color: '#fff', fontSize: 16, fontWeight: '800', letterSpacing: -0.3 },
  teamRow: { flexDirection: 'row', alignItems: 'center', marginTop: 3 },
  teamLogo: { width: 14, height: 14, marginRight: 5 },
  teamVenue: { color: Colors.textSecondary, fontSize: 11.5, fontWeight: '600' },
  actions: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  shareBtn: {
    width: 28,
    height: 28,
    borderRadius: 8,
    backgroundColor: 'rgba(57,255,20,0.10)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 6,
  },
  badgeText: { color: '#000', fontSize: 9, fontWeight: '900' },
  badgeLive: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,59,48,0.16)',
    borderWidth: 1,
    borderColor: 'rgba(255,59,48,0.45)',
    paddingHorizontal: 6,
    paddingVertical: 2.5,
    borderRadius: 6,
  },
  pulseDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.error,
    marginRight: 4,
  },
  badgeLiveText: { color: Colors.error, fontSize: 9, fontWeight: '900' },
  statsRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  stat: { alignItems: 'center', flex: 1 },
  statLabel: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.5,
    marginBottom: 3,
  },
  statValue: { fontSize: 18, fontWeight: '800', color: Colors.text },
  trackBarOuter: {
    position: 'relative',
    height: 6,
    backgroundColor: 'rgba(255,255,255,0.08)',
    borderRadius: 3,
    marginTop: 10,
  },
  trackBarFill: {
    position: 'absolute',
    left: 0,
    top: 0,
    bottom: 0,
    borderRadius: 3,
  },
  trackBarMarker: {
    position: 'absolute',
    top: -2,
    bottom: -2,
    width: 2,
    backgroundColor: 'rgba(255,255,255,0.6)',
  },
  bottomRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 10,
  },
  bottomItem: { flexDirection: 'row', alignItems: 'center' },
  bottomText: { color: Colors.textTertiary, fontSize: 11, marginLeft: 4, fontWeight: '600' },
  matchCtxBlock: { marginTop: 10 },
  matchCtxLine: {
    color: 'rgba(255,255,255,0.75)',
    fontSize: 11,
    fontWeight: '600',
  },
  storyBlock: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginTop: 10,
  },
  storyDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    marginTop: 5,
    marginRight: 6,
  },
  storyText: {
    color: 'rgba(255,255,255,0.80)',
    fontSize: 11,
    lineHeight: 16,
    flex: 1,
  },
  trackBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 10,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: 'rgba(57,255,20,0.10)',
    borderWidth: 1,
    borderColor: 'rgba(57,255,20,0.25)',
  },
  trackBtnText: {
    color: Colors.primary,
    fontSize: 11,
    fontWeight: '800',
    marginLeft: 5,
  },
  gsBanner: {
    flexDirection: 'row',
    marginTop: 10,
    borderRadius: 10,
    borderWidth: 1,
    backgroundColor: '#111',
    overflow: 'hidden',
  },
  gsBannerStripe: {
    width: 4,
  },
  gsBannerBody: {
    flex: 1,
    padding: 10,
  },
  gsBannerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 4,
  },
  gsBannerLabel: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  gsBannerProb: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 10,
    fontWeight: '800',
    marginLeft: 'auto',
  },
  gsBannerTitle: {
    fontSize: 13,
    fontWeight: '800',
    marginBottom: 5,
  },
  gsBannerScenarios: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginBottom: 5,
  },
  gsBannerChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 5,
    backgroundColor: 'rgba(255,255,255,0.08)',
  },
  gsBannerChipName: {
    color: 'rgba(255,255,255,0.65)',
    fontSize: 10,
    fontWeight: '600',
  },
  gsBannerChipPct: {
    fontSize: 10,
    fontWeight: '800',
  },
  gsBannerSub: {
    color: 'rgba(255,255,255,0.55)',
    fontSize: 10,
    fontWeight: '600',
  },
});
