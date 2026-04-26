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

const INPUT_STYLE = Platform.OS === 'web' ? { outlineWidth: 0 } as object : {};
const DEBOUNCE_MS = 280;

function leagueName(leagueId: number): string {
  return LEAGUES.find(l => l.id === leagueId)?.name || '';
}

// Get DOM element from a React Native Web ref
function getDOMNode(ref: React.RefObject<View | null>): HTMLElement | null {
  if (!ref.current) return null;
  try {
    // React Native Web exposes the underlying DOM node this way
    const node = (ref.current as any);
    if (node.getBoundingClientRect) return node as unknown as HTMLElement;
    // Try accessing via internal RN Web APIs
    if (node._nativeTag) {
      const el = document.querySelector(`[data-testid]`) || null;
      return el as HTMLElement;
    }
    return null;
  } catch {
    return null;
  }
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
  const containerRef = useRef<View>(null);
  const inputRowRef = useRef<View>(null);

  // For web fixed dropdown positioning
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number; width: number } | null>(null);

  const measureForWeb = useCallback(() => {
    if (Platform.OS !== 'web') return;
    if (!inputRowRef.current) return;
    try {
      // React Native Web host components expose getBoundingClientRect directly
      const node = inputRowRef.current as unknown as HTMLElement;
      if (typeof node.getBoundingClientRect === 'function') {
        const rect = node.getBoundingClientRect();
        // position:fixed coords are viewport-relative — same as getBoundingClientRect, no scroll offset needed
        setDropdownPos({
          top: rect.bottom,
          left: rect.left,
          width: rect.width,
        });
      }
    } catch {
      // fallback: keep previous pos
    }
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) {
      setResults([]);
      setShowDropdown(false);
      return;
    }
    setLoading(true);
    try {
      if (searchType === 'teams') {
        const data = await searchTeams(q, leagueId);
        setResults(data.results || []);
        setShowDropdown((data.results || []).length > 0);
      } else if (searchType === 'leagues') {
        const data = await searchLeagues(q);
        setResults(data.leagues || []);
        setShowDropdown((data.leagues || []).length > 0);
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
      setResults([]);
      setShowDropdown(false);
    } finally {
      setLoading(false);
    }
  }, [searchType, leagueId]);

  const handleChange = (text: string) => {
    onChangeText(text);
    lastQueryRef.current = text;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (text.length < 2) {
      setResults([]);
      setShowDropdown(false);
      return;
    }
    debounceRef.current = setTimeout(() => {
      if (lastQueryRef.current === text) doSearch(text);
    }, DEBOUNCE_MS);
  };

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // Re-measure when dropdown becomes visible (keyboard may have shifted layout)
  useEffect(() => {
    if (showDropdown && Platform.OS === 'web') {
      // Small delay to let the keyboard open and layout stabilise
      const t = setTimeout(measureForWeb, 50);
      return () => clearTimeout(t);
    }
  }, [showDropdown, measureForWeb]);

  const handleSelectTeam = (item: TeamSearchResult) => {
    onChangeText(item.teamName);
    setShowDropdown(false);
    setResults([]);
    onSelectTeam?.(item);
  };

  const handleSelectPlayer = (item: FuzzyPlayerResult) => {
    onChangeText(item.playerName);
    setShowDropdown(false);
    setResults([]);
    onSelectPlayer?.(item);
  };

  const handleSelectLeague = (item: LeagueSearchResult) => {
    onChangeText(item.name);
    setShowDropdown(false);
    setResults([]);
    onSelectLeague?.(item);
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

  const dropdownList = showDropdown && results.length > 0 ? (
    searchType === 'teams' ? (
      <FlatList
        data={results as TeamSearchResult[]}
        keyExtractor={(_, i) => String(i)}
        renderItem={renderTeamItem}
        keyboardShouldPersistTaps="always"
        scrollEnabled={results.length > 4}
        style={{ maxHeight: 220 }}
      />
    ) : searchType === 'leagues' ? (
      <FlatList
        data={results as LeagueSearchResult[]}
        keyExtractor={(_, i) => String(i)}
        renderItem={renderLeagueItem}
        keyboardShouldPersistTaps="always"
        scrollEnabled={results.length > 4}
        style={{ maxHeight: 220 }}
      />
    ) : (
      <FlatList
        data={results as FuzzyPlayerResult[]}
        keyExtractor={(_, i) => String(i)}
        renderItem={renderPlayerItem}
        keyboardShouldPersistTaps="always"
        scrollEnabled={results.length > 4}
        style={{ maxHeight: 220 }}
      />
    )
  ) : null;

  // Web: use fixed positioning anchored to measured input position
  const webDropdownStyle: any = Platform.OS === 'web' && dropdownPos ? {
    position: 'fixed',
    top: dropdownPos.top,
    left: dropdownPos.left,
    width: dropdownPos.width,
    zIndex: 99999,
    backgroundColor: '#111',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a2a',
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.5,
    shadowRadius: 6,
    elevation: 8,
  } : null;

  return (
    <View ref={containerRef} style={[styles.container, style]}>
      <View
        ref={inputRowRef}
        style={styles.inputRow}
      >
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
          onSubmitEditing={() => {
            setShowDropdown(false);
            onSubmitEditing?.();
          }}
          onFocus={() => {
            measureForWeb();
            if (value.length >= 2 && results.length > 0) setShowDropdown(true);
          }}
          onBlur={() => {
            setTimeout(() => setShowDropdown(false), 180);
          }}
        />
        {loading && (
          <ActivityIndicator size="small" color={Colors.primary} style={styles.spinner} />
        )}
        {!loading && value.length > 0 && (
          <TouchableOpacity onPress={() => { onChangeText(''); setResults([]); setShowDropdown(false); }} style={styles.clearBtn}>
            <Ionicons name="close-circle" size={15} color="#555" />
          </TouchableOpacity>
        )}
      </View>

      {/* Web: fixed-position dropdown using getBoundingClientRect coordinates */}
      {Platform.OS === 'web' && showDropdown && results.length > 0 && dropdownPos && (
        <View style={webDropdownStyle}>
          {dropdownList}
        </View>
      )}

      {/* Native: standard absolute dropdown */}
      {Platform.OS !== 'web' && showDropdown && results.length > 0 && (
        <View style={styles.dropdown}>
          {dropdownList}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    position: 'relative',
    zIndex: 100,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#1a1a1a',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a2a',
    paddingHorizontal: 10,
  },
  input: {
    flex: 1,
    height: 40,
    color: Colors.text,
    fontSize: 16,
    fontFamily: 'Inter_400Regular',
  },
  spinner: {
    marginLeft: 6,
  },
  clearBtn: {
    marginLeft: 4,
    padding: 2,
  },
  dropdown: {
    position: 'absolute',
    top: 44,
    left: 0,
    right: 0,
    backgroundColor: '#111',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#2a2a2a',
    overflow: 'hidden',
    zIndex: 200,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.5,
    shadowRadius: 6,
    elevation: 8,
  },
  dropdownItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 9,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1e1e1e',
  },
  dropdownIcon: {
    marginRight: 8,
  },
  dropdownTextWrap: {
    flex: 1,
  },
  dropdownMain: {
    color: Colors.text,
    fontSize: 13,
    fontFamily: 'Inter_500Medium',
  },
  dropdownSub: {
    color: Colors.textSecondary,
    fontSize: 11,
    fontFamily: 'Inter_400Regular',
    marginTop: 1,
  },
});
