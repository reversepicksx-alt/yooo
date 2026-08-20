import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, Image, TouchableOpacity, StyleSheet, Share, Platform, ActivityIndicator, Modal,
} from 'react-native';

const APP_STORE_URL = 'https://apps.apple.com/app/id6781092173';

async function fetchAsDataUri(url: string): Promise<string> {
  try {
    const resp = await fetch(url);
    const blob = await resp.blob();
    return await new Promise<string>(res => {
      const reader = new FileReader();
      reader.onload = () => res(reader.result as string);
      reader.readAsDataURL(blob);
    });
  } catch { return ''; }
}
import { Ionicons } from '@expo/vector-icons';
import { captureRef } from 'react-native-view-shot';
import Colors from '@/constants/colors';
import { Pick } from '@/lib/api';

// ─── helpers ────────────────────────────────────────────────────────────────
const PROP_LABELS: Record<string, string> = {
  pass_attempts: 'PASSES', passes: 'PASSES', shots: 'SHOTS', shots_on_target: 'SOT',
  tackles: 'TACKLES', key_passes: 'KEY PASSES', saves: 'SAVES', interceptions: 'INTS',
  blocks: 'BLOCKS', dribbles: 'DRIBBLES', crosses: 'CROSSES', clearances: 'CLEARANCES',
  goals: 'GOALS', assists: 'ASSISTS', fouls_drawn: 'FOULS WON', fouls_committed: 'FOULS',
  duels_won: 'DUELS', yellow_cards: 'YC', shots_assisted: 'SHOT ASSISTS', goalie_saves: 'SAVES',
};

function isSettled(p: Pick) {
  return getSettledOutcome(p) != null;
}
const LIVE_MATCH_STATUSES = new Set([
  '1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE',
]);

function fixtureKickoffMs(p: Pick): number | null {
  if (!p.fixtureDate) return null;
  const value = new Date(p.fixtureDate).getTime();
  return Number.isFinite(value) ? value : null;
}

function isLive(p: Pick, nowMs = Date.now()) {
  if (isPendingReview(p)) return false;
  if (isSettled(p)) return false;
  // A live stat or a previously promoted database status is not proof that
  // the fixture has started. Only a provider in-progress status can promote
  // the card, and a known future kickoff always wins over a stale status.
  const providerStatus = String(p.matchStatus || '').trim().toUpperCase();
  if (!LIVE_MATCH_STATUSES.has(providerStatus)) return false;
  const kickoffMs = fixtureKickoffMs(p);
  return kickoffMs == null || nowMs >= kickoffMs;
}
function isPendingReview(p: Pick) {
  const raw = String(p.result || '').toLowerCase();
  const hasFinalOutcome = ['hit', 'miss', 'push', 'dnp'].includes(raw)
    && p.actualValue != null
    && (
      p.settlementSource?.verified === true
      || p.settlementSource?.verificationMethod === 'legacy_numeric_reconciliation'
      || p.status === 'settled'
    );
  if (hasFinalOutcome) return false;
  return p.status === 'pending_review'
    || raw === 'pending_review';
}
function isPending(p: Pick) {
  return !isPendingReview(p) && !isSettled(p) && !isLive(p);
}
function getSettledOutcome(p: Pick): 'hit' | 'miss' | 'push' | 'dnp' | null {
  const raw = String(p.result || '').toLowerCase();
  const hasVerifiedSource = p.settlementSource?.verified === true;
  const isExplicitFinal = p.status === 'settled' || p.matchStatus === 'final';
  if (p.actualValue != null) {
    const line = Number(p.line);
    const actual = Number(p.actualValue);
    const rec = String(p.recommendation || '').toLowerCase();
    if (Number.isFinite(line) && Number.isFinite(actual) && (rec === 'over' || rec === 'under')) {
      const computed = Number.isInteger(line) && actual === line
        ? 'push'
        : (rec === 'over' ? actual > line : actual < line) ? 'hit' : 'miss';
      if (['hit', 'miss', 'push', 'won', 'lost'].includes(raw)) {
        if (
          (raw === 'hit' || raw === 'won') && computed !== 'hit'
          || (raw === 'miss' || raw === 'lost') && computed !== 'miss'
          || raw === 'push' && computed !== 'push'
        ) return null;
      }
      return computed;
    }
  }
  if ((hasVerifiedSource || isExplicitFinal) && (raw === 'hit' || raw === 'won')) return 'hit';
  if ((hasVerifiedSource || isExplicitFinal) && (raw === 'miss' || raw === 'lost')) return 'miss';
  if ((hasVerifiedSource || isExplicitFinal) && raw === 'push') return 'push';
  if ((hasVerifiedSource || isExplicitFinal) && raw === 'dnp') return 'dnp';
  if ((hasVerifiedSource || isExplicitFinal) && raw === 'pass') {
    // A verified final value is always HIT, MISS, or PUSH. Older saved rows
    // may still say PASS; use their stored directional settlement outcome.
    if (p.passOutcome === 'hit') return 'hit';
    if (p.passOutcome === 'miss') return 'miss';
    if (p.passOutcome === 'push') return 'push';
  }
  if (
    (p.status === 'settled' || p.matchStatus === 'final')
    && p.actualValue != null
      && (p.settlementSource?.verified === true || isExplicitFinal)
  ) {
    const line = Number(p.line);
    const actual = Number(p.actualValue);
    const rec = String(p.recommendation || '').toLowerCase();
    if (Number.isFinite(line) && Number.isFinite(actual) && (rec === 'over' || rec === 'under')) {
      if (Number.isInteger(line) && actual === line) return 'push';
      return (rec === 'over' ? actual > line : actual < line) ? 'hit' : 'miss';
    }
  }
  return null;
}
function pickWon(p: Pick) { return getSettledOutcome(p) === 'hit' || p.status === 'won'; }
function pickLost(p: Pick) { return getSettledOutcome(p) === 'miss' || p.status === 'lost'; }
function pickPush(p: Pick) { return getSettledOutcome(p) === 'push'; }
function pickDnp(p: Pick) { return getSettledOutcome(p) === 'dnp'; }
function getRecDir(p: Pick): 'OVER' | 'UNDER' | null {
  if (p.recommendation === 'OVER' || p.recommendation === 'UNDER') return p.recommendation;
  const passLean = String(p.passLeaning || '').toUpperCase();
  if (passLean === 'OVER' || passLean === 'UNDER') return passLean;
  const proj = p.projection ?? p.projectedValue;
  if (proj != null && p.line > 0) return proj > p.line ? 'OVER' : 'UNDER';
  return null;
}
function fmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return Number(n).toFixed(n % 1 === 0 ? 0 : 1);
}
function formatMatchTime(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
  const timeStr = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `Today · ${timeStr}`;
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow · ${timeStr}`;
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} · ${timeStr}`;
}

function formatCountdown(iso: string | undefined, nowMs: number): string | null {
  const kickoffMs = iso ? new Date(iso).getTime() : NaN;
  if (!Number.isFinite(kickoffMs) || kickoffMs <= nowMs) return null;
  const seconds = Math.ceil((kickoffMs - nowMs) / 1000);
  if (seconds < 60) return 'STARTS IN <1M';
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `STARTS IN ${minutes}M`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder > 0
    ? `STARTS IN ${hours}H ${String(remainder).padStart(2, '0')}M`
    : `STARTS IN ${hours}H`;
}

function elapsedFromKickoff(iso?: string, nowMs = Date.now()): number | null {
  if (!iso) return null;
  const kickoffMs = new Date(iso).getTime();
  if (!Number.isFinite(kickoffMs) || nowMs < kickoffMs) return null;
  return Math.max(0, Math.min(120, Math.floor((nowMs - kickoffMs) / 60000)));
}

function formatStatusLabel(
  pick: Pick,
  live: boolean,
  settled: boolean,
  elapsed: number | null,
  nowMs: number,
): string {
  if (settled || pick.matchStatus === 'final') return 'FT';
  const period = String(pick.period || '').toUpperCase();
  if (live) {
    if (period === 'HT') return 'LIVE · HT';
    const providerElapsed = elapsed != null && Number(elapsed) > 0
      ? Math.floor(Number(elapsed))
      : null;
    const displayElapsed = providerElapsed ?? elapsedFromKickoff(pick.fixtureDate, nowMs);
    return displayElapsed != null ? `LIVE · ${displayElapsed}'` : 'LIVE';
  }
  const countdown = formatCountdown(pick.fixtureDate, nowMs);
  if (countdown) return countdown;
  const kickoff = formatMatchTime(pick.fixtureDate);
  return kickoff ? `AWAITING START · ${kickoff}` : 'SCHEDULED';
}

// ─── Status badge ────────────────────────────────────────────────────────────
function StatusPill({
  won, lost, push, dnp,
  live, pending, pendingReview,
}: Record<string, any>) {
  if (won) return <View style={[pill.root, { backgroundColor: '#39FF14' }]}><Text style={[pill.txt, { color: '#000' }]}>HIT ✓</Text></View>;
  if (lost) return <View style={[pill.root, { backgroundColor: '#FF3B30' }]}><Text style={[pill.txt, { color: '#fff' }]}>MISS</Text></View>;
  if (push) return <View style={[pill.root, { backgroundColor: '#0A84FF' }]}><Text style={[pill.txt, { color: '#fff' }]}>PUSH</Text></View>;
  if (dnp) return <View style={[pill.root, { backgroundColor: '#FF9500' }]}><Text style={[pill.txt, { color: '#fff' }]}>DNP</Text></View>;
  if (pendingReview) return <View style={[pill.root, pill.pendingReview]}><Text style={[pill.txt, { color: '#FFD60A' }]}>PENDING REVIEW</Text></View>;
  if (live) return (
    <View style={[pill.root, pill.live]}>
      <View style={pill.dot} />
      <Text style={[pill.txt, { color: '#FF3B30' }]}>LIVE</Text>
    </View>
  );
  if (pending) return <View style={[pill.root, pill.pending]}><Text style={[pill.txt, { color: 'rgba(255,255,255,0.7)' }]}>PENDING</Text></View>;
  return null;
}
const pill = StyleSheet.create({
  root: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 20 },
  live: { backgroundColor: 'rgba(255,59,48,0.12)', borderWidth: 1, borderColor: 'rgba(255,59,48,0.4)' },
  pending: { backgroundColor: 'rgba(255,255,255,0.08)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.14)' },
  pendingReview: { backgroundColor: 'rgba(255,214,10,0.12)', borderWidth: 1, borderColor: 'rgba(255,214,10,0.45)' },
  dot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: '#FF3B30', marginRight: 5 },
  txt: { fontSize: 9, fontWeight: '900', letterSpacing: 0.8 },
  manager: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 6, paddingVertical: 3, borderRadius: 20,
    backgroundColor: 'rgba(245,158,11,0.12)',
    borderWidth: 1, borderColor: 'rgba(245,158,11,0.45)',
  },
  rerun: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 6, paddingVertical: 3, borderRadius: 20,
    backgroundColor: 'rgba(255,107,53,0.12)',
    borderWidth: 1, borderColor: 'rgba(255,107,53,0.5)',
  },
});

// ─── Main component ──────────────────────────────────────────────────────────
export default function OwnerPickCard({
  pick, onDelete, onManagerBadgePress, onRefreshSettlement, settlementRefreshing = false,
  ownerMediaEnabled = false, compact = false,
}: {
  pick: Pick;
  onDelete?: () => void;
  onManagerBadgePress?: () => void;
  onRefreshSettlement?: () => void;
  settlementRefreshing?: boolean;
  ownerMediaEnabled?: boolean;
  compact?: boolean;
}) {
  const [clockNow, setClockNow] = useState(() => Date.now());
  const won = pickWon(pick);
  const lost = pickLost(pick);
  const push = pickPush(pick);
  const dnp = pickDnp(pick);
  const settled = isSettled(pick);
  const pendingReview = isPendingReview(pick);
  const canRefreshSettlement = !!onRefreshSettlement
    && (settled || pendingReview)
    && (!pick.sport || String(pick.sport).toLowerCase() === 'soccer');
  const live = isLive(pick, clockNow);
  const pending = isPending(pick);

  const dir = getRecDir(pick);
  const isOver = dir === 'OVER';
  const isUnder = dir === 'UNDER';
  // Accent: green=OVER/HIT, red=UNDER/MISS, blue=push, amber=dnp, dim=pending
  const accentColor = won ? '#39FF14' : lost ? '#FF3B30' : push ? '#0A84FF' : dnp ? '#FF9500'
    : isOver ? '#39FF14' : isUnder ? '#FF3B30' : 'rgba(255,255,255,0.18)';

  const lineValue = typeof pick.line === 'number' ? pick.line : null;
  const projValue = pick.projection ?? pick.projectedValue ?? null;
  // A settled card may only show an official final stat. Never fall back to
  // currentValue here: that is a live/polling value and was the source of
  // misleading "FINAL" values on older saved picks.
  const explicitResult = ['hit', 'miss', 'push', 'dnp', 'pass'].includes(String(pick.result || '').toLowerCase());
  const hasVerifiedFinal = pick.actualValue != null
    && explicitResult
    && pick.settlementSource?.verified === true;
  const actualValue = hasVerifiedFinal ? pick.actualValue! : null;
  const livePace = pick.pace ?? null;
  const nowValue = pendingReview
    ? (pick.actualValue ?? pick.currentValue ?? null)
    : settled ? actualValue : live ? (pick.currentValue ?? pick.actualValue ?? null) : null;
  // The saved model projection is permanent context. Live pace is a separate
  // in-game estimate and must never replace PROJ in the compact iOS card.
  const paceValue = live ? livePace : null;
  const paceLabel = 'PACE';
  // hitPct is a live pace estimate.  Once settled, 0/100 is the outcome,
  // not a model probability; showing it as "hit prob" was misleading.
  const hitPct = live ? (pick.hitPct ?? null) : null;

  const progress = useMemo(() => {
    if (lineValue == null || nowValue == null || lineValue <= 0) return null;
    return Math.max(0, Math.min(100, (nowValue / (lineValue * 2)) * 100));
  }, [lineValue, nowValue]);

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
  const showScoreLine = trustOrient && (((settled || live) && hasScore) || hasActualPoss || hasProjPoss);

  const propLabel = PROP_LABELS[pick.propType] || pick.propType?.replace(/_/g, ' ').toUpperCase() || 'PROP';
  const venueTag = pick.venue === 'away' ? 'AWAY' : 'HOME';
  const elapsed = pick.elapsed ?? (pick as any).matchMinute ?? null;
  const matchTime = !settled ? formatMatchTime(pick.fixtureDate) : '';
  const statusLabel = formatStatusLabel(pick, live, settled, elapsed, clockNow);
  const dirLabel = dir ? `${dir} ${propLabel}` : propLabel;
  const nowTrackColor = nowValue != null && lineValue != null
    ? ((isOver && nowValue > lineValue) || (!isUnder && !isOver)) ? Colors.primary
      : ((isOver && nowValue < lineValue) || (isUnder && nowValue > lineValue)) ? Colors.error : Colors.primary
    : Colors.text;

  const [sharing, setSharing] = useState(false);
  const [photoFailed, setPhotoFailed] = useState(false);
  const [teamLogoFailed, setTeamLogoFailed] = useState(false);
  const [shareSheetVisible, setShareSheetVisible] = useState(false);
  const [captureMode, setCaptureMode] = useState(false);
  const pendingBlobRef = useRef<Blob | null>(null);
  const cardRef = useRef<View>(null);

  useEffect(() => {
    if (settled) return;
    // This drives both the pregame countdown and the live clock. The
    // provider status still controls LIVE; this timer only refreshes display.
    const timer = setInterval(() => setClockNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, [settled]);

  // The owner-only response fields are preferred, but older/mobile cached
  // responses may not contain them. Reconstruct the same verified
  // API-Football media URLs from the persisted IDs only for the owner card.
  // Never enable this fallback for subscriber cards.
  const canUseOwnerMedia = ownerMediaEnabled
    && (!pick.sport || pick.sport.toLowerCase() === 'soccer');
  const playerPhotoUri = pick.ownerPlayerPhoto
    || (canUseOwnerMedia && pick.playerId
      ? `https://media.api-sports.io/football/players/${pick.playerId}.png`
      : '');
  const teamLogoUri = pick.ownerTeamLogo
    || (canUseOwnerMedia && pick.teamId
      ? `https://media.api-sports.io/football/teams/${pick.teamId}.png`
      : '');

  useEffect(() => {
    setPhotoFailed(false);
    setTeamLogoFailed(false);
  }, [playerPhotoUri, teamLogoUri]);

  const tweetText = `${APP_STORE_URL}\nvia @Reversepickss`;
  const nativeShareText = `${APP_STORE_URL}\nvia @Reversepickss`;

  const handleShare = async () => {
    setSharing(true);
    try {
      if (Platform.OS === 'web') {
        await generateShareImage();
        setShareSheetVisible(true);
      } else {
        await handleNativeShareImage();
      }
    } catch { /* cancelled */ } finally { setSharing(false); }
  };

  const handleNativeShareImage = async () => {
    try {
      setCaptureMode(true);
      // Let the buttons disappear before capture
      await new Promise(r => setTimeout(r, 80));
      const uri = await captureRef(cardRef, { format: 'png', quality: 1 });
      await Share.share({ url: uri, message: nativeShareText, title: `${pick.playerName} pick` });
    } catch {
      // fallback to text-only if capture fails
      await Share.share({ message: nativeShareText, title: `${pick.playerName} pick` });
    } finally {
      setCaptureMode(false);
    }
  };

  const generateShareImage = async () => {
    // Pre-fetch all images as base64 — API-Football blocks CORS so canvas would be blank otherwise
    const logoSrc = (require('../assets/logo.png') as any);
    const logoUrl = typeof logoSrc === 'string' ? logoSrc : logoSrc?.uri ?? '';
    const [logoDataUri, photoDataUri, teamLogoDataUri] = await Promise.all([
      logoUrl ? fetchAsDataUri(logoUrl) : Promise.resolve(''),
      playerPhotoUri ? fetchAsDataUri(playerPhotoUri) : Promise.resolve(''),
      teamLogoUri ? fetchAsDataUri(teamLogoUri) : Promise.resolve(''),
    ]);

    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-9999px;top:0;width:380px;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif';
    document.body.appendChild(container);
    container.innerHTML = buildShareHTML(pick, {
      won, lost, push, dnp, live, pending, dir, isOver, accentColor,
      nowValue, paceValue, paceLabel, hitPct, lineValue, progress,
      hasScore, finalHome, finalAway, homeTeamName, awayTeamName,
      hasActualPoss, hasProjPoss, showScoreLine, propLabel, elapsed,
      venueTag, matchTime, cardStatusLabel: statusLabel, dirLabel, logoDataUri, photoDataUri, teamLogoDataUri,
    });
    // Only wait for data-URI images (they load instantly; no CORS risk)
    await new Promise(r => setTimeout(r, 120));
    const html2canvas = (await import('html2canvas')).default;
    const canvas = await html2canvas(container.firstElementChild as HTMLElement, {
      scale: 3, backgroundColor: null, useCORS: false, allowTaint: false, logging: false,
    });
    document.body.removeChild(container);
    const blob = await new Promise<Blob | null>(res => canvas.toBlob(res, 'image/png'));
    pendingBlobRef.current = blob;
  };

  const handleSaveImage = async () => {
    setShareSheetVisible(false);
    await shareImageFile();
  };

  const shareImageFile = async (includeText?: string) => {
    const blob = pendingBlobRef.current;
    if (!blob) return;
    const fileName = `reversepicks-${pick.playerName?.replace(/\s+/g, '-') || 'pick'}.png`;
    const file = new File([blob], fileName, { type: 'image/png' });
    const shareData: any = { files: [file] };
    if (includeText) shareData.text = includeText;
    if ((navigator as any).canShare?.(shareData)) {
      try { await navigator.share(shareData); } catch {}
      return;
    }
    // Fallback: download image; if Post-to-X, also open X web composer
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement('a'), { href: url, download: fileName });
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    if (includeText) {
      window.open(`https://twitter.com/intent/tweet?text=${encodeURIComponent(includeText)}`, '_blank', 'noopener,noreferrer');
    }
  };

  const handleShareToX = async () => {
    setShareSheetVisible(false);
    await shareImageFile(tweetText);
  };

  const shareSheet = Platform.OS === 'web' ? (
    <Modal visible={shareSheetVisible} transparent animationType="slide" onRequestClose={() => setShareSheetVisible(false)}>
      <TouchableOpacity style={styles.ssOverlay} activeOpacity={1} onPress={() => setShareSheetVisible(false)}>
        <View style={styles.ssSheet}>
          <Text style={styles.ssTitle}>Share Pick</Text>
          <TouchableOpacity style={styles.ssOption} onPress={handleSaveImage} activeOpacity={0.75}>
            <Ionicons name="download-outline" size={20} color="#fff" />
            <Text style={styles.ssOptionText}>Save Image to Device</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.ssOption} onPress={handleShareToX} activeOpacity={0.75}>
            <Text style={styles.ssXIcon}>𝕏</Text>
            <Text style={styles.ssOptionText}>Post to X (Twitter)</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.ssOption, styles.ssCancel]} onPress={() => setShareSheetVisible(false)} activeOpacity={0.75}>
            <Text style={styles.ssCancelText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </TouchableOpacity>
    </Modal>
  ) : null;

  if (compact) {
    return (
      <>
        <View ref={cardRef} style={styles.compactCard}>
          <View style={[styles.stripe, styles.compactStripe, { backgroundColor: accentColor }]} />
          <View style={styles.compactInner}>
            <View style={styles.compactTopRow}>
              <View style={styles.identity}>
                {playerPhotoUri && !photoFailed ? (
                  <Image source={{ uri: playerPhotoUri }} style={styles.compactAvatar}
                    onError={() => setPhotoFailed(true)} />
                ) : (
                  <View style={[styles.compactAvatar, styles.avatarFallback]}>
                    <Text style={styles.compactAvatarLetter}>{pick.playerName?.charAt(0) || '?'}</Text>
                  </View>
                )}
                <View style={styles.nameBlock}>
                  <Text style={styles.compactPlayerName} numberOfLines={1}>{pick.playerName}</Text>
                  <View style={styles.compactTeamRow}>
                    {teamLogoUri && !teamLogoFailed ? (
                      <Image
                        source={{ uri: teamLogoUri }}
                        style={styles.compactTeamLogo}
                        onError={() => setTeamLogoFailed(true)}
                      />
                    ) : null}
                    <Text style={styles.compactSubText} numberOfLines={1}>
                      {pick.teamName || 'Team'}
                      <Text style={{ color: accentColor === 'rgba(255,255,255,0.18)' ? 'rgba(255,255,255,0.35)' : accentColor }}> · {venueTag}</Text>
                    </Text>
                  </View>
                  {live && pick.fixtureId != null && (
                    <Text style={styles.compactMatchId}>MATCH ID {pick.fixtureId}</Text>
                  )}
                </View>
              </View>
              <View style={styles.compactRightCluster}>
                {Platform.OS === 'web' && onDelete && !captureMode && (
                  // @ts-ignore
                  <button type="button"
                    onClick={(e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
                    onPointerDown={(e: React.PointerEvent) => e.stopPropagation()}
                    onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
                    style={{ all: 'unset', cursor: 'pointer', padding: '2px 4px', display: 'inline-flex', alignItems: 'center' }}>
                    <Ionicons name="trash-outline" size={13} color={Colors.error} />
                  </button>
                )}
                {!captureMode && (
                  <TouchableOpacity
                    onPress={handleShare}
                    style={styles.compactShareBtn}
                    activeOpacity={0.7}
                    accessibilityLabel="Share or save pick image"
                  >
                    {sharing
                      ? <ActivityIndicator size="small" color={Colors.primary} />
                      : <Ionicons name="arrow-up-circle-outline" size={16} color={Colors.primary} />}
                  </TouchableOpacity>
                )}
                {canRefreshSettlement && !captureMode && (
                  <TouchableOpacity
                    onPress={onRefreshSettlement}
                    style={styles.compactSettlementBtn}
                    activeOpacity={0.7}
                    disabled={settlementRefreshing}
                    accessibilityLabel="Confirm final result from provider"
                    accessibilityRole="button"
                  >
                    {settlementRefreshing
                      ? <ActivityIndicator size="small" color={Colors.primary} />
                      : <Ionicons name="checkmark-circle-outline" size={16} color={Colors.primary} />}
                  </TouchableOpacity>
                )}
                <StatusPill
                  won={won} lost={lost} push={push} dnp={dnp}
                  live={live} pending={pending} pendingReview={pendingReview}
                />
                {pick.managerContext?.isRecent === true && !captureMode && (
                  <View style={styles.compactManagerDot}>
                    <Ionicons name="alert-circle-outline" size={12} color="#F59E0B" />
                  </View>
                )}
              </View>
            </View>

            <View style={styles.compactBottomRow}>
              {dir ? (
                <View style={[styles.compactDir, { borderColor: accentColor + '55', backgroundColor: accentColor + '12' }]}>
                  <Text style={[styles.compactDirText, { color: accentColor }]} numberOfLines={1}>{dirLabel}</Text>
                </View>
              ) : <View style={styles.compactDirPlaceholder} />}
              <View style={styles.compactStatsRow}>
                {!pending && (live || nowValue != null) && (
                  <View style={styles.compactStat}>
                    <Text style={styles.compactStatLabel}>{settled && hasVerifiedFinal ? 'FINAL' : live ? 'NOW' : 'PEND'}</Text>
                    <Text style={[styles.compactStatValue, {
                      color: nowValue != null && lineValue != null
                        ? ((isOver && nowValue >= lineValue) || (isUnder && nowValue <= lineValue)) ? '#39FF14' : '#FF3B30'
                        : Colors.text,
                    }]}>{fmt(nowValue)}</Text>
                  </View>
                )}
                <View style={styles.compactStat}>
                  <Text style={styles.compactStatLabel}>LINE</Text>
                  <Text style={styles.compactStatValue}>{fmt(lineValue)}</Text>
                </View>
                <View style={styles.compactStat}>
                  <Text style={styles.compactStatLabel}>PROJ</Text>
                  <Text style={styles.compactStatValue}>{fmt(projValue)}</Text>
                </View>
                {live && paceValue != null && (
                  <View style={styles.compactStat}>
                    <Text style={styles.compactStatLabel}>{paceLabel}</Text>
                    <Text style={[styles.compactStatValue, { color: Colors.primary }]}>{fmt(paceValue)}</Text>
                  </View>
                )}
              </View>
            </View>
            {live && nowValue == null && (
              <Text style={styles.liveStatNote}>NOW — · awaiting live stat</Text>
            )}
            <Text style={[styles.compactStatus, live && styles.liveStatus]}>{statusLabel}</Text>
          </View>
        </View>
        {shareSheet}
      </>
    );
  }

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <View
      ref={cardRef}
      style={styles.card}
    >
      {/* Left accent stripe — color = direction / result */}
      <View style={[styles.stripe, { backgroundColor: accentColor }]} />

      <View style={styles.inner}>
        {/* ── Top row: identity + badge ─────────────────────── */}
        <View style={styles.topRow}>
          <View style={styles.identity}>
            {playerPhotoUri && !photoFailed ? (
              <Image source={{ uri: playerPhotoUri }} style={styles.avatar}
                onError={() => setPhotoFailed(true)} />
            ) : (
              <View style={[styles.avatar, styles.avatarFallback]}>
                <Text style={styles.avatarLetter}>{pick.playerName?.charAt(0) || '?'}</Text>
              </View>
            )}
            <View style={styles.nameBlock}>
              <Text style={styles.playerName} numberOfLines={1}>{pick.playerName}</Text>
              <View style={styles.subRow}>
                {teamLogoUri && !teamLogoFailed ? (
                  <Image source={{ uri: teamLogoUri }} style={styles.teamCrest}
                    onError={() => setTeamLogoFailed(true)} />
                ) : null}
                <Text style={styles.subText} numberOfLines={1}>
                  {pick.teamName || 'Team'}
                  <Text style={{ color: accentColor === 'rgba(255,255,255,0.18)' ? 'rgba(255,255,255,0.35)' : accentColor }}> · {venueTag}</Text>
                </Text>
              </View>
            </View>
          </View>

          <View style={styles.rightCluster}>
            {Platform.OS === 'web' && onDelete && !captureMode && (
              // @ts-ignore
              <button type="button"
                onClick={(e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); onDelete(); }}
                onPointerDown={(e: React.PointerEvent) => e.stopPropagation()}
                onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
                style={{ all: 'unset', cursor: 'pointer', padding: '3px 5px', display: 'inline-flex', alignItems: 'center' }}>
                <Ionicons name="trash-outline" size={14} color={Colors.error} />
              </button>
            )}
            {!captureMode && (
              <TouchableOpacity onPress={handleShare} style={styles.shareBtn} activeOpacity={0.7}>
                {sharing
                  ? <ActivityIndicator size="small" color={Colors.primary} />
                  : <Ionicons name="arrow-up-circle-outline" size={17} color={Colors.primary} />}
              </TouchableOpacity>
            )}
            {canRefreshSettlement && !captureMode && (
              <TouchableOpacity
                onPress={onRefreshSettlement}
                style={styles.shareBtn}
                activeOpacity={0.7}
                disabled={settlementRefreshing}
                accessibilityLabel="Confirm final result from provider"
                accessibilityRole="button"
              >
                {settlementRefreshing
                  ? <ActivityIndicator size="small" color={Colors.primary} />
                  : <Ionicons name="checkmark-circle-outline" size={17} color={Colors.primary} />}
              </TouchableOpacity>
            )}
            <StatusPill
              won={won} lost={lost} push={push} dnp={dnp}
              live={live} pending={pending} pendingReview={pendingReview}
            />
            {pick.managerContext?.isRecent === true && !captureMode && (
              <TouchableOpacity
                style={pill.manager}
                activeOpacity={0.8}
                onPress={(e) => {
                  (e as any).stopPropagation?.();
                  onManagerBadgePress?.();
                }}
              >
                <Ionicons name="alert-circle-outline" size={8} color="#F59E0B" style={{ marginRight: 3 }} />
                <Text style={[pill.txt, { color: '#F59E0B' }]}>MGR</Text>
              </TouchableOpacity>
            )}
            {pick.managerChangedAfterPick === true && !settled && !captureMode && (
              <View style={pill.rerun}>
                <Ionicons name="refresh-outline" size={8} color="#FF6B35" style={{ marginRight: 3 }} />
                <Text style={[pill.txt, { color: '#FF6B35' }]}>RE-RUN</Text>
              </View>
            )}
          </View>
        </View>

        {/* ── Direction banner ──────────────────────────────── */}
        {dir && (
          <View style={[styles.dirBanner, { borderColor: accentColor + '40', backgroundColor: accentColor + '10' }]}>
            <Text style={[styles.dirText, { color: accentColor }]}>{dirLabel}</Text>
            {hitPct != null && (
              <Text style={[styles.hitPctText, { color: accentColor }]}>{Math.round(hitPct)}% hit prob</Text>
            )}
          </View>
        )}

        {/* ── Stats row ────────────────────────────────────── */}
        <View style={styles.statsRow}>
          {!pending && (live || nowValue != null) && (
            <View style={styles.statBlock}>
              <Text style={styles.statLbl}>{settled && hasVerifiedFinal ? 'FINAL' : live ? 'NOW' : 'PENDING'}</Text>
              <Text style={[styles.statVal, {
                color: nowValue != null && lineValue != null
                  ? ((isOver && nowValue >= lineValue) || (isUnder && nowValue <= lineValue)) ? '#39FF14' : '#FF3B30'
                  : Colors.text
              }]}>{fmt(nowValue)}</Text>
            </View>
          )}
          <View style={styles.statBlock}>
            <Text style={styles.statLbl}>LINE</Text>
            <Text style={styles.statVal}>{fmt(lineValue)}</Text>
          </View>
          <View style={styles.statBlock}>
            <Text style={styles.statLbl}>PROJ</Text>
            <Text style={styles.statVal}>{fmt(projValue)}</Text>
          </View>
          {live && paceValue != null && (
            <View style={styles.statBlock}>
              <Text style={styles.statLbl}>{paceLabel}</Text>
              <Text style={[styles.statVal, { color: Colors.primary }]}>{fmt(paceValue)}</Text>
            </View>
          )}
        </View>
        {live && nowValue == null && (
          <Text style={styles.liveStatNote}>NOW — · awaiting live stat</Text>
        )}

        {/* ── Progress track ─────────────────────────────── */}
        {progress != null && (
          <View style={styles.track}>
            <View style={[styles.trackFill, { width: `${progress}%` as any, backgroundColor: accentColor }]} />
            <View style={styles.trackMid} />
          </View>
        )}

        {/* ── Bottom row ─────────────────────────────────── */}
        <View style={styles.footRow}>
          <Text style={styles.footTime} numberOfLines={1}>
            {statusLabel}
          </Text>
          {live && pick.fixtureId != null && (
            <Text style={styles.matchId} numberOfLines={1}>MATCH ID {pick.fixtureId}</Text>
          )}
          {showScoreLine && (
            <Text style={styles.footScore} numberOfLines={1}>
              {hasScore
                ? `${settled ? 'FT ' : ''}${homeTeamName} ${finalHome}–${finalAway} ${awayTeamName}`
                : `${homeTeamName} vs ${awayTeamName}`}
              {hasActualPoss ? ` · ${Math.round(pick.homePoss!)}%/${Math.round(pick.awayPoss!)}%` : ''}
            </Text>
          )}
        </View>
      </View>

      {shareSheet}
    </View>
  );
}

// ─── Share card HTML (broadcast / intel style) ───────────────────────────────
function buildShareHTML(pick: Pick, s: Record<string, any>): string {
  const {
    won, lost, push, dnp, live, pending, dir, isOver, accentColor,
    nowValue, paceValue, paceLabel, hitPct, lineValue, progress,
    hasScore, finalHome, finalAway, homeTeamName, awayTeamName,
    hasActualPoss, showScoreLine, propLabel, elapsed,
    venueTag, matchTime, cardStatusLabel, dirLabel, logoDataUri, photoDataUri, teamLogoDataUri,
  } = s;

  // Use pre-fetched base64 data URIs — avoids CORS taint on html2canvas
  const photoUrl = photoDataUri || '';
  const teamLogo = teamLogoDataUri || '';
  const accent = accentColor;

  // Status label
  const statusLabel = won ? 'HIT ✓' : lost ? 'MISS' : push ? 'PUSH' : dnp ? 'DNP' : live ? '● LIVE' : pending ? 'PENDING' : '';
  const statusBg = won ? '#39FF14' : lost ? '#FF3B30' : push ? '#0A84FF' : dnp ? '#FF9500'
    : live ? 'rgba(255,59,48,0.18)' : 'rgba(255,255,255,0.1)';
  const statusColor = won ? '#000' : '#fff';

  // Progress bar
  const progHTML = progress != null
    ? `<div style="position:relative;height:5px;background:rgba(255,255,255,0.08);border-radius:3px;margin:12px 0 0">
        <div style="position:absolute;left:0;top:0;bottom:0;border-radius:3px;width:${progress}%;background:${accent}"></div>
        <div style="position:absolute;left:50%;top:-3px;bottom:-3px;width:2px;background:rgba(255,255,255,0.5)"></div>
       </div>` : '';

  // Score / context
  const ctxHTML = showScoreLine
    ? `<div style="font-size:10px;color:rgba(255,255,255,0.45);margin-top:8px;white-space:nowrap;overflow:hidden">
        ${hasScore ? `${won||lost||push?'FT ':''}${homeTeamName} ${finalHome}–${finalAway} ${awayTeamName}` : `${homeTeamName} vs ${awayTeamName}`}
        ${hasActualPoss ? ` · ${Math.round(pick.homePoss!)}%/${Math.round(pick.awayPoss!)}%` : ''}
       </div>` : '';

  const timeStr = cardStatusLabel || matchTime || (elapsed != null ? `${elapsed}'` : live ? '● LIVE' : '');

  // Stat blocks
  const statBlockHTML = (label: string, value: string, color = '#fff') =>
    `<div style="flex:1;text-align:center">
      <div style="font-size:8px;font-weight:700;letter-spacing:0.8px;color:rgba(255,255,255,0.35);margin-bottom:4px">${label}</div>
      <div style="font-size:22px;font-weight:900;color:${color};line-height:1">${value}</div>
     </div>`;

  const nowColor = nowValue != null && lineValue != null
    ? ((isOver && nowValue >= lineValue) ? '#39FF14' : '#FF3B30') : '#fff';

  let statsHTML = '';
  if (!pending && nowValue != null) statsHTML += statBlockHTML(won || !live ? 'FINAL' : 'NOW', fmt(nowValue), nowColor);
  statsHTML += statBlockHTML('LINE', fmt(lineValue));
  statsHTML += statBlockHTML('PROJ', fmt(pick.projection ?? pick.projectedValue ?? null));
  if (live && paceValue != null) {
    statsHTML += statBlockHTML(paceLabel, fmt(paceValue), '#39FF14');
  }
  if (hitPct != null) statsHTML += statBlockHTML('HIT%', `${Math.round(hitPct)}%`);

  return `
<div style="width:380px;background:#080808;border-radius:18px;overflow:hidden;position:relative;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif">
  <!-- Left accent bar -->
  <div style="position:absolute;left:0;top:0;bottom:0;width:4px;background:${accent}"></div>


  <div style="position:relative;padding:18px 18px 16px 20px">
    <!-- Top row: branding + status -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <div style="display:flex;align-items:center;gap:7px">
        ${logoDataUri
          ? `<img src="${logoDataUri}" style="width:28px;height:28px;object-fit:contain;image-rendering:crisp-edges" />`
          : `<div style="width:8px;height:8px;border-radius:4px;background:${accent}"></div>`}
        <span style="font-size:10px;font-weight:900;letter-spacing:1.5px;color:rgba(255,255,255,0.5)">REVERSE PICKS</span>
      </div>
      ${statusLabel ? `<div style="background:${statusBg};color:${statusColor};padding:3px 10px;border-radius:20px;font-size:9px;font-weight:900;letter-spacing:0.8px">${statusLabel}</div>` : ''}
    </div>

    <!-- Player identity -->
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;max-width:58%">
      ${photoUrl
        ? `<img src="${photoUrl}" style="width:44px;height:44px;border-radius:22px;object-fit:cover;border:2px solid ${accent};flex-shrink:0"/>`
        : `<div style="width:44px;height:44px;border-radius:22px;background:#1A1A1A;border:2px solid ${accent};display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:900;color:${accent};flex-shrink:0">${pick.playerName?.charAt(0) || '?'}</div>`}
      <div style="min-width:0">
        <div style="font-size:20px;font-weight:900;color:#fff;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${pick.playerName}</div>
        <div style="display:flex;align-items:center;gap:5px;margin-top:3px">
          ${teamLogo ? `<img src="${teamLogo}" style="width:13px;height:13px;object-fit:contain"/>` : ''}
          <span style="font-size:11px;font-weight:600;color:rgba(255,255,255,0.45)">${pick.teamName || ''}</span>
          <span style="font-size:11px;font-weight:800;color:${accent}">· ${venueTag}</span>
        </div>
      </div>
    </div>

    <!-- Direction banner -->
    ${dir ? `<div style="background:${accent}18;border:1px solid ${accent}35;border-radius:8px;padding:7px 12px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between">
      <span style="font-size:14px;font-weight:900;color:${accent};letter-spacing:0.5px">${dirLabel}</span>
      ${hitPct != null ? `<span style="font-size:11px;font-weight:700;color:${accent}80">${Math.round(hitPct)}% hit prob</span>` : ''}
    </div>` : ''}

    <!-- Stats -->
    <div style="display:flex;gap:0;margin-bottom:2px">${statsHTML}</div>

    ${progHTML}

    <!-- Footer -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-top:12px">
      <span style="font-size:10px;color:rgba(255,255,255,0.3);font-weight:600">${timeStr}</span>
    </div>
    ${ctxHTML}
  </div>
</div>`;
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    backgroundColor: '#0A0A0A',
    borderRadius: 10,
    marginBottom: 5,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
    elevation: 3,
  },
  compactCard: {
    flexDirection: 'row',
    minHeight: 62,
    backgroundColor: '#0A0A0A',
    borderRadius: 9,
    marginBottom: 2,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
  },
  compactStripe: { width: 3, borderTopLeftRadius: 9, borderBottomLeftRadius: 9 },
  compactInner: { flex: 1, paddingHorizontal: 8, paddingVertical: 4 },
  compactTopRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    minHeight: 31,
  },
  compactAvatar: {
    width: 25, height: 25, borderRadius: 12.5,
    backgroundColor: '#1A1A1A', borderWidth: 1.25, borderColor: 'rgba(57,255,20,0.3)',
  },
  compactAvatarLetter: { color: Colors.primary, fontSize: 11, fontWeight: '900' },
  compactPlayerName: { color: '#fff', fontSize: 12.5, fontWeight: '800', letterSpacing: -0.2 },
  compactSubText: { color: 'rgba(255,255,255,0.4)', fontSize: 8.5, fontWeight: '600', marginTop: 1 },
  compactMatchId: { color: Colors.primary, fontSize: 7.5, fontWeight: '800', letterSpacing: 0.35, marginTop: 1 },
  compactRightCluster: { flexDirection: 'row', alignItems: 'center', gap: 4, marginLeft: 5 },
  compactShareBtn: {
    width: 22, height: 22, borderRadius: 11,
    backgroundColor: 'rgba(57,255,20,0.08)', alignItems: 'center', justifyContent: 'center',
  },
  compactSettlementBtn: {
    width: 22, height: 22, borderRadius: 11,
    backgroundColor: 'rgba(57,255,20,0.14)', alignItems: 'center', justifyContent: 'center',
  },
  compactManagerDot: { width: 18, height: 18, alignItems: 'center', justifyContent: 'center' },
  compactTeamRow: { flexDirection: 'row', alignItems: 'center', marginTop: 1, minWidth: 0 },
  compactTeamLogo: { width: 11, height: 11, marginRight: 4 },
  compactBottomRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: 2, minHeight: 23,
  },
  compactDir: {
    maxWidth: '58%', borderWidth: 1, borderRadius: 5,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  compactDirPlaceholder: { flex: 1 },
  compactDirText: { fontSize: 9, fontWeight: '900', letterSpacing: 0.25 },
  compactStatsRow: { flexDirection: 'row', alignItems: 'center', gap: 9, marginLeft: 6 },
  compactStat: { alignItems: 'center', minWidth: 25 },
  compactStatLabel: { color: 'rgba(255,255,255,0.32)', fontSize: 6.5, fontWeight: '800', letterSpacing: 0.35 },
  compactStatValue: { color: '#fff', fontSize: 12.5, fontWeight: '900', lineHeight: 14 },
  compactStatus: { color: 'rgba(255,255,255,0.38)', fontSize: 8.5, fontWeight: '700', marginTop: 3 },
  liveStatus: { color: '#FF3B30', letterSpacing: 0.3 },
  liveStatNote: { color: 'rgba(255,255,255,0.42)', fontSize: 8.5, fontWeight: '600', marginTop: 2 },
  stripe: {
    width: 3,
    alignSelf: 'stretch',
    borderTopLeftRadius: 10,
    borderBottomLeftRadius: 10,
  },
  inner: { flex: 1, padding: 8 },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  identity: { flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 8 },
  avatar: {
    width: 30, height: 30, borderRadius: 15,
    backgroundColor: '#1A1A1A',
    borderWidth: 1.5, borderColor: 'rgba(57,255,20,0.3)',
  },
  avatarFallback: { alignItems: 'center', justifyContent: 'center' },
  avatarLetter: { color: Colors.primary, fontSize: 13, fontWeight: '900' },
  nameBlock: { marginLeft: 7, flex: 1 },
  playerName: { color: '#fff', fontSize: 14, fontWeight: '800', letterSpacing: -0.3 },
  subRow: { flexDirection: 'row', alignItems: 'center', marginTop: 1 },
  teamCrest: { width: 11, height: 11, marginRight: 4 },
  subText: { color: 'rgba(255,255,255,0.4)', fontSize: 9.5, fontWeight: '600' },
  rightCluster: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  shareBtn: {
    width: 26, height: 26, borderRadius: 13,
    backgroundColor: 'rgba(57,255,20,0.08)',
    alignItems: 'center', justifyContent: 'center',
  },
  dirBanner: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    borderWidth: 1, borderRadius: 7,
    paddingHorizontal: 9, paddingVertical: 5,
    marginBottom: 7,
  },
  dirText: { fontSize: 11, fontWeight: '900', letterSpacing: 0.4 },
  hitPctText: { fontSize: 9, fontWeight: '700', opacity: 0.7 },
  statsRow: {
    flexDirection: 'row',
    justifyContent: 'flex-start',
    gap: 16,
    marginBottom: 4,
  },
  statBlock: { alignItems: 'center' },
  statLbl: { color: 'rgba(255,255,255,0.35)', fontSize: 7.5, fontWeight: '700', letterSpacing: 0.6, marginBottom: 1 },
  statVal: { fontSize: 14, fontWeight: '900', color: '#fff', letterSpacing: -0.3 },
  track: {
    position: 'relative',
    height: 3,
    backgroundColor: 'rgba(255,255,255,0.07)',
    borderRadius: 2,
    marginTop: 4,
    marginBottom: 4,
  },
  trackFill: { position: 'absolute', left: 0, top: 0, bottom: 0, borderRadius: 2 },
  trackMid: { position: 'absolute', left: '50%', top: -2, bottom: -2, width: 1.5, backgroundColor: 'rgba(255,255,255,0.4)' },
  footRow: { marginTop: 4, gap: 2 },
  footTime: { color: 'rgba(255,255,255,0.3)', fontSize: 9, fontWeight: '600' },
  matchId: { color: Colors.primary, fontSize: 9, fontWeight: '800', letterSpacing: 0.25, marginLeft: 8 },
  footScore: { color: 'rgba(255,255,255,0.25)', fontSize: 8.5, fontWeight: '500' },
  liveBtnText: { color: Colors.primary, fontSize: 9, fontWeight: '800' },
  // Share sheet
  ssOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  ssSheet: {
    backgroundColor: '#141414', borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingTop: 20, paddingBottom: 32, paddingHorizontal: 16,
  },
  ssTitle: { fontSize: 16, fontWeight: '800', color: Colors.text, textAlign: 'center', marginBottom: 18 },
  ssOption: {
    flexDirection: 'row', alignItems: 'center', gap: 14,
    backgroundColor: '#1E1E1E', borderRadius: 12, padding: 16, marginBottom: 10,
  },
  ssOptionText: { fontSize: 15, fontWeight: '600', color: Colors.text },
  ssXIcon: { fontSize: 17, fontWeight: '900', color: Colors.text, width: 20, textAlign: 'center' },
  ssCancel: { backgroundColor: 'transparent', justifyContent: 'center', marginTop: 4 },
  ssCancelText: { fontSize: 15, fontWeight: '600', color: Colors.textSecondary, textAlign: 'center', flex: 1 },
});
