import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, ActivityIndicator, Platform, ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import Colors from '@/constants/colors';
import {
  searchTeams, searchPlayersQuick, searchLeagues,
  searchMlbPlayers, searchCs2Players, searchCs2Teams, searchWtaPlayers,
  TeamSearchResult, PlayerSearchResult, LeagueSearchResult, LEAGUES,
  MlbPlayer, Cs2Player, Cs2Team, WtaPlayer,
} from '@/lib/api';

export type SearchType =
  | 'teams' | 'players' | 'leagues'
  | 'mlb_players' | 'cs2_players' | 'cs2_teams' | 'wta_players';

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

  onSelectTeam?: (result: FuzzyTeamResult) => void;
  onSelectPlayer?: (result: FuzzyPlayerResult) => void;
  onSelectLeague?: (result: FuzzyLeagueResult) => void;
  onSelectMlbPlayer?: (p: MlbPlayer) => void;
  onSelectCs2Player?: (p: Cs2Player) => void;
  onSelectCs2Team?: (t: Cs2Team) => void;
  onSelectWtaPlayer?: (p: WtaPlayer) => void;
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
  onSelectTeam,
  onSelectPlayer,
  onSelectLeague,
  onSelectMlbPlayer,
  onSelectCs2Player,
  onSelectCs2Team,
  onSelectWtaPlayer,
  onSelectStaticItem,
}: FuzzySearchInputProps) {
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQueryRef = useRef('');

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) { setResults([]); setShowDropdown(false); setHasSearched(false); return; }

    if (staticItems) {
      const filtered = localFuzzy(staticItems, q);
      setResults(filtered);
      setShowDropdown(true);
      setHasSearched(true);
      return;
    }

    setLoading(true);
    try {
      let r: any[] = [];
      if (searchType === 'teams') {
        const data = await searchTeams(q, leagueId);
        r = data.results || [];
      } else if (searchType === 'leagues') {
        const data = await searchLeagues(q);
        r = data.leagues || [];
      } else if (searchType === 'players') {
        const data = await searchPlayersQuick(q, leagueId);
        r = (data.players || []).map((p: Record<string, unknown>) => ({
          playerId: (p.id as number) || 0,
          playerName: (p.name as string) || '',
          teamId: (p.teamId as number) || 0,
          teamName: (p.teamName as string) || (p.team as string) || '',
          leagueId: (p.leagueId as number) || 0,
          position: (p.position as string) || '',
        }));
      } else if (searchType === 'mlb_players') {
        r = await searchMlbPlayers(q);
      } else if (searchType === 'cs2_players') {
        r = (await searchCs2Players(q)).filter((p: Cs2Player) => p.isActive !== false);
      } else if (searchType === 'cs2_teams') {
        r = await searchCs2Teams(q);
      } else if (searchType === 'wta_players') {
        r = await searchWtaPlayers(q);
      }
      setResults(r);
      setShowDropdown(r.length > 0);
      setHasSearched(true);
    } catch {
      setResults([]); setShowDropdown(false);
    } finally {
      setLoading(false);
    }
  }, [searchType, leagueId, staticItems]);

  const handleChange = (text: string) => {
    onChangeText(text);
    lastQueryRef.current = text;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (confirmed) { setResults([]); setShowDropdown(false); return; }
    if (text.length < 2) {
      setResults([]); setShowDropdown(false); setHasSearched(false); return;
    }
    const delay = staticItems ? 60 : DEBOUNCE_MS;
    debounceRef.current = setTimeout(() => {
      if (lastQueryRef.current === text) doSearch(text);
    }, delay);
  };

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  const dismiss = () => { setShowDropdown(false); };

  const handleSelectTeam = (item: TeamSearchResult) => {
    onChangeText(item.teamName); dismiss(); setResults([]);
    onSelectTeam?.({ teamId: item.teamId || (item as any).id, teamName: item.teamName, leagueId: item.leagueId || 0 });
  };
  const handleSelectPlayer = (item: FuzzyPlayerResult) => {
    onChangeText(item.playerName); dismiss(); setResults([]);
    onSelectPlayer?.(item);
  };
  const handleSelectLeague = (item: any) => {
    onChangeText(item.name); dismiss(); setResults([]);
    onSelectLeague?.({ id: item.id, name: item.name, country: item.country || '' });
  };
  const handleSelectMlbPlayer = (p: MlbPlayer) => {
    onChangeText(p.fullName || p.firstName + ' ' + p.lastName); dismiss(); setResults([]);
    onSelectMlbPlayer?.(p);
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
  const handleSelectStatic = (item: StaticItem) => {
    onChangeText(item.primary); dismiss(); setResults([]);
    onSelectStaticItem?.(item.raw ?? item, item.primary);
  };

  const showEmpty = !loading && hasSearched && results.length === 0 && value.length >= 2;
  const shouldShow = !confirmed && ((showDropdown && results.length > 0) || showEmpty);

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
          <Ionicons name="person-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.playerName}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
        </TouchableOpacity>
      );
    }
    if (searchType === 'mlb_players') {
      const team = item.team?.displayName || '';
      const pos  = item.position || '';
      const sub  = [team, pos].filter(Boolean).join(' · ');
      return (
        <TouchableOpacity key={index} style={styles.dropdownItem} onPress={() => handleSelectMlbPlayer(item)} activeOpacity={0.7}>
          <Ionicons name="baseball-outline" size={13} color={Colors.primary} style={styles.dropdownIcon} />
          <View style={styles.dropdownTextWrap}>
            <Text style={styles.dropdownMain} numberOfLines={1}>{item.fullName}</Text>
            {sub ? <Text style={styles.dropdownSub} numberOfLines={1}>{sub}</Text> : null}
          </View>
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
        {!loading && value.length > 0 && !confirmed && (
          <TouchableOpacity
            onPress={() => { onChangeText(''); setResults([]); setShowDropdown(false); setHasSearched(false); }}
            style={styles.clearBtn}
          >
            <Ionicons name="close-circle" size={15} color="#555" />
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
            {showEmpty ? (
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
  container: { position: 'relative', zIndex: 100 },
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
    marginTop: 4,
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
