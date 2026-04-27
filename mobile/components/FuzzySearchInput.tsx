import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, FlatList,
  StyleSheet, ActivityIndicator, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import { searchTeams, searchPlayersQuick, searchLeagues, TeamSearchResult, PlayerSearchResult, LeagueSearchResult, LEAGUES } from '@/lib/api';

type SearchType = 'teams' | 'players' | 'leagues';

export interface FuzzyTeamResult {
  teamId: number;
  teamName: string;
  leagueId: number;
}

export interface FuzzyPlayerResult {
  playerId: number;
  playerName: string;
  teamId: number;
  teamName: string;
  leagueId: number;
  position?: string;
}

export interface FuzzyLeagueResult {
  id: number;
  name: string;
  country: string;
}

interface FuzzySearchInputProps {
  value: string;
  onChangeText: (text: string) => void;
  onSelectTeam?: (result: FuzzyTeamResult) => void;
  onSelectPlayer?: (result: FuzzyPlayerResult) => void;
  onSelectLeague?: (result: FuzzyLeagueResult) => void;
  searchType: SearchType;
  leagueId?: number;
  placeholder?: string;
  style?: object;
  inputStyle?: object;
  autoFocus?: boolean;
  returnKeyType?: 'done' | 'search' | 'next';
  onSubmitEditing?: () => void;
}

const IS_WEB = Platform.OS === 'web';
const INPUT_STYLE = IS_WEB ? { outlineWidth: 0 } as object : {};
const DEBOUNCE_MS = 280;

function leagueName(leagueId: number): string {
  return LEAGUES.find(l => l.id === leagueId)?.name || '';
}

export default function FuzzySearchInput({
  value,
  onChangeText,
  onSelectTeam,
  onSelectPlayer,
  onSelectLeague,
  searchType,
  leagueId,
  placeholder = 'Search...',
  style,
  inputStyle,
  autoFocus = false,
  returnKeyType = 'done',
  onSubmitEditing,
}: FuzzySearchInputProps) {
  const [results, setResults] = useState<(TeamSearchResult | PlayerSearchResult | LeagueSearchResult)[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQueryRef = useRef('');
  const inputRowRef = useRef<View>(null);
  // Web: fixed-position coords measured via measureInWindow + visual-viewport aware sizing.
  // `placement: 'below'` opens under the input; 'above' flips it on top when the keyboard /
  // iOS Safari URL pill leaves no usable room below. `maxHeight` is clamped to whatever the
  // visible viewport can actually show so no list item ever lands under the keyboard or URL bar.
  const [fixedPos, setFixedPos] = useState<{
    top?: number;
    bottom?: number;
    left: number;
    width: number;
    maxHeight: number;
    placement: 'below' | 'above';
  } | null>(null);
  // Tracks whether `measureInputRow` has run at least once for the current open
  // dropdown. Lets us tell "measurement pending" (use absolute fallback) apart
  // from "measured but no usable space" (hide entirely).
  const [measured, setMeasured] = useState(false);

  const measureInputRow = useCallback(() => {
    if (!IS_WEB || !inputRowRef.current) return;
    inputRowRef.current.measureInWindow((x: number, y: number, width: number, height: number) => {
      // visualViewport reflects the area NOT covered by the on-screen keyboard. Fall back to
      // window.innerHeight on browsers without it (older Android, some embedded webviews).
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const vv: any = typeof window !== 'undefined' ? (window as any).visualViewport : null;
      const winH = typeof window !== 'undefined' ? window.innerHeight : 800;
      const vvHeight = vv?.height ?? winH;
      const vvOffsetTop = vv?.offsetTop ?? 0;
      // Detect keyboard-open: visualViewport shrinks substantially when iOS/Android show the
      // soft keyboard. When open, iOS Safari ALSO adds a ~56px form accessory bar (the
      // up/down/AutoFill pill) on top of the page that visualViewport does NOT subtract.
      // We pad bottom space accordingly so the dropdown never lands behind that bar.
      const keyboardOpen = winH - vvHeight > 100;
      const TOP_PAD = 16;                     // breathing room from URL bar / status area
      const BOTTOM_PAD = keyboardOpen ? 64 : 16; // covers iOS form accessory bar when keyboard up
      const inputTopInVV = y - vvOffsetTop;
      const inputBottomInVV = inputTopInVV + height;
      const spaceBelow = vvHeight - inputBottomInVV - BOTTOM_PAD;
      const spaceAbove = inputTopInVV - TOP_PAD;
      const MIN_USABLE = 80; // below this, even one row + scroll handle can't fit cleanly
      const MIN_BELOW = 96; // prefer below if at least ~2 rows fit
      // True clamp — never let maxHeight exceed the actual visible space.
      const heightBelow = Math.min(260, Math.max(0, spaceBelow));
      const heightAbove = Math.min(260, Math.max(0, spaceAbove));
      const useBelow = spaceBelow >= MIN_BELOW || spaceBelow >= spaceAbove;
      if (useBelow && heightBelow >= MIN_USABLE) {
        setFixedPos({
          top: y + height,
          left: x,
          width,
          maxHeight: heightBelow,
          placement: 'below',
        });
      } else if (heightAbove >= MIN_USABLE) {
        // Flip above: anchor the dropdown's BOTTOM just above the input.
        const bottomFromTop = vvHeight + vvOffsetTop - y + 4;
        setFixedPos({
          bottom: bottomFromTop,
          left: x,
          width,
          maxHeight: heightAbove,
          placement: 'above',
        });
      } else {
        // Viewport too cramped (split-screen, tiny landscape) — don't position the
        // dropdown into a region that would be obscured. Hide until layout recovers.
        setFixedPos(null);
      }
      setMeasured(true);
    });
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) { setResults([]); setShowDropdown(false); return; }
    setLoading(true);
    try {
      if (searchType === 'teams') {
        const data = await searchTeams(q, leagueId);
        const r = data.results || [];
        setResults(r);
        setShowDropdown(r.length > 0);
      } else if (searchType === 'leagues') {
        const data = await searchLeagues(q);
        const r = data.leagues || [];
        setResults(r);
        setShowDropdown(r.length > 0);
      } else {
        const data = await searchPlayersQuick(q, leagueId);
        const mapped = (data.players || []).map((p: Record<string, unknown>) => ({
          playerId: (p.id as number) || 0,
          playerName: (p.name as string) || '',
          teamId: (p.teamId as number) || 0,
          teamName: (p.teamName as string) || (p.team as string) || '',
          leagueId: (p.leagueId as number) || 0,
          position: (p.position as string) || '',
        }));
        setResults(mapped);
        setShowDropdown(mapped.length > 0);
      }
    } catch {
      setResults([]); setShowDropdown(false);
    } finally {
      setLoading(false);
    }
  }, [searchType, leagueId]);

  const handleChange = (text: string) => {
    onChangeText(text);
    lastQueryRef.current = text;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (text.length < 2) { setResults([]); setShowDropdown(false); return; }
    debounceRef.current = setTimeout(() => {
      if (lastQueryRef.current === text) doSearch(text);
    }, DEBOUNCE_MS);
  };

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  // Re-measure aggressively while the dropdown is open. iOS Safari auto-scrolls
  // the page after focus to bring the input into view; that shift fires neither
  // visualViewport.resize nor (reliably) any scroll event in time, so the
  // initial measurement points at the input's PRE-scroll location and the
  // dropdown ends up far away. We use a short rAF loop to follow the input
  // through that animation, then settle into pure event-driven re-measures.
  useEffect(() => {
    if (!showDropdown || !IS_WEB) {
      setMeasured(false);
      return;
    }
    measureInputRow();
    let rafId: number | null = null;
    const start = Date.now();
    const tick = () => {
      // Track for ~900ms after open — covers iOS scroll-into-view (~600ms).
      if (Date.now() - start > 900) return;
      measureInputRow();
      rafId = typeof window !== 'undefined' ? window.requestAnimationFrame(tick) : null;
    };
    rafId = typeof window !== 'undefined' ? window.requestAnimationFrame(tick) : null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const vv: any = typeof window !== 'undefined' ? (window as any).visualViewport : null;
    const onChange = () => measureInputRow();
    vv?.addEventListener?.('resize', onChange);
    vv?.addEventListener?.('scroll', onChange);
    if (typeof window !== 'undefined') {
      window.addEventListener('resize', onChange);
      // Capture-phase scroll listener catches scrolls inside any nested container,
      // so the dropdown follows the input even when the parent ScrollView moves.
      window.addEventListener('scroll', onChange, true);
    }
    return () => {
      if (rafId != null && typeof window !== 'undefined') window.cancelAnimationFrame(rafId);
      vv?.removeEventListener?.('resize', onChange);
      vv?.removeEventListener?.('scroll', onChange);
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', onChange);
        window.removeEventListener('scroll', onChange, true);
      }
    };
  }, [showDropdown, measureInputRow]);

  const handleSelectTeam = (item: TeamSearchResult) => {
    onChangeText(item.teamName); setShowDropdown(false); setResults([]); onSelectTeam?.(item);
  };
  const handleSelectPlayer = (item: FuzzyPlayerResult) => {
    onChangeText(item.playerName); setShowDropdown(false); setResults([]); onSelectPlayer?.(item);
  };
  const handleSelectLeague = (item: LeagueSearchResult) => {
    onChangeText(item.name); setShowDropdown(false); setResults([]); onSelectLeague?.(item);
  };

  const renderTeamItem = ({ item }: { item: TeamSearchResult }) => {
    const lg = leagueName(item.leagueId);
    return (
      <TouchableOpacity style={styles.dropdownItem} onPress={() => handleSelectTeam(item)} activeOpacity={0.7}>
        <Ionicons name="shield-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
        <View style={styles.dropdownTextWrap}>
          <Text style={styles.dropdownMain} numberOfLines={1}>{item.teamName}</Text>
          {lg ? <Text style={styles.dropdownSub} numberOfLines={1}>{lg}</Text> : null}
        </View>
      </TouchableOpacity>
    );
  };

  const renderPlayerItem = ({ item }: { item: FuzzyPlayerResult }) => {
    const sub = [item.teamName, item.position].filter(Boolean).join(' · ');
    return (
      <TouchableOpacity style={styles.dropdownItem} onPress={() => handleSelectPlayer(item)} activeOpacity={0.7}>
        <Ionicons name="person-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
        <View style={styles.dropdownTextWrap}>
          <Text style={styles.dropdownMain} numberOfLines={1}>{item.playerName}</Text>
          {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
        </View>
      </TouchableOpacity>
    );
  };

  const renderLeagueItem = ({ item }: { item: LeagueSearchResult }) => (
    <TouchableOpacity style={styles.dropdownItem} onPress={() => handleSelectLeague(item)} activeOpacity={0.7}>
      <Ionicons name="trophy-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
      <View style={styles.dropdownTextWrap}>
        <Text style={styles.dropdownMain} numberOfLines={1}>{item.name}</Text>
        {item.country ? <Text style={styles.dropdownSub} numberOfLines={1}>{item.country}</Text> : null}
      </View>
    </TouchableOpacity>
  );

  const renderList = (maxH: number = 220) => {
    if (searchType === 'teams') return (
      <FlatList data={results as TeamSearchResult[]} keyExtractor={(_, i) => String(i)}
        renderItem={renderTeamItem} keyboardShouldPersistTaps="always"
        scrollEnabled style={{ maxHeight: maxH }} />
    );
    if (searchType === 'leagues') return (
      <FlatList data={results as LeagueSearchResult[]} keyExtractor={(_, i) => String(i)}
        renderItem={renderLeagueItem} keyboardShouldPersistTaps="always"
        scrollEnabled style={{ maxHeight: maxH }} />
    );
    return (
      <FlatList data={results as FuzzyPlayerResult[]} keyExtractor={(_, i) => String(i)}
        renderItem={renderPlayerItem} keyboardShouldPersistTaps="always"
        scrollEnabled style={{ maxHeight: maxH }} />
    );
  };

  const shouldShow = showDropdown && results.length > 0;

  return (
    <View style={[styles.container, style]}>
      <View ref={inputRowRef} style={styles.inputRow}>
        <TextInput
          style={[styles.input, inputStyle, INPUT_STYLE]}
          value={value}
          onChangeText={handleChange}
          placeholder={placeholder}
          placeholderTextColor={Colors.textTertiary}
          autoFocus={autoFocus}
          autoCorrect={false}
          autoCapitalize="words"
          returnKeyType={returnKeyType}
          onSubmitEditing={() => { setShowDropdown(false); onSubmitEditing?.(); }}
          onFocus={() => {
            measureInputRow();
            if (value.length >= 2 && results.length > 0) setShowDropdown(true);
          }}
          onBlur={() => { setTimeout(() => setShowDropdown(false), 200); }}
        />
        {loading && <ActivityIndicator size="small" color={Colors.primary} style={styles.spinner} />}
        {!loading && value.length > 0 && (
          <TouchableOpacity onPress={() => { onChangeText(''); setResults([]); setShowDropdown(false); }} style={styles.clearBtn}>
            <Ionicons name="close-circle" size={15} color="#555" />
          </TouchableOpacity>
        )}
      </View>

      {/* Web: render with position:fixed so it escapes overflow:hidden/scroll parents */}
      {IS_WEB && shouldShow && fixedPos && (
        <View style={[styles.dropdownBase, {
          position: 'fixed' as any,
          left: fixedPos.left,
          width: fixedPos.width,
          maxHeight: fixedPos.maxHeight,
          zIndex: 99999,
          ...(fixedPos.placement === 'below'
            ? { top: fixedPos.top }
            : { bottom: fixedPos.bottom }),
        }]}>
          {renderList(fixedPos.maxHeight)}
        </View>
      )}

      {/* Web fallback only while measurement is pending; if measurement returned
          null (cramped viewport) we deliberately render nothing so the list never
          lands in a region hidden by the keyboard or URL pill. */}
      {IS_WEB && shouldShow && !fixedPos && !measured && (
        <View style={[styles.dropdownBase, styles.dropdownAbsolute]}>
          {renderList()}
        </View>
      )}

      {/* Native: always absolute */}
      {!IS_WEB && shouldShow && (
        <View style={[styles.dropdownBase, styles.dropdownAbsolute]}>
          {renderList()}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { position: 'relative', zIndex: 100 },
  inputRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#1a1a1a', borderRadius: 8,
    borderWidth: 1, borderColor: '#2a2a2a', paddingHorizontal: 10,
  },
  input: {
    flex: 1, height: 40, color: Colors.text,
    fontSize: 16, fontFamily: 'Inter_400Regular',
  },
  spinner: { marginLeft: 6 },
  clearBtn: { marginLeft: 4, padding: 2 },
  dropdownBase: {
    backgroundColor: '#111', borderRadius: 8,
    borderWidth: 1, borderColor: '#2a2a2a',
    overflow: 'hidden',
    shadowColor: '#000', shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.6, shadowRadius: 8, elevation: 12,
  },
  dropdownAbsolute: {
    position: 'absolute', top: 44, left: 0, right: 0, zIndex: 200,
  },
  dropdownItem: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 10, paddingHorizontal: 12,
    borderBottomWidth: 1, borderBottomColor: '#1e1e1e',
  },
  dropdownIcon: { marginRight: 8 },
  dropdownTextWrap: { flex: 1 },
  dropdownMain: { color: Colors.text, fontSize: 13, fontFamily: 'Inter_500Medium' },
  dropdownSub: { color: Colors.textSecondary, fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 1 },
});
