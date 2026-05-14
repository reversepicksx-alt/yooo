import React, { useState, useCallback, useRef } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity,
  ActivityIndicator, Alert, Platform, RefreshControl,
  Modal, ScrollView, Pressable,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from '@react-navigation/native';
import * as Haptics from 'expo-haptics';
import { Ionicons } from '@expo/vector-icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import ReanimatedSwipeable, {
  SwipeableMethods,
} from 'react-native-gesture-handler/ReanimatedSwipeable';
import Reanimated, {
  SharedValue,
  useAnimatedStyle,
  interpolate,
  Extrapolation,
} from 'react-native-reanimated';
import Colors from '@/constants/colors';
import { listPicks, deletePick, fetchPickAnalysis, Pick } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';

type Tab = 'live' | 'history';
type SportKey = 'soccer' | 'mlb' | 'cs2';

const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'Pass Attempts', shots: 'Shots', shots_on_target: 'SOT',
  goals: 'Goals', assists: 'Assists', key_passes: 'Key Passes',
  tackles: 'Tackles', saves: 'Saves', dribbles: 'Dribbles', crosses: 'Crosses',
  interceptions: 'Interceptions', blocks: 'Blocks', fouls_drawn: 'Fouls Drawn',
  fouls_committed: 'Fouls', clearances: 'Clearances', duels_won: 'Duels Won',
  yellow_cards: 'Yellow Cards', shots_assisted: 'Shot Assists', passes: 'Passes',
};

const MLB_PROP_SET = new Set([
  'pitcher_strikeouts', 'innings_pitched', 'hits_allowed', 'earned_runs',
  'walks_allowed', 'pitches_thrown', 'batters_faced', 'hits', 'home_runs',
  'rbi', 'walks', 'strikeouts', 'runs', 'total_bases', 'stolen_bases',
  'doubles', 'plate_appearances', 'hitter_fantasy_points',
]);

const CS2_PROP_SET = new Set([
  'maps_1_2_kills', 'maps_1_2_headshots', 'maps_1_2_deaths',
  'maps_1_2_assists', 'maps_1_2_adr', 'map3_kills', 'map3_headshots',
  'map3_deaths', 'map3_assists', 'map3_adr', 'kills', 'headshots',
  'deaths', 'adr', 'mvps', 'rating', 'headshot_pct',
]);

function getSport(p: Pick): SportKey {
  if (p.sport === 'mlb' || MLB_PROP_SET.has(p.propType)) return 'mlb';
  if (p.sport === 'cs2' || CS2_PROP_SET.has(p.propType)) return 'cs2';
  return 'soccer';
}

const SPORT_META: Record<SportKey, { label: string; icon: string }> = {
  soccer: { label: 'Soccer', icon: '⚽' },
  mlb:    { label: 'MLB',    icon: '⚾' },
  cs2:    { label: 'CS2',    icon: '🎮' },
};

function isLive(p: Pick) {
  return p.matchStatus === 'live' || p.status === 'live' || p.status === 'pending' || (!p.status && !['hit','miss','push','won','lost'].includes(p.result));
}
function isSettled(p: Pick) {
  return p.matchStatus === 'final' || p.status === 'settled' || ['hit','miss','push','won','lost'].includes(p.result);
}
function pickWon(p: Pick) {
  return p.result === 'hit' || p.result === 'won' || p.status === 'won';
}
function pickLost(p: Pick) {
  return p.result === 'miss' || p.result === 'lost' || p.status === 'lost';
}
function pickPush(p: Pick) {
  return p.result === 'push';
}

function SwipeLeftAction({
  drag,
  onPress,
}: {
  drag: SharedValue<number>;
  onPress: () => void;
}) {
  // The action sits behind the card on the LEFT side. As the card is dragged
  // to the right, `drag` increases from 0 → +N. We grow the icon/text in.
  const animatedStyle = useAnimatedStyle(() => {
    const scale = interpolate(drag.value, [0, 60, 120], [0.5, 0.9, 1], Extrapolation.CLAMP);
    const opacity = interpolate(drag.value, [0, 30, 80], [0, 0.6, 1], Extrapolation.CLAMP);
    return { transform: [{ scale }], opacity };
  });

  // WEB: render a native <button> so the click is handled by the browser
  // directly — bypasses every quirk of react-native-web's Pressable, RNGH's
  // RectButton, and ReanimatedSwipeable's gesture detection. Tap-to-delete
  // is now identical to clicking any other HTML button on the page.
  if (Platform.OS === 'web') {
    return (
      <View style={styles.swipeAction} pointerEvents="box-none">
        {/* @ts-ignore react-native-web accepts raw DOM elements when wrapped
            — we deliberately use a real <button> for reliability. */}
        <button
          type="button"
          onClick={(e: React.MouseEvent) => {
            e.preventDefault();
            e.stopPropagation();
            onPress();
          }}
          style={{
            all: 'unset',
            cursor: 'pointer',
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 3,
          }}
          aria-label="Delete pick"
        >
          <Reanimated.View style={[styles.swipeActionInner, animatedStyle]} pointerEvents="none">
            <Ionicons name="trash" size={22} color="#fff" />
            <Text style={styles.swipeActionText}>DELETE</Text>
          </Reanimated.View>
        </button>
      </View>
    );
  }

  // NATIVE iOS/Android: TouchableOpacity from react-native works fine here
  // because the swipe gesture is in its idle state (open) and isn't actively
  // capturing touches. Earlier breakage was specifically a web rendering
  // path issue, not a native gesture-conflict issue.
  return (
    <TouchableOpacity
      style={styles.swipeAction}
      onPress={onPress}
      activeOpacity={0.85}
      accessibilityRole="button"
      accessibilityLabel="Delete pick"
    >
      <Reanimated.View style={[styles.swipeActionInner, animatedStyle]}>
        <Ionicons name="trash" size={22} color="#fff" />
        <Text style={styles.swipeActionText}>DELETE</Text>
      </Reanimated.View>
    </TouchableOpacity>
  );
}

function SwipeableRow({
  onDelete,
  children,
}: {
  onDelete: () => void;
  children: React.ReactNode;
}) {
  const swipeRef = useRef<SwipeableMethods | null>(null);

  // WEB: don't wrap in a swipeable at all. The browser doesn't have native
  // swipe-to-delete UX, the gesture-handler web shim is flaky, and we now
  // surface the trash bin directly inside PickCard for web users. Just
  // render children straight through.
  if (Platform.OS === 'web') {
    return <>{children}</>;
  }

  // NATIVE iOS/Android: keep the iOS-Mail-style swipe-to-reveal action.
  // Tapping DELETE confirms; swiping closed dismisses without action.
  return (
    <ReanimatedSwipeable
      ref={swipeRef}
      friction={1.5}
      leftThreshold={40}
      dragOffsetFromLeftEdge={6}
      overshootLeft={false}
      enableTrackpadTwoFingerGesture
      renderLeftActions={(_progress, drag) => (
        <SwipeLeftAction
          drag={drag}
          onPress={() => {
            swipeRef.current?.close();
            onDelete();
          }}
        />
      )}
      onSwipeableWillOpen={() => {
        try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
      }}
    >
      {children}
    </ReanimatedSwipeable>
  );
}

function PickCard({ pick, onDelete }: { pick: Pick; onDelete?: () => void }) {
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const live = isLive(pick);

  const push = pickPush(pick);
  const statusColor = won ? Colors.success : lost ? Colors.error : push ? Colors.push : Colors.textTertiary;

  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ') || '—';
  const venueStr = pick.venue ? pick.venue.toUpperCase() : '';
  const posLabel = [pick.position, pick.role].filter(Boolean).join(' · ');

  const settled = won || lost || push;
  const nowValue = settled
    ? (pick.actualValue ?? (pick as { currentValue?: number | null }).currentValue ?? null)
    : ((pick as { currentValue?: number | null }).currentValue ?? pick.actualValue ?? null);
  const projValue = pick.projection ?? (pick as { projectedValue?: number | null }).projectedValue ?? null;
  const livePace = (pick as { pace?: number | null }).pace;
  const matchStatus = (pick as { matchStatus?: string }).matchStatus;
  const hasLiveData = matchStatus === 'live' || (livePace != null && livePace > 0) || nowValue != null;
  const paceValue = settled ? projValue : (livePace != null && livePace > 0 ? livePace : projValue);
  const hitPct = (pick as { hitPct?: number | null }).hitPct ?? (pick as { hitRate?: number | null }).hitRate ?? (pick as { winRate?: number | null }).winRate;
  const lineValue = typeof pick.line === 'number' ? pick.line : null;

  // Always show OVER or UNDER — never PASS. For historical PASS picks,
  // use the flipped direction (fade the model: lean OVER → show UNDER, lean UNDER → show OVER).
  const rec = pick.recommendation;
  const effectiveDir: 'OVER' | 'UNDER' | null =
    rec === 'OVER' ? 'OVER'
    : rec === 'UNDER' ? 'UNDER'
    : projValue != null && lineValue != null
      ? (projValue < lineValue ? 'OVER' : 'UNDER')   // flipped: fade the model's lean
      : null;
  const isOver = effectiveDir === 'OVER';
  const isUnder = effectiveDir === 'UNDER';
  const recColor = isOver ? Colors.primary : isUnder ? Colors.error : Colors.textSecondary;

  const trackValue = nowValue ?? paceValue ?? null;
  const trackDistance = lineValue != null && lineValue > 0 && trackValue != null
    ? Math.max(0, Math.min(2, trackValue / lineValue))
    : null;
  const trackFillPct = trackDistance != null
    ? Math.max(6, Math.min(94, (trackDistance / 2) * 100))
    : null;
  const trackMarkerPct = 50;
  const progressFillPct = lineValue != null && trackValue != null
    ? Math.max(0, Math.min(100, (trackValue / Math.max(lineValue * 2, 1)) * 100))
    : null;
  const trackColor = won
    ? Colors.success
    : lost
    ? Colors.error
    : push
    ? Colors.push
    : trackValue != null && lineValue != null
    ? ((isOver && trackValue > lineValue) || (isUnder && trackValue < lineValue) ? Colors.success : Colors.error)
    : Colors.textSecondary;
  const paceColor = trackValue != null ? Colors.primary : Colors.textSecondary;

  const nowLabel = (won || lost || push) ? 'FINAL' : (hasLiveData ? 'NOW' : null);
  const paceLabel = settled ? 'PROJ' : (livePace != null && livePace > 0 ? 'PACE' : 'PROJ');

  // Determine home/away team labels.
  // PREFERRED path: backend resolved fixture → both `homeTeam` and `awayTeam` set.
  // FALLBACK path: legacy picks may only have `teamName`/`opponentName`/`venue`/`matchScore`.
  //   We orient using `venue` ('home' = subject team is home; 'away' = subject is away).
  //   If `venue` is missing/unknown we cannot trust orientation, so we hide the score line
  //   (prevents showing wrong winner color or flipping goals).
  const venueLower = (pick.venue || '').toLowerCase();
  const venueKnown = venueLower === 'home' || venueLower === 'away';
  const hasFixtureNames = !!(pick.homeTeam && pick.awayTeam);
  const homeTeamName = pick.homeTeam
    || (venueKnown ? (venueLower === 'away' ? pick.opponentName : pick.teamName) : '')
    || '';
  const awayTeamName = pick.awayTeam
    || (venueKnown ? (venueLower === 'away' ? pick.teamName : pick.opponentName) : '')
    || '';
  // Resolve final goals — prefer explicit fields, otherwise parse legacy `matchScore` string.
  // Note: `matchScore` is stored player-perspective ("subject_goals-opp_goals"), NOT home-away.
  // We can only re-orient correctly when venue is known.
  let finalHome: number | null | undefined = pick.finalHomeGoals;
  let finalAway: number | null | undefined = pick.finalAwayGoals;
  if ((finalHome == null || finalAway == null)
      && venueKnown
      && typeof pick.matchScore === 'string') {
    const m = pick.matchScore.match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if (m) {
      const subject = parseInt(m[1], 10);
      const opp = parseInt(m[2], 10);
      if (!Number.isNaN(subject) && !Number.isNaN(opp)) {
        if (venueLower === 'away') {
          finalHome = opp; finalAway = subject;
        } else {
          finalHome = subject; finalAway = opp;
        }
      }
    }
  }
  const homePoss = pick.homePoss;
  const awayPoss = pick.awayPoss;
  const projHomePoss = pick.projHomePoss;
  const projAwayPoss = pick.projAwayPoss;
  const hasActualPoss = homePoss != null && awayPoss != null;
  const hasProjPoss = projHomePoss != null && projAwayPoss != null;
  // Render the match-context block when we have either a trustable score OR a
  // possession projection to compare against. Projected possession alone is
  // enough to surface the "model said 60% — real was 48%" edge story.
  const trustOrient = hasFixtureNames || venueKnown;
  const showScoreLine = trustOrient && (
    ((settled || (live && hasLiveData)) && finalHome != null && finalAway != null)
    || hasActualPoss
    || hasProjPoss
  );
  const haveScoreNumbers = finalHome != null && finalAway != null;
  const subjectWon = settled && finalHome != null && finalAway != null && venueKnown && (
    (venueLower === 'home' && finalHome > finalAway) ||
    (venueLower === 'away' && finalAway > finalHome)
  );
  const subjectLost = settled && finalHome != null && finalAway != null && venueKnown && (
    (venueLower === 'home' && finalHome < finalAway) ||
    (venueLower === 'away' && finalAway < finalHome)
  );
  const homeColor = subjectWon && venueLower === 'home' ? Colors.success
    : subjectLost && venueLower === 'home' ? Colors.error
    : Colors.text;
  const awayColor = subjectWon && venueLower === 'away' ? Colors.success
    : subjectLost && venueLower === 'away' ? Colors.error
    : Colors.text;

  return (
    <View style={[styles.card, won && styles.cardWon, lost && styles.cardLost]}>
      {/* Row 1: player name left | badge right */}
      <View style={styles.cardTopRow}>
        <Text style={styles.cardPlayer} numberOfLines={1}>{pick.playerName}</Text>
        <View style={styles.cardRight}>
          {live && !won && !lost && hasLiveData && (
            <View style={styles.liveBadge}>
              <View style={styles.liveDot} />
              <Text style={styles.liveText}>LIVE</Text>
            </View>
          )}
          {live && !won && !lost && !hasLiveData && (
            <View style={styles.pendingBadge}>
              <Ionicons name="time-outline" size={9} color={Colors.textSecondary} />
              <Text style={styles.pendingText}>PENDING</Text>
            </View>
          )}
          {won && (
            <View style={styles.wonBadge}>
              <Ionicons name="checkmark" size={9} color="#000" />
              <Text style={styles.wonText}>HIT</Text>
            </View>
          )}
          {lost && (
            <View style={styles.lostBadge}>
              <Ionicons name="close" size={9} color={Colors.error} />
              <Text style={styles.lostText}>MISS</Text>
            </View>
          )}
          {push && !won && !lost && (
            <View style={styles.pushBadge}>
              <Ionicons name="remove-outline" size={9} color={Colors.push} />
              <Text style={styles.pushText}>PUSH</Text>
            </View>
          )}
        </View>
      </View>

      {/* Row 2: meta + pick pill left | inline stats right */}
      <View style={styles.cardRow2}>
        <View style={styles.cardRow2Left}>
          <Text style={styles.cardMeta} numberOfLines={1}>
            {[pick.teamName, posLabel || null, pick.opponentName
              ? (pick.venue === 'away' ? `@ ${pick.opponentName}` : `vs ${pick.opponentName}`)
              : null]
              .filter(Boolean).join(' · ')}
          </Text>
          {effectiveDir && (
            <View style={styles.pickRow}>
              <View style={[styles.recPill, { backgroundColor: isOver ? Colors.successDim : isUnder ? Colors.errorDim : Colors.cardSecondary }]}>
                <Text style={[styles.recPillText, { color: recColor }]}>{effectiveDir}</Text>
              </View>
              <Text style={styles.pickDetail} numberOfLines={1}>
                {propLabel} · {pick.line}
              </Text>
              {(() => {
                // Confidence badge — visible on every live + settled card.
                // Color comes from the level the model assigned at pick-time.
                //
                // The backend stores confidenceScore as 0-100 (real model
                // output) PLUS a categorical level word. When the AI fails to
                // produce a real score the backend falls back to exactly 50 as
                // a placeholder — showing "50%" then is misleading because it
                // looks like a coin-flip when really it just means "no score
                // available". So when the score is the 50 placeholder (or
                // missing entirely) we display the level word only; otherwise
                // we show both ("85% STRONG").
                const confRaw = typeof pick.confidence === 'number' ? pick.confidence : null;
                const lvlRaw = (pick.confidenceLevel || '').trim();
                if (confRaw == null && !lvlRaw) return null;
                const lvl = lvlRaw.toLowerCase();
                const confColor =
                  lvl.startsWith('strong') || lvl.startsWith('high') ? Colors.success
                  : lvl.startsWith('weak') || lvl.startsWith('low') ? Colors.textTertiary
                  : Colors.primary; // medium / unknown
                const confPct = confRaw == null
                  ? null
                  : Math.round(confRaw > 1 ? confRaw : confRaw * 100);
                const isPlaceholder = confPct === 50; // backend default
                const showPct = confPct != null && !isPlaceholder;
                const confLabel = showPct
                  ? `${confPct}%${lvlRaw ? ' ' + lvlRaw.toUpperCase() : ''}`
                  : lvlRaw.toUpperCase();
                if (!confLabel) return null;
                return (
                  <View style={[styles.confBadge, { borderColor: confColor }]}>
                    <Text style={[styles.confBadgeText, { color: confColor }]} numberOfLines={1}>
                      {confLabel}
                    </Text>
                  </View>
                );
              })()}
              {pick.coinFlip && (
                <View style={styles.coinFlipBadge}>
                  <Text style={styles.coinFlipText}>~</Text>
                </View>
              )}
            </View>
          )}
        </View>
        {/* Inline stats: NOW (if live/settled) and PROJ */}
        <View style={styles.inlineStats}>
          {nowLabel && nowValue != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: trackColor }]}>{Number(nowValue).toFixed(0)}</Text>
              <Text style={styles.inlineLbl}>{nowLabel}</Text>
            </View>
          )}
          {paceValue != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: Colors.primary }]}>{Number(paceValue).toFixed(0)}</Text>
              <Text style={styles.inlineLbl}>{paceLabel}</Text>
            </View>
          )}
          {hitPct != null && (
            <View style={styles.inlineStat}>
              <Text style={[styles.inlineVal, { color: Colors.textSecondary }]}>{Math.round(hitPct)}%</Text>
              <Text style={styles.inlineLbl}>HIT</Text>
            </View>
          )}
        </View>
      </View>

      {/* Thin progress bar — only visible when live/settled with real data */}
      {lineValue != null && nowValue != null && (() => {
        const fillPct = Math.max(0, Math.min(100, (nowValue / Math.max(lineValue * 2, 1)) * 100));
        return (
          <View style={styles.trackBarOuter}>
            <View style={[styles.trackBarFill, { width: `${fillPct}%`, backgroundColor: trackColor }]} />
            <View style={[styles.trackBarMarker, { left: `${trackMarkerPct}%` }]} />
          </View>
        );
      })()}

      {/* Match score + possession block — clearly labels home (left) vs away
          (right). Shows actual final/live possession when available AND the
          model's pre-match projected possession on its own line — so the user
          can spot when the projection was off (a directional edge signal). */}
      {showScoreLine && (
        <View style={styles.matchCtxBlock}>
          <View style={styles.matchCtxScoreRow}>
            {haveScoreNumbers ? (
              <>
                <Text style={styles.scoreLabel}>{settled ? 'FT' : 'LIVE'}</Text>
                <Text style={styles.scoreText} numberOfLines={1} ellipsizeMode="middle">
                  <Text style={[styles.scoreTeamName, { color: homeColor }]}>{homeTeamName || 'Home'}</Text>
                  <Text style={[styles.scoreNum, { color: homeColor }]}>{`  ${finalHome}`}</Text>
                  <Text style={styles.scoreDash}>{'  –  '}</Text>
                  <Text style={[styles.scoreNum, { color: awayColor }]}>{`${finalAway}  `}</Text>
                  <Text style={[styles.scoreTeamName, { color: awayColor }]}>{awayTeamName || 'Away'}</Text>
                </Text>
              </>
            ) : (
              // No score yet (pre-match / pending): still show team labels so
              // the user can read the possession figures below.
              <Text style={styles.scoreText} numberOfLines={1} ellipsizeMode="middle">
                <Text style={[styles.scoreTeamName, { color: Colors.textSecondary }]}>{homeTeamName || 'Home'}</Text>
                <Text style={styles.scoreDash}>{'   vs   '}</Text>
                <Text style={[styles.scoreTeamName, { color: Colors.textSecondary }]}>{awayTeamName || 'Away'}</Text>
              </Text>
            )}
          </View>
          {(hasActualPoss || hasProjPoss) && (() => {
            // Compact single line: actual + projection + delta together
            // (was 2 stacked lines — collapsed to save vertical space).
            let deltaTag: { color: string; label: string } | null = null;
            if (hasActualPoss && hasProjPoss) {
              const delta = Math.abs((projHomePoss as number) - (homePoss as number));
              const color = delta <= 3 ? Colors.success : delta >= 8 ? Colors.error : Colors.textSecondary;
              deltaTag = { color, label: `Δ${delta.toFixed(0)}` };
            }
            return (
              <View style={styles.projPossRow}>
                {hasActualPoss && (
                  <Text style={styles.possText} numberOfLines={1}>
                    {settled ? 'Poss' : 'Live'} {homePoss}/{awayPoss}
                  </Text>
                )}
                {hasProjPoss && (
                  <Text style={styles.projPossText} numberOfLines={1}>
                    Proj {Math.round(projHomePoss as number)}/{Math.round(projAwayPoss as number)}
                  </Text>
                )}
                {deltaTag && (
                  <Text style={[styles.projDeltaText, { color: deltaTag.color }]}>{deltaTag.label}</Text>
                )}
              </View>
            );
          })()}
        </View>
      )}

      {/* WEB-ONLY trash bin in the bottom-right corner. We render a real
          HTML <button> so the click is handled by the browser directly —
          no react-native-web Pressable, no gesture handler, no synthetic
          event system. stopPropagation prevents the card's outer Pressable
          (analysis modal) from firing on the same click. */}
      {Platform.OS === 'web' && onDelete && (
        <View style={styles.cardFooterWeb}>
          {/* @ts-ignore raw DOM button is intentional for click reliability */}
          <button
            type="button"
            onClick={(e: React.MouseEvent) => {
              e.preventDefault();
              e.stopPropagation();
              onDelete();
            }}
            // Stop pointer/mouse/touch events at the start as well, so the
            // parent Pressable (for the analysis modal) never starts a press
            // sequence on this trash button. Without these, react-native-web's
            // Pressable can still fire onPress because it uses pointer events
            // which fire before click.
            onPointerDown={(e: React.PointerEvent) => e.stopPropagation()}
            onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
            onTouchStart={(e: React.TouchEvent) => e.stopPropagation()}
            style={{
              all: 'unset',
              cursor: 'pointer',
              padding: '6px 8px',
              borderRadius: 6,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
            }}
            aria-label="Delete pick"
            title="Delete pick"
          >
            <Ionicons name="trash-outline" size={14} color={Colors.error} />
          </button>
        </View>
      )}
    </View>
  );
}

function RecordBar({ picks }: { picks: Pick[] }) {
  const hits = picks.filter(pickWon).length;
  const misses = picks.filter(pickLost).length;
  const pending = picks.filter(isLive).length;
  const settled = hits + misses;
  const winPct = settled > 0 ? Math.round((hits / settled) * 100) : null;

  let streak = 0;
  const sorted = [...picks].sort((a, b) =>
    new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime()
  );
  for (const p of sorted) {
    if (pickWon(p)) streak++;
    else if (pickLost(p)) break;
  }

  return (
    <View style={styles.recordBar}>
      <Text style={styles.recordLabel}>YOUR RECORD</Text>
      <View style={styles.recordStats}>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.success }]}>{hits}</Text>
          <Text style={styles.recordKey}>HITS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.error }]}>{misses}</Text>
          <Text style={styles.recordKey}>MISS</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.textSecondary }]}>{pending}</Text>
          <Text style={styles.recordKey}>LIVE</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.primary }]}>
            {winPct != null ? `${winPct}%` : '—'}
          </Text>
          <Text style={styles.recordKey}>WIN%</Text>
        </View>
        <View style={styles.recordStat}>
          <Text style={[styles.recordVal, { color: Colors.accent }]}>
            {streak > 0 ? `${streak}W` : '—'}
          </Text>
          <Text style={styles.recordKey}>STRK</Text>
        </View>
      </View>
      <View style={styles.progressWrap}>
        <View style={styles.progressTrack}>
          <View
            style={[
              styles.progressFill,
              { width: `${picks.length > 0 ? Math.max(8, Math.min(100, (settled / picks.length) * 100)) : 0}%` },
            ]}
          />
        </View>
        <Text style={styles.progressText}>
          {settled}/{picks.length} settled
        </Text>
      </View>
    </View>
  );
}

function renderAnalysisBlocks(text: string, rec: string) {
  const isOver = rec === 'OVER';
  const isUnder = rec === 'UNDER';
  const recColor = isOver ? Colors.success : isUnder ? Colors.error : Colors.textSecondary;
  const paragraphs = text.split(/\n\n+/).filter(p => p.trim());
  const blocks: React.ReactElement[] = [];
  for (let i = 0; i < paragraphs.length; i++) {
    const para = paragraphs[i];
    const m = para.match(/^\*\*([^*]+)\*\*\s*([\s\S]*)/);
    if (m) {
      const section = m[1].trim();
      const body = m[2].trim().replace(/\*\*/g, '');
      if (section === 'Analysis') continue;
      if (section === 'Verdict') {
        blocks.push(
          <View key={i} style={mStyles.aiVerdictBlock}>
            <View style={[mStyles.aiVerdictPill, { backgroundColor: isOver ? 'rgba(57,255,20,0.12)' : 'rgba(255,59,48,0.12)' }]}>
              <Text style={[mStyles.aiVerdictLabel, { color: recColor }]}>VERDICT</Text>
            </View>
            <Text style={mStyles.aiVerdictText}>{body}</Text>
          </View>
        );
        continue;
      }
      if (section === 'TL;DR') {
        blocks.push(
          <View key={i} style={mStyles.aiTldrBlock}>
            <Text style={mStyles.aiTldrText}>{body}</Text>
          </View>
        );
        continue;
      }
      blocks.push(
        <View key={i} style={mStyles.aiSection}>
          <Text style={mStyles.aiSectionTitle}>{section.toUpperCase()}</Text>
          {body ? <Text style={mStyles.aiSectionBody}>{body}</Text> : null}
        </View>
      );
    } else {
      const plain = para.replace(/\*\*/g, '').trim();
      if (plain) blocks.push(<Text key={i} style={mStyles.aiSectionBody}>{plain}</Text>);
    }
  }
  return blocks;
}

function SportSectionHeader({
  sport, picks, expanded, onToggle,
}: {
  sport: SportKey; picks: Pick[]; expanded: boolean; onToggle: () => void;
}) {
  const { label, icon } = SPORT_META[sport];
  const hits   = picks.filter(pickWon).length;
  const misses = picks.filter(pickLost).length;
  const pushes = picks.filter(pickPush).length;
  const total  = hits + misses;
  const winPct = total > 0 ? Math.round((hits / total) * 100) : null;
  const pctColor = winPct == null ? Colors.textTertiary
    : winPct >= 60 ? Colors.success
    : winPct >= 50 ? Colors.primary
    : Colors.error;

  return (
    <TouchableOpacity
      onPress={onToggle}
      activeOpacity={0.75}
      style={secStyles.header}
    >
      <View style={secStyles.headerLeft}>
        <Text style={secStyles.headerIcon}>{icon}</Text>
        <Text style={secStyles.headerLabel}>{label}</Text>
        <View style={secStyles.countPill}>
          <Text style={secStyles.countText}>{picks.length}</Text>
        </View>
      </View>
      <View style={secStyles.headerRight}>
        <View style={secStyles.headerStats}>
          <Text style={[secStyles.statNum, { color: Colors.success }]}>
            {hits}<Text style={secStyles.statLbl}>H</Text>
          </Text>
          <Text style={secStyles.statDiv}> · </Text>
          <Text style={[secStyles.statNum, { color: Colors.error }]}>
            {misses}<Text style={secStyles.statLbl}>M</Text>
          </Text>
          {pushes > 0 && (
            <>
              <Text style={secStyles.statDiv}> · </Text>
              <Text style={[secStyles.statNum, { color: Colors.textTertiary }]}>
                {pushes}<Text style={secStyles.statLbl}>P</Text>
              </Text>
            </>
          )}
          <Text style={secStyles.statDiv}>  </Text>
          <Text style={[secStyles.winPct, { color: pctColor }]}>
            {winPct != null ? `${winPct}%` : '—'}
          </Text>
        </View>
        <Ionicons
          name={expanded ? 'chevron-up' : 'chevron-down'}
          size={14}
          color={Colors.textSecondary}
          style={{ marginLeft: 6 }}
        />
      </View>
    </TouchableOpacity>
  );
}

export default function PicksScreen() {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const qc = useQueryClient();
  const topPad = Platform.OS === 'web' ? 67 : insets.top;
  const [activeTab, setActiveTab] = useState<Tab>('live');
  const [analysisModal, setAnalysisModal] = useState<{ pick: Pick; data: Record<string, unknown> | null; loading: boolean } | null>(null);
  // Which sport sections are expanded in history (default: all collapsed so all 3 headers visible)
  const [expandedSports, setExpandedSports] = useState<Set<SportKey>>(new Set());
  const toggleSport = useCallback((sport: SportKey) => {
    setExpandedSports(prev => {
      const next = new Set(prev);
      if (next.has(sport)) next.delete(sport);
      else next.add(sport);
      return next;
    });
  }, []);

  const { data: picks = [], isLoading, refetch, isRefetching, error } = useQuery({
    queryKey: ['picks', session?.email],
    queryFn: async () => {
      if (!session) return [];
      try {
        return await listPicks(session.email, session.token);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        // If auth failed, don't silently return [] — re-throw so error state is set
        if (msg.includes('Invalid session') || msg.includes('401') || msg.includes('Unauthorized')) {
          throw new Error('SESSION_INVALID');
        }
        throw e;
      }
    },
    enabled: !!session,
    refetchInterval: 15000,
    refetchIntervalInBackground: true,
    retry: 2,
    retryDelay: 2000,
  });

  useFocusEffect(
    useCallback(() => {
      refetch();
      const timer = setInterval(() => refetch(), 15000);
      return () => clearInterval(timer);
    }, [refetch])
  );

  const deleteMutation = useMutation({
    mutationFn: (pickId: string) => {
      if (!session) throw new Error('Not authenticated');
      return deletePick(session.email, session.token, pickId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['picks'] });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
    onError: (e: Error) => Alert.alert('Delete failed', e.message),
  });

  const handleDelete = useCallback((pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id) return;
    if (Platform.OS === 'web') {
      if (window.confirm(`Remove ${pick.playerName}?`)) {
        deleteMutation.mutate(id);
      }
    } else {
      Alert.alert('Delete Pick', `Remove ${pick.playerName}?`, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Delete', style: 'destructive', onPress: () => deleteMutation.mutate(id) },
      ]);
    }
  }, [deleteMutation]);

  const handlePickPress = useCallback(async (pick: Pick) => {
    const id = pick.pickId || pick._id || pick.id;
    if (!id || !session) return;
    setAnalysisModal({ pick, data: null, loading: true });
    try {
      const result = await fetchPickAnalysis(session.email, session.token, id);
      setAnalysisModal({ pick, data: result.found ? (result.analysis ?? null) : null, loading: false });
    } catch {
      setAnalysisModal({ pick, data: null, loading: false });
    }
  }, [session]);

  const live = picks.filter(isLive);
  const history = picks.filter(isSettled);

  // Split history into sport buckets (Soccer → MLB → CS2 order)
  const SPORT_ORDER: SportKey[] = ['soccer', 'mlb', 'cs2'];
  const bySport: Record<SportKey, Pick[]> = { soccer: [], mlb: [], cs2: [] };
  for (const p of history) bySport[getSport(p)].push(p);

  // Build flat accordion list: header items + pick items for expanded sections
  type AccordionItem =
    | { type: 'header'; sport: SportKey }
    | { type: 'pick';   sport: SportKey; pick: Pick };

  const accordionData: AccordionItem[] = [];
  for (const sport of SPORT_ORDER) {
    if (bySport[sport].length === 0) continue;
    accordionData.push({ type: 'header', sport });
    if (expandedSports.has(sport)) {
      for (const pick of bySport[sport]) {
        accordionData.push({ type: 'pick', sport, pick });
      }
    }
  }

  const modalRec = ((analysisModal?.data?.recommendation ?? analysisModal?.pick?.recommendation) as string | undefined)?.toUpperCase() ?? '';
  const modalIsOver = modalRec === 'OVER';
  const modalIsUnder = modalRec === 'UNDER';
  const modalRecColor = modalIsOver ? Colors.success : modalIsUnder ? Colors.error : Colors.textSecondary;
  const modalText = (analysisModal?.data?.reasoning ?? analysisModal?.data?.tacticalBreakdown ?? analysisModal?.data?.explanation) as string | undefined;

  return (
    <View style={[styles.root, { paddingTop: topPad }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>My Picks</Text>
        <View style={styles.tabToggle}>
          {(['live', 'history'] as Tab[]).map(t => (
            <TouchableOpacity
              key={t}
              style={[styles.toggle, activeTab === t && styles.toggleActive]}
              onPress={() => { setActiveTab(t); Haptics.selectionAsync(); }}
            >
              {t === 'live' && live.length > 0 && activeTab !== 'live' && (
                <View style={styles.tabDot} />
              )}
              <Text style={[styles.toggleText, activeTab === t && styles.toggleTextActive]}>
                {t === 'live' ? `Live${live.length > 0 ? ` (${live.length})` : ''}` : `History${history.length > 0 ? ` (${history.length})` : ''}`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      {/* Record bar — always show if any picks exist */}
      {picks.length > 0 && <RecordBar picks={picks} />}

      {error && (error as Error).message === 'SESSION_INVALID' ? (
        <View style={styles.center}>
          <Ionicons name="lock-closed-outline" size={44} color={Colors.textTertiary} />
          <Text style={[styles.emptyTitle, { marginTop: 12 }]}>Session expired</Text>
          <Text style={[styles.emptySub, { textAlign: 'center', marginTop: 6 }]}>
            Your session timed out. Go to Account and tap Verify Access to restore your picks.
          </Text>
          <TouchableOpacity onPress={() => refetch()} style={{ marginTop: 16 }}>
            <Text style={{ color: Colors.primary, fontWeight: '700' }}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.primary} size="large" />
        </View>
      ) : activeTab === 'live' && live.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="timer-outline" size={52} color={Colors.textTertiary} />
          <Text style={styles.emptyTitle}>No live picks</Text>
          <Text style={styles.emptySub}>Scan a prop slip and save a prediction to track it here.</Text>
        </View>
      ) : activeTab === 'history' && history.length === 0 ? (
        <View style={styles.empty}>
          <Ionicons name="archive-outline" size={52} color={Colors.textTertiary} />
          <Text style={styles.emptyTitle}>No settled picks yet</Text>
          <Text style={styles.emptySub}>Picks move here once their game is finished and results are confirmed.</Text>
        </View>
      ) : activeTab === 'live' ? (
        <FlatList
          data={live}
          keyExtractor={(item, i) => item.pickId || item._id || item.id || String(i)}
          renderItem={({ item }) => {
            const tappable = isLive(item) && !pickWon(item) && !pickLost(item);
            const onDeleteForItem = () => handleDelete(item);
            const card = tappable ? (
              <Pressable
                onPress={() => {
                  try { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); } catch {}
                  handlePickPress(item);
                }}
                style={({ pressed }) => [{ opacity: pressed ? 0.88 : 1 }]}
              >
                <PickCard pick={item} onDelete={onDeleteForItem} />
              </Pressable>
            ) : (
              <PickCard pick={item} onDelete={onDeleteForItem} />
            );
            return <SwipeableRow onDelete={onDeleteForItem}>{card}</SwipeableRow>;
          }}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />}
          showsVerticalScrollIndicator={false}
        />
      ) : (
        /* ── HISTORY: collapsible sport accordion ── */
        <FlatList
          data={accordionData}
          keyExtractor={(item, i) =>
            item.type === 'header'
              ? `hdr-${item.sport}`
              : `pick-${(item as { type: 'pick'; pick: Pick; sport: SportKey }).pick.pickId || i}`
          }
          renderItem={({ item }) => {
            if (item.type === 'header') {
              return (
                <SportSectionHeader
                  sport={item.sport}
                  picks={bySport[item.sport]}
                  expanded={expandedSports.has(item.sport)}
                  onToggle={() => {
                    try { Haptics.selectionAsync(); } catch {}
                    toggleSport(item.sport);
                  }}
                />
              );
            }
            const pickItem = item.pick;
            const onDeleteForItem = () => handleDelete(pickItem);
            return (
              <SwipeableRow onDelete={onDeleteForItem}>
                <Pressable
                  onPress={() => handlePickPress(pickItem)}
                  style={({ pressed }) => [{ opacity: pressed ? 0.92 : 1 }]}
                >
                  <PickCard pick={pickItem} onDelete={onDeleteForItem} />
                </Pressable>
              </SwipeableRow>
            );
          }}
          contentContainerStyle={[styles.list, { paddingTop: 4 }]}
          refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={Colors.primary} />}
          showsVerticalScrollIndicator={false}
        />
      )}

      {/* ── Analysis Modal ── */}
      <Modal
        visible={analysisModal !== null}
        animationType="slide"
        transparent
        onRequestClose={() => setAnalysisModal(null)}
      >
        <View style={mStyles.modalContainer}>
          <Pressable style={mStyles.modalBackdrop} onPress={() => setAnalysisModal(null)} />
          <View style={mStyles.modalSheet}>
          {/* Handle */}
          <View style={mStyles.modalHandle} />

          {/* Header */}
          <View style={mStyles.modalHeader}>
            <View style={mStyles.modalPlayerInfo}>
              <Text style={mStyles.modalPlayer} numberOfLines={1}>{analysisModal?.pick.playerName}</Text>
              <Text style={mStyles.modalMeta} numberOfLines={1}>
                {[analysisModal?.pick.teamName, analysisModal?.pick.opponentName
                  ? (analysisModal?.pick.venue === 'away' ? `@ ${analysisModal?.pick.opponentName}` : `vs ${analysisModal?.pick.opponentName}`)
                  : null].filter(Boolean).join(' · ')}
              </Text>
            </View>
            <View style={mStyles.modalRight}>
              {modalRec ? (
                <View style={[mStyles.modalRecBadge, { backgroundColor: modalIsOver ? Colors.successDim : modalIsUnder ? Colors.errorDim : Colors.cardSecondary }]}>
                  <Text style={[mStyles.modalRecText, { color: modalRecColor }]}>{modalRec}</Text>
                </View>
              ) : null}
              <TouchableOpacity onPress={() => setAnalysisModal(null)} style={mStyles.modalClose}>
                <Ionicons name="close" size={18} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
          </View>

          {/* Prop row */}
          <View style={mStyles.modalPropRow}>
            <Text style={mStyles.modalPropText}>
              {PROP_LABELS[analysisModal?.pick.propType ?? ''] ?? analysisModal?.pick.propType} · Line {analysisModal?.pick.line}
            </Text>
            {analysisModal?.data?.projectedValue != null && (
              <Text style={[mStyles.modalProjText, { color: modalRecColor }]}>
                Proj {(analysisModal.data.projectedValue as number).toFixed(1)}
              </Text>
            )}
          </View>

          {/* Edge-gap pill row — surfaces how far projection sits from the line
              and whether league calibration / game-script informed the call. */}
          {(() => {
            const bm: any = (analysisModal?.data as any)?.bayesianMetrics ?? {};
            const gapPct = bm.edgeGapPct;
            const gapBand = bm.edgeGapBand;
            const lcal = bm.leagueCalibration;
            const gs = bm.gameScript;
            if (gapPct == null && !lcal?.applied && !gs?.applied) return null;
            const bandColor = gapBand === 'DEEP' ? Colors.success
              : gapBand === 'STRONG' ? Colors.success
              : gapBand === 'MODERATE' ? Colors.primary
              : Colors.textSecondary;
            return (
              <View style={mStyles.modalEdgeRow}>
                {gapPct != null && (
                  <View style={[mStyles.edgePill, { borderColor: bandColor }]}>
                    <Text style={[mStyles.edgePillText, { color: bandColor }]}>
                      {gapBand ?? 'EDGE'} · {gapPct > 0 ? '+' : ''}{Number(gapPct).toFixed(1)}%
                    </Text>
                  </View>
                )}
                {lcal?.applied && lcal?.n > 0 && (
                  <View style={[mStyles.edgePill, { borderColor: Colors.borderSubtle }]}>
                    <Text style={[mStyles.edgePillText, { color: Colors.textSecondary }]}>
                      League calib · n={lcal.n} · {Math.round((lcal.hit_rate ?? 0) * 100)}% hit
                    </Text>
                  </View>
                )}
                {gs?.applied && (
                  <View style={[mStyles.edgePill, { borderColor: Colors.borderSubtle }]}>
                    <Text style={[mStyles.edgePillText, { color: Colors.textSecondary }]}>
                      Game-script · ×{Number(gs.multiplier).toFixed(3)}
                    </Text>
                  </View>
                )}
              </View>
            );
          })()}

          <View style={mStyles.modalDivider} />

          {/* Body */}
          <ScrollView style={mStyles.modalScroll} contentContainerStyle={mStyles.modalScrollContent} showsVerticalScrollIndicator={false}>
            {analysisModal?.loading ? (
              <View style={mStyles.modalLoading}>
                <ActivityIndicator color={Colors.primary} />
                <Text style={mStyles.modalLoadingText}>Loading analysis…</Text>
              </View>
            ) : !modalText ? (
              <View style={mStyles.modalLoading}>
                <Ionicons name="analytics-outline" size={32} color={Colors.textTertiary} />
                <Text style={mStyles.modalLoadingText}>No analysis found for this pick yet.</Text>
              </View>
            ) : (
              <View style={mStyles.aiBlocks}>
                {renderAnalysisBlocks(modalText, modalRec)}
              </View>
            )}
          </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const secStyles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginHorizontal: 16,
    marginTop: 14,
    marginBottom: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: Colors.card,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  headerLeft:  { flexDirection: 'row', alignItems: 'center', gap: 7 },
  headerRight: { flexDirection: 'row', alignItems: 'center' },
  headerIcon:  { fontSize: 16 },
  headerLabel: { fontSize: 13, fontWeight: '800', color: Colors.text, letterSpacing: 0.3 },
  countPill: {
    backgroundColor: Colors.cardSecondary,
    borderRadius: 999, paddingHorizontal: 7, paddingVertical: 1,
  },
  countText:   { fontSize: 10, fontWeight: '700', color: Colors.textSecondary },
  headerStats: { flexDirection: 'row', alignItems: 'baseline' },
  statNum:     { fontSize: 13, fontWeight: '800' },
  statLbl:     { fontSize: 9, fontWeight: '600', color: Colors.textTertiary },
  statDiv:     { fontSize: 11, color: Colors.textTertiary },
  winPct:      { fontSize: 14, fontWeight: '800' },
});

const mStyles = StyleSheet.create({
  modalContainer: { flex: 1, justifyContent: 'flex-end' },
  modalBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(0,0,0,0.6)' },
  modalSheet: {
    backgroundColor: Colors.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '82%',
    borderWidth: 1,
    borderColor: Colors.borderSubtle,
    borderBottomWidth: 0,
  },
  modalHandle: {
    width: 36, height: 4, borderRadius: 2,
    backgroundColor: Colors.border, alignSelf: 'center', marginTop: 10,
  },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingTop: 14, paddingBottom: 10, gap: 10,
  },
  modalPlayerInfo: { flex: 1 },
  modalPlayer: { fontSize: 18, fontWeight: '800', color: Colors.text },
  modalMeta: { fontSize: 12, color: Colors.textSecondary, marginTop: 2 },
  modalRight: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  modalRecBadge: { paddingHorizontal: 12, paddingVertical: 5, borderRadius: 8 },
  modalRecText: { fontSize: 13, fontWeight: '800', letterSpacing: 0.5 },
  modalClose: { padding: 4 },
  modalPropRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 18, paddingBottom: 12,
  },
  modalPropText: { fontSize: 13, color: Colors.textSecondary },
  modalProjText: { fontSize: 14, fontWeight: '700' },
  modalEdgeRow: {
    flexDirection: 'row', flexWrap: 'wrap', gap: 6,
    paddingHorizontal: 18, paddingTop: 4, paddingBottom: 8,
  },
  edgePill: {
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 6, borderWidth: 1,
    backgroundColor: Colors.cardSecondary,
  },
  edgePillText: { fontSize: 11, fontWeight: '700', letterSpacing: 0.3 },
  modalDivider: { height: 1, backgroundColor: Colors.borderSubtle },
  modalScroll: { flex: 0 },
  modalScrollContent: { padding: 18, paddingBottom: 40 },
  modalLoading: { alignItems: 'center', paddingVertical: 40, gap: 14 },
  modalLoadingText: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center' },
  aiBlocks: { gap: 16 },
  aiVerdictBlock: {
    backgroundColor: 'rgba(57,255,20,0.06)',
    borderLeftWidth: 3, borderLeftColor: Colors.primary,
    borderRadius: 8, padding: 12, gap: 6,
  },
  aiVerdictPill: { alignSelf: 'flex-start', borderRadius: 4, paddingHorizontal: 7, paddingVertical: 2 },
  aiVerdictLabel: { fontSize: 9, fontWeight: '800', letterSpacing: 1.5 },
  aiVerdictText: { fontSize: 14, fontWeight: '600', lineHeight: 21, color: Colors.text },
  aiTldrBlock: { backgroundColor: Colors.cardSecondary, borderRadius: 8, padding: 12 },
  aiTldrText: { fontSize: 12, color: Colors.textSecondary, lineHeight: 18, fontStyle: 'italic' },
  aiSection: { gap: 5 },
  aiSectionTitle: { fontSize: 10, fontWeight: '800', color: Colors.primary, letterSpacing: 1.2 },
  aiSectionBody: { fontSize: 13, color: Colors.textSecondary, lineHeight: 20 },
});

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Colors.background },
  header: {
    paddingHorizontal: 20, paddingBottom: 12,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 28, fontWeight: '800', color: Colors.text },
  tabToggle: {
    flexDirection: 'row', backgroundColor: Colors.card,
    borderRadius: 10, borderWidth: 1, borderColor: Colors.border, padding: 3,
  },
  toggle: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: 8, flexDirection: 'row', alignItems: 'center', gap: 5 },
  toggleActive: { backgroundColor: Colors.primary },
  toggleText: { fontSize: 13, fontWeight: '600', color: Colors.textSecondary },
  toggleTextActive: { color: '#000' },
  tabDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: Colors.primary },
  progressWrap: { gap: 6, marginTop: 2 },
  progressTrack: {
    height: 6,
    borderRadius: 999,
    backgroundColor: Colors.cardSecondary,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%' as unknown as number,
    borderRadius: 999,
    backgroundColor: Colors.primary,
  },
  progressText: { fontSize: 10, color: Colors.textTertiary, fontWeight: '600', textAlign: 'right' },

  recordBar: {
    marginHorizontal: 20, marginBottom: 12, backgroundColor: Colors.card,
    borderRadius: Colors.radius, borderWidth: 1, borderColor: Colors.border, padding: 14, gap: 8,
  },
  recordLabel: { fontSize: 10, fontWeight: '700', color: Colors.textTertiary, letterSpacing: 1.5 },
  recordStats: { flexDirection: 'row', justifyContent: 'space-between' },
  recordStat: { alignItems: 'center', flex: 1 },
  recordVal: { fontSize: 18, fontWeight: '800', color: Colors.text },
  recordKey: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.5, marginTop: 2 },

  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12, paddingHorizontal: 40 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: Colors.text },
  emptySub: { fontSize: 14, color: Colors.textSecondary, textAlign: 'center', lineHeight: 21 },
  list: { paddingHorizontal: 16, paddingBottom: 40, gap: 4 },

  card: {
    backgroundColor: Colors.card, borderRadius: 10,
    paddingHorizontal: 10, paddingVertical: 6,
    borderWidth: 1, borderColor: Colors.borderSubtle, gap: 2,
  },
  cardWon: { borderColor: 'rgba(57,255,20,0.3)' },
  cardLost: { borderColor: 'rgba(255,59,48,0.25)' },

  cardTopRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  cardRight: { flexDirection: 'row', alignItems: 'center', gap: 5, flexShrink: 0 },
  cardPlayer: { fontSize: 13, fontWeight: '700', color: Colors.text, flex: 1 },
  cardMeta: { fontSize: 9, color: Colors.textTertiary, letterSpacing: 0.2, marginBottom: 1 },

  cardRow2: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  cardRow2Left: { flex: 1, gap: 1 },
  inlineStats: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 },
  inlineStat: { alignItems: 'center', gap: 0 },
  inlineVal: { fontSize: 12, fontWeight: '700', color: Colors.text },
  inlineLbl: { fontSize: 7, color: Colors.textTertiary, fontWeight: '600', letterSpacing: 0.6 },

  liveBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  liveDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: Colors.primary },
  liveText: { fontSize: 8, color: Colors.primary, fontWeight: '700', letterSpacing: 0.5 },
  pendingBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 5,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    paddingHorizontal: 6, paddingVertical: 2,
  },
  pendingText: { fontSize: 8, color: Colors.textSecondary, fontWeight: '600', letterSpacing: 0.5 },
  wonBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: Colors.primary, borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  wonText: { fontSize: 8, color: '#000', fontWeight: '800', letterSpacing: 0.5 },
  lostBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: Colors.errorDim, borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
    borderWidth: 1, borderColor: 'rgba(255,59,48,0.3)',
  },
  lostText: { fontSize: 8, color: Colors.error, fontWeight: '800', letterSpacing: 0.5 },
  pushBadge: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: Colors.pushDim, borderRadius: 5,
    borderWidth: 1, borderColor: 'rgba(10,132,255,0.3)',
    paddingHorizontal: 6, paddingVertical: 2,
  },
  pushText: { fontSize: 8, color: Colors.push, fontWeight: '800', letterSpacing: 0.5 },

  pickRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  recPill: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  recPillText: { fontSize: 9, fontWeight: '800', letterSpacing: 0.5 },
  pickDetail: { fontSize: 11, color: Colors.textSecondary },
  coinFlipBadge: { backgroundColor: Colors.cardSecondary, paddingHorizontal: 4, paddingVertical: 1, borderRadius: 4 },
  coinFlipText: { fontSize: 8, fontWeight: '800', color: Colors.textTertiary },
  confBadge: {
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
    borderWidth: 1,
    backgroundColor: 'transparent',
  },
  confBadgeText: { fontSize: 9, fontWeight: '800', letterSpacing: 0.3 },

  trackBarOuter: {
    height: 4,
    backgroundColor: Colors.cardSecondary,
    borderRadius: 2,
    overflow: 'hidden',
    position: 'relative',
    marginTop: 3,
  },
  trackBarFill: {
    position: 'absolute',
    left: 0,
    top: 0,
    height: '100%' as unknown as number,
    borderRadius: 2,
  },
  trackBarMarker: {
    position: 'absolute',
    left: '50%' as unknown as number,
    top: 0,
    width: 1,
    height: '100%' as unknown as number,
    backgroundColor: 'rgba(255,255,255,0.55)',
    transform: [{ translateX: -0.5 }],
  },

  matchCtxBlock: {
    marginTop: 4,
    paddingTop: 3,
    borderTopWidth: 1,
    borderTopColor: Colors.borderSubtle,
    gap: 1,
  },
  matchCtxScoreRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  scoreLabel: {
    fontSize: 8,
    fontWeight: '800',
    color: Colors.textTertiary,
    letterSpacing: 0.5,
  },
  scoreText: { flex: 1, fontSize: 10 },
  scoreTeamName: { fontWeight: '700' },
  scoreNum: { fontWeight: '800' },
  scoreDash: { color: Colors.textTertiary, fontWeight: '600' },
  possText: { fontSize: 9, color: Colors.textSecondary, fontWeight: '600' },
  projPossRow: { flexDirection: 'row', alignItems: 'center', gap: 5, flexWrap: 'wrap' },
  projPossText: { fontSize: 9, color: Colors.textTertiary, fontWeight: '600', fontStyle: 'italic', flexShrink: 1 },
  projDeltaText: { fontSize: 8, fontWeight: '800', letterSpacing: 0.3, flexShrink: 0 },

  swipeAction: {
    backgroundColor: Colors.error,
    justifyContent: 'center',
    alignItems: 'center',
    width: 84,
    borderRadius: 12,
  },
  swipeActionInner: { alignItems: 'center', justifyContent: 'center', gap: 3 },
  swipeActionText: {
    color: '#fff',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  tapHint: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    paddingTop: 4, borderTopWidth: 1, borderTopColor: Colors.borderSubtle,
  },
  cardFooterWeb: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    marginTop: 6,
  },
  tapHintText: { fontSize: 9, color: Colors.primary, fontWeight: '600', letterSpacing: 0.3 },
});
