import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, Image,
  StyleSheet, ActivityIndicator, Platform, ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import {
  searchTeams, searchPlayersQuick, searchLeagues,
  searchCs2Players, searchCs2Teams, searchWtaPlayers,
  searchNbaPlayers, searchNhlPlayers, searchMlbPlayers, searchNflPlayers,
  TeamSearchResult, PlayerSearchResult, LeagueSearchResult, LEAGUES,
  Cs2Player, Cs2Team, WtaPlayer, NbaPlayer, NhlPlayer, MlbPlayer, NflPlayer,
} from '@/lib/api';

export type SearchType =
  | 'teams' | 'players' | 'leagues'
  | 'all_players'
  | 'cs2_players' | 'cs2_teams' | 'wta_players'
  | 'nba_players' | 'nhl_players' | 'mlb_players' | 'nfl_players';

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
  ownerPlayerPhoto?: string;
  ownerTeamLogo?: string;
}

export interface UniversalPlayerResult extends FuzzyPlayerResult {
  sport: 'soccer' | 'mlb' | 'nfl';
  raw?: any;
}

export interface FuzzyLeagueResult {
  id: number;
  name: string;
  country: string;
}

export type StaticItem = {
  id: number | string;
  primary: string;
  secondary?: string;
  raw?: any;
};

interface FuzzySearchInputProps {
  value: string;
  onChangeText: (text: string) => void;
  searchType: SearchType;
  leagueId?: number;
  placeholder?: string;
  style?: object;
  inputStyle?: object;
  autoFocus?: boolean;
  returnKeyType?: 'done' | 'search' | 'next';
  onSubmitEditing?: () => void;
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  confirmed?: boolean;
  staticItems?: StaticItem[];
  ownerSession?: { email: string; token: string };

  onSelectTeam?: (result: FuzzyTeamResult) => void;
  onSelectPlayer?: (result: FuzzyPlayerResult) => void;
  onSelectAllPlayer?: (result: UniversalPlayerResult) => void;
  onSelectLeague?: (result: FuzzyLeagueResult) => void;
  onSelectCs2Player?: (p: Cs2Player) => void;
  onSelectCs2Team?: (t: Cs2Team) => void;
  onSelectWtaPlayer?: (p: WtaPlayer) => void;
  onSelectNbaPlayer?: (p: NbaPlayer) => void;
  onSelectNhlPlayer?: (p: NhlPlayer) => void;
  onSelectMlbPlayer?: (p: MlbPlayer) => void;
  onSelectNflPlayer?: (p: NflPlayer) => void;
  onSelectStaticItem?: (raw: any, primary: string) => void;
}

const IS_WEB = Platform.OS === 'web';
const INPUT_STYLE = IS_WEB ? { outlineWidth: 0 } as object : {};
const DEBOUNCE_MS = 150;

function leagueName(id: number): string {
  return LEAGUES.find(l => l.id === id)?.name || '';
}

function localFuzzy(items: StaticItem[], q: string): StaticItem[] {
  if (!q || q.length < 1) return [];
  const ql = q.toLowerCase().trim();
  const words = ql.split(/\s+/).filter(Boolean);
  const scored = items.map(item => {
    const p = item.primary.toLowerCase();
    const s = (item.secondary || '').toLowerCase();
    let score = 0;
    if (p === ql || s === ql)            score = 100;
    else if (p.startsWith(ql))           score = 90;
    else if (p.includes(ql))             score = 80;
    else if (s.includes(ql))             score = 70;
    else if (words.length > 0 && words.every(w => p.includes(w))) score = 65;
    else if (words.some(w => w.length > 2 && p.includes(w)))      score = 40;
    return { item, score };
  });
  return scored
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 7)
    .map(x => x.item);
}

export default function FuzzySearchInput({
  value,
  onChangeText,
  searchType,
  leagueId,
  placeholder = 'Search...',
  style,
  inputStyle,
  autoFocus = false,
  returnKeyType = 'done',
  onSubmitEditing,
  autoCapitalize = 'words',
  confirmed = false,
  staticItems,
  ownerSession,
  onSelectTeam,
  onSelectPlayer,
  onSelectAllPlayer,
  onSelectLeague,
  onSelectCs2Player,
  onSelectCs2Team,
  onSelectWtaPlayer,
  onSelectNbaPlayer,
  onSelectNhlPlayer,
  onSelectMlbPlayer,
  onSelectNflPlayer,
  onSelectStaticItem,
}: FuzzySearchInputProps) {
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const lastQueryRef = useRef('');
  const searchIdRef = useRef(0);

  const invalidatePendingSearch = () => {
    searchIdRef.current += 1;
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
      searchTimeoutRef.current = null;
    }
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    setLoading(false);
  };

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) { setResults([]); setShowDropdown(false); setHasSearched(false); setSearchError(false); return; }

    if (staticItems) {
      const filtered = localFuzzy(staticItems, q);
      setResults(filtered);
      setShowDropdown(true);
      setHasSearched(true);
      setSearchError(false);
      return;
    }

    const myId = ++searchIdRef.current;
    const requestController = new AbortController();
    requestAbortRef.current?.abort();
    requestAbortRef.current = requestController;
    const signal = requestController.signal;
    setLoading(true);
    setSearchError(false);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    // A rate-limited upstream search must not leave the control looking
    // permanently busy.  The request itself is ignored if it completes after
    // this timeout; the user gets an explicit retry affordance instead.
    const searchTimeoutMs = searchType === 'all_players' ? 5_000 : 2_900;
    searchTimeoutRef.current = setTimeout(() => {
      if (searchIdRef.current !== myId) return;
      requestController.abort();
      requestAbortRef.current = null;
      searchIdRef.current += 1;
      setLoading(false);
      // Universal search is progressive: keep any fast soccer/MLB result
      // visible when a slower provider times out.
      setShowDropdown(true);
      setHasSearched(true);
      setSearchError(true);
    }, searchTimeoutMs);
    try {
      let r: any[] = [];
      if (searchType === 'teams') {
        const data = await searchTeams(q, leagueId, signal);
        r = data.results || [];
      } else if (searchType === 'leagues') {
        const data = await searchLeagues(q, signal);
        r = data.leagues || [];
      } else if (searchType === 'players') {
        const data = await searchPlayersQuick(q, leagueId, ownerSession, signal);
        r = (data.players || []).map((p: any) => ({
          playerId: (p.id as number) || 0,
          playerName: (p.name as string) || '',
          teamId: (p.teamId as number) || 0,
          teamName: (p.teamName as string) || (p.team as string) || '',
          leagueId: (p.leagueId as number) || 0,
          position: (p.position as string) || '',
          ownerPlayerPhoto: (p.ownerPlayerPhoto as string) || '',
          ownerTeamLogo: (p.ownerTeamLogo as string) || '',
        }));
      } else if (searchType === 'all_players') {
        // Publish each sport as soon as it returns. Previously the UI waited
        // for the slowest MLB/NFL provider before showing a fast soccer hit.
        // The abort signal also stops stale requests when the user types again.
        const universalRows: any[] = [];
        const addUniversalRows = (sport: 'soccer' | 'mlb' | 'nfl', rows: any[]) => {
          const mapped = rows.map((p: any) => ({
            sport,
            playerId: sport === 'soccer' ? (p.id || p.playerId || 0) : (p.id || 0),
            playerName: sport === 'soccer'
              ? (p.name || p.playerName || '')
              : (p.fullName || `${p.firstName || ''} ${p.lastName || ''}`.trim()),
            teamId: sport === 'soccer' ? (p.teamId || 0) : (p.team?.id || 0),
            teamName: sport === 'soccer' ? (p.teamName || p.team || '') : (p.team?.full_name || ''),
            leagueId: sport === 'soccer' ? (p.leagueId || 0) : 0,
            position: p.position || '',
            ownerPlayerPhoto: p.ownerPlayerPhoto || '',
            ownerTeamLogo: p.ownerTeamLogo || '',
            raw: p,
          }));
          const qWords = q.toLowerCase().trim().split(/\s+/).filter(Boolean);
          universalRows.push(...mapped.filter(p => p.playerName).filter(p => {
            if (sport === 'soccer' || qWords.length < 2) return true;
            return qWords.every(w => p.playerName.toLowerCase().includes(w));
          }));
          if (searchIdRef.current === myId && lastQueryRef.current === q) {
            setResults([...universalRows]);
            setShowDropdown(universalRows.length > 0);
            setHasSearched(true);
          }
        };
        const soccerPromise = searchPlayersQuick(q, leagueId, ownerSession, signal)
          .then(data => addUniversalRows('soccer', data.players || []))
          .catch(() => {});
        const mlbPromise = searchMlbPlayers(q, signal)
          .then(rows => addUniversalRows('mlb', rows))
          .catch(() => {});
        const nflPromise = searchNflPlayers(q, signal)
          .then(rows => addUniversalRows('nfl', rows))
          .catch(() => {});
        await Promise.allSettled([soccerPromise, mlbPromise, nflPromise]);
        r = universalRows;
      } else if (searchType === 'cs2_players') {
        r = (await searchCs2Players(q, signal)).filter((p: Cs2Player) => p.isActive !== false);
      } else if (searchType === 'cs2_teams') {
        r = await searchCs2Teams(q, signal);
      } else if (searchType === 'wta_players') {
        r = await searchWtaPlayers(q, signal);
      } else if (searchType === 'nba_players') {
        r = await searchNbaPlayers(q, signal);
      } else if (searchType === 'nhl_players') {
        r = await searchNhlPlayers(q, signal);
      } else if (searchType === 'mlb_players') {
        r = await searchMlbPlayers(q, signal);
      } else if (searchType === 'nfl_players') {
        r = await searchNflPlayers(q, signal);
      }
      // A response must still belong to the exact text currently in the
      // control. Selection and clear actions can update the parent value
      // without going through handleChange, so the request id alone is not
      // sufficient protection against an older empty response repainting the
      // dropdown over a valid result.
      if (searchIdRef.current !== myId || lastQueryRef.current !== q) return;
      setResults(r);
      setShowDropdown(r.length > 0);
      setHasSearched(true);
    } catch {
      if (searchIdRef.current !== myId || lastQueryRef.current !== q) return;
      setResults([]);
      setShowDropdown(false);
      setSearchError(true);
      setHasSearched(true);
    } finally {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = null;
      }
      if (requestAbortRef.current === requestController) {
        requestAbortRef.current = null;
      }
      if (searchIdRef.current === myId) setLoading(false);
    }
  }, [searchType, leagueId, staticItems, ownerSession?.email, ownerSession?.token]);

  const handleChange = (text: string) => {
    onChangeText(text);
    lastQueryRef.current = text;
    // Invalidate an already-running request immediately. Without this,
    // typing "Jonathan Jesus" can leave an older "Jesus" response eligible
    // to overwrite the current dropdown.
    searchIdRef.current += 1;
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
      searchTimeoutRef.current = null;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    requestAbortRef.current?.abort();
    requestAbortRef.current = null;
    if (confirmed) { setResults([]); setShowDropdown(false); return; }
    // Do not keep displaying results for the previous query while the new
    // query is waiting for its debounce/request.
    setResults([]);
    setShowDropdown(false);
    if (text.length < 2) {
      setHasSearched(false); setSearchError(false); setLoading(false); return;
    }
    // Show the loading spinner immediately — before the debounce fires —
    // so the user gets instant feedback that something is happening.
    if (!staticItems) setLoading(true);
    const isRateLimitedPlayerSearch = searchType === 'mlb_players' || searchType === 'nfl_players';
    // Give the user time to finish a name before hitting the provider. The
    // API layer also filters recent results locally for subsequent letters.
    const delay = staticItems ? 60 : isRateLimitedPlayerSearch ? 400 : DEBOUNCE_MS;
    debounceRef.current = setTimeout(() => {
      if (lastQueryRef.current === text) doSearch(text);
    }, delay);
  };

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    requestAbortRef.current?.abort();
  }, []);

  const dismiss = () => { setShowDropdown(false); };

  const handleSelectTeam = (item: TeamSearchResult) => {
    onChangeText(item.teamName); dismiss(); setResults([]);
    onSelectTeam?.({ teamId: item.teamId || (item as any).id, teamName: item.teamName, leagueId: item.leagueId || 0 });
  };
  const handleSelectPlayer = (item: FuzzyPlayerResult) => {
    onChangeText(item.playerName); dismiss(); setResults([]);
    onSelectPlayer?.(item);
  };
  const handleSelectAllPlayer = (item: UniversalPlayerResult) => {
    // Stop the in-flight universal request before committing the selection.
    // Otherwise a slower provider can repaint the dropdown after the tap.
    invalidatePendingSearch();
    lastQueryRef.current = item.playerName;
    onChangeText(item.playerName); dismiss(); setResults([]);
    setHasSearched(false); setSearchError(false);
    onSelectAllPlayer?.(item);
  };
  const handleSelectLeague = (item: any) => {
    onChangeText(item.name); dismiss(); setResults([]);
    onSelectLeague?.({ id: item.id, name: item.name, country: item.country || '' });
  };
  const handleSelectCs2Player = (p: Cs2Player) => {
    onChangeText(p.nickname); dismiss(); setResults([]);
    onSelectCs2Player?.(p);
  };
  const handleSelectCs2Team = (t: Cs2Team) => {
    onChangeText(t.name); dismiss(); setResults([]);
    onSelectCs2Team?.(t);
  };
  const handleSelectWtaPlayer = (p: WtaPlayer) => {
    onChangeText(p.fullName); dismiss(); setResults([]);
    onSelectWtaPlayer?.(p);
  };
  const handleSelectNbaPlayer = (p: NbaPlayer) => {
    onChangeText(p.fullName || `${p.firstName} ${p.lastName}`.trim()); dismiss(); setResults([]);
    onSelectNbaPlayer?.(p);
  };
  const handleSelectNhlPlayer = (p: NhlPlayer) => {
    onChangeText(p.fullName || `${p.firstName} ${p.lastName}`.trim()); dismiss(); setResults([]);
    onSelectNhlPlayer?.(p);
  };
  const handleSelectMlbPlayer = (p: MlbPlayer) => {
    const name = p.fullName || `${p.firstName || ''} ${p.lastName || ''}`.trim();
    invalidatePendingSearch();
    lastQueryRef.current = name;
    if (name) onChangeText(name);
    dismiss(); setResults([]);
    setHasSearched(false); setSearchError(false);
    onSelectMlbPlayer?.(p);
  };
  const handleSelectNflPlayer = (p: NflPlayer) => {
    const name = p.fullName || `${p.firstName || ''} ${p.lastName || ''}`.trim();
    invalidatePendingSearch();
    lastQueryRef.current = name;
    onChangeText(name); dismiss(); setResults([]);
    setHasSearched(false); setSearchError(false);
    onSelectNflPlayer?.(p);
  };
  const handleSelectStatic = (item: StaticItem) => {
    onChangeText(item.primary); dismiss(); setResults([]);
    onSelectStaticItem?.(item.raw ?? item, item.primary);
  };

  const showEmpty = !loading && hasSearched && !searchError && results.length === 0 && value.length >= 2;
  const shouldShow = !confirmed && ((showDropdown && results.length > 0) || showEmpty || (!loading && searchError && value.length >= 2));

  const renderItem = (item: any, index: number) => {
    if (staticItems) {
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectStatic(item)} activeOpacity={0.7}>
          <Ionicons name="ellipse-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.primary}</Text>
            {item.secondary ? <Text style={styles.dropdownSub} numberOfLines={1}>{item.secondary}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'teams') {
      const lg = leagueName(item.leagueId);
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectTeam(item)} activeOpacity={0.7}>
          <Ionicons name="shield-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.teamName}</Text>
            {lg ? <Text style={styles.dropdownSub} numberOfLines={1}>{lg}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'leagues') {
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectLeague(item)} activeOpacity={0.7}>
          <Ionicons name="trophy-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.name}</Text>
            {item.country ? <Text style={styles.dropdownSub} numberOfLines={1}>{item.country}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'players') {
      const sub = [item.teamName, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectPlayer(item)} activeOpacity={0.7}>
          {item.ownerPlayerPhoto ? (
            <Image source={{ uri: item.ownerPlayerPhoto }} style={styles.playerPhoto} />
          ) : (
            <Ionicons name="person-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          )}
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.playerName}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
          {item.ownerTeamLogo ? <Image source={{ uri: item.ownerTeamLogo }} style={styles.teamLogo} /> : null}
        </TouchableOpacity>
      );
    }
    if (searchType === 'all_players') {
      const sportLabel = item.sport === 'mlb' ? 'MLB' : item.sport === 'nfl' ? 'NFL' : 'Soccer';
      const icon = item.sport === 'mlb'
        ? 'baseball-outline'
        : item.sport === 'nfl'
          ? 'american-football-outline'
          : 'football-outline';
      const sub = [sportLabel, item.teamName, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectAllPlayer(item)} activeOpacity={0.7}>
          {item.sport === 'soccer' && item.ownerPlayerPhoto ? (
            <Image source={{ uri: item.ownerPlayerPhoto }} style={styles.playerPhoto} />
          ) : (
            <Ionicons name={icon as any} size={13} color={Colors.primary} style={styles.dropdownIcon} />
          )}
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.playerName}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
          {item.sport === 'soccer' && item.ownerTeamLogo ? <Image source={{ uri: item.ownerTeamLogo }} style={styles.teamLogo} /> : null}
        </TouchableOpacity>
      );
    }
    if (searchType === 'cs2_players') {
      const sub = [item.team?.name, item.fullName].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectCs2Player(item)} activeOpacity={0.7}>
          <Ionicons name="game-controller-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.nickname}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'cs2_teams') {
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectCs2Team(item)} activeOpacity={0.7}>
          <Ionicons name="shield-half-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.name}</Text>
            {item.shortName ? <Text style={styles.dropdownSub} numberOfLines={1}>{item.shortName}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'wta_players') {
      const rank = item.currentRank ? `#${item.currentRank}` : '';
      const sub  = [item.country, rank].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectWtaPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="tennisball-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.fullName}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'nba_players') {
      const name = item.fullName || `${item.firstName || ''} ${item.lastName || ''}`.trim();
      const sub  = [item.team?.full_name, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectNbaPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="basketball-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{name}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'nhl_players') {
      const name = item.fullName || `${item.firstName || ''} ${item.lastName || ''}`.trim();
      const sub  = [item.team?.full_name, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectNhlPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="snow-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{name}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'mlb_players') {
      const name = item.fullName || `${item.firstName || ''} ${item.lastName || ''}`.trim();
      const sub  = [item.team?.full_name, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectMlbPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="baseball-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{name}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'nfl_players') {
      const name = item.fullName || `${item.firstName || ''} ${item.lastName || ''}`.trim();
      const sub  = [item.team?.full_name, item.position].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectNflPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="american-football-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{name}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    return null;
  };

  const leadingIcon = confirmed
    ? <Ionicons name="checkmark-circle" size={16} color={Colors.primary} style={styles.leadIcon} />
    : loading
    ? <ActivityIndicator size="small" color={Colors.primary} style={styles.leadIcon} />
    : <Ionicons name="search-outline" size={15} color="#555" style={styles.leadIcon} />;

  return (
    <View style={[styles.container, style]}>
      <View style={[styles.inputRow, confirmed && styles.inputRowConfirmed]}>
        {leadingIcon}
        <TextInput
          style={[styles.input, inputStyle, INPUT_STYLE]}
          value={value}
          onChangeText={handleChange}
          placeholder={placeholder}
          placeholderTextColor={Colors.textTertiary}
          autoFocus={autoFocus}
          autoCorrect={false}
          autoCapitalize={autoCapitalize}
          returnKeyType={returnKeyType}
          onSubmitEditing={() => { dismiss(); onSubmitEditing?.(); }}
          onFocus={() => { if (!confirmed && value.length >= 2 && results.length > 0) setShowDropdown(true); }}
          onBlur={() => { setTimeout(dismiss, 200); }}
          editable={!confirmed}
        />
        {!loading && value.length > 0 && (
          <TouchableOpacity
            onPress={() => { onChangeText(''); setResults([]); setShowDropdown(false); setHasSearched(false); setSearchError(false); }}
            style={styles.clearBtn}
          >
            <Ionicons name="close-circle" size={15} color={confirmed ? Colors.primary : '#555'} />
          </TouchableOpacity>
        )}
      </View>

      {shouldShow && (
        <View style={styles.dropdownInline}>
          <ScrollView
            style={styles.dropdownScroll}
            keyboardShouldPersistTaps="always"
            nestedScrollEnabled
          >
            {searchError ? (
              <TouchableOpacity
                style={[styles.emptyRow, { paddingVertical: 12 }]}
                onPress={() => doSearch(value)}
                activeOpacity={0.7}
              >
                <Ionicons name="refresh-outline" size={14} color="#f0a500" style={{ marginRight: 6 }} />
                <Text style={[styles.emptyText, { color: '#f0a500' }]}>Search unavailable — tap to retry</Text>
              </TouchableOpacity>
            ) : showEmpty ? (
              <View style={styles.emptyRow}>
                <Ionicons name="search-outline" size={13} color="#555" style={{ marginRight: 6 }} />
                <Text style={styles.emptyText}>No results for "{value}"</Text>
              </View>
            ) : (
              results.map((item, i) => renderItem(item, i))
            )}
          </ScrollView>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { position: 'relative', zIndex: 9999 },
  inputRow: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: 'rgba(17,17,17,0.8)', borderRadius: 12,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.12)', paddingHorizontal: 12,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3, shadowRadius: 6,
  },
  inputRowConfirmed: {
    borderColor: 'rgba(57,255,20,0.5)',
    borderWidth: 1.5,
    backgroundColor: 'rgba(57,255,20,0.06)',
  },
  leadIcon: { marginRight: 7 },
  input: {
    flex: 1, height: 40, color: Colors.text,
    fontSize: 16, fontFamily: 'Inter_400Regular',
  },
  clearBtn: { marginLeft: 4, padding: 2 },
  dropdownInline: {
    position: 'absolute',
    top: 44,
    left: 0,
    right: 0,
    backgroundColor: 'rgba(10,10,10,0.98)', borderRadius: 12,
    borderWidth: 1, borderColor: 'rgba(57,255,20,0.15)',
    overflow: 'hidden',
    shadowColor: Colors.primary, shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.15, shadowRadius: 20, elevation: 16,
  },
  dropdownScroll: { maxHeight: 248 },
  dropdownItem: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 10, paddingHorizontal: 12,
    borderBottomWidth: 1, borderBottomColor: '#1e1e1e',
  },
  playerPhoto: {
    width: 28, height: 28, borderRadius: 14, marginRight: 8,
    backgroundColor: '#202020',
  },
  teamLogo: {
    width: 22, height: 22, marginLeft: 8,
    resizeMode: 'contain' as const,
  },
  dropdownIcon: { marginRight: 8 },
  dropdownTextWrap: { flex: 1 },
  dropdownMain: { color: Colors.text, fontSize: 13, fontFamily: 'Inter_500Medium' },
  dropdownSub: { color: Colors.textSecondary, fontSize: 11, fontFamily: 'Inter_400Regular', marginTop: 1 },
  emptyRow: {
    flexDirection: 'row', alignItems: 'center',
    paddingVertical: 12, paddingHorizontal: 12,
  },
  emptyText: { color: '#555', fontSize: 12, fontFamily: 'Inter_400Regular', fontStyle: 'italic' },
});
