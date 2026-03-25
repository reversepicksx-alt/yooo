
import { League, Player, Team } from '../types';

const API_KEY = '8154742f66d14cb52548c73c3edfbee3'; // Hardcoded as requested or from env
const BASE_URL = 'https://v3.football.api-sports.io';
const CURRENT_SEASON = 2025; // 2025/2026 season

const headers = {
  'x-apisports-key': API_KEY,
  'x-rapidapi-key': API_KEY,
};

export const SUPPORTED_LEAGUES = [
  // Domestic Leagues
  { id: 39, name: 'Premier League', type: 'Domestic' },
  { id: 140, name: 'La Liga', type: 'Domestic' },
  { id: 135, name: 'Serie A', type: 'Domestic' },
  { id: 78, name: 'Bundesliga', type: 'Domestic' },
  { id: 61, name: 'Ligue 1', type: 'Domestic' },
  { id: 40, name: 'Championship', type: 'Domestic' },
  { id: 188, name: 'A-League', type: 'Domestic' },
  { id: 253, name: 'MLS', type: 'Domestic' },
  { id: 262, name: 'Liga MX', type: 'Domestic' },
  { id: 128, name: 'Liga Profesional Argentina', type: 'Domestic' },
  { id: 71, name: 'Brasileirao', type: 'Domestic' },
  { id: 307, name: 'Saudi Pro League', type: 'Domestic' },
  { id: 254, name: 'NWSL', type: 'Domestic' },
  
  // International Club
  { id: 2, name: 'Champions League', type: 'International Club' },
  { id: 3, name: 'Europa League', type: 'International Club' },
  
  // International Team
  { id: 1, name: 'World Cup', type: 'International Team' },
  { id: 34, name: 'World Cup Qualifiers (UEFA)', type: 'International Team' },
  { id: 30, name: 'World Cup Qualifiers (CONMEBOL)', type: 'International Team' },
  { id: 32, name: 'World Cup Qualifiers (CONCACAF)', type: 'International Team' },
  { id: 31, name: 'World Cup Qualifiers (CAF)', type: 'International Team' },
  { id: 33, name: 'World Cup Qualifiers (AFC)', type: 'International Team' },
  { id: 4, name: 'Euro Championship', type: 'International Team' },
  { id: 96, name: 'Euro Qualifiers', type: 'International Team' },
  { id: 9, name: 'Copa America', type: 'International Team' },
  { id: 5, name: 'UEFA Nations League', type: 'International Team' },
  { id: 13, name: 'CONCACAF Nations League', type: 'International Team' },
  { id: 6, name: 'Africa Cup of Nations', type: 'International Team' },
  { id: 115, name: 'AFCON Qualifiers', type: 'International Team' },
  { id: 7, name: 'Asian Cup', type: 'International Team' },
  { id: 10, name: 'International Friendlies', type: 'International Team' },
];

export async function searchPlayers(query: string, leagueId?: number, season?: number, attempts: number = 0): Promise<Player[]> {
  if (query.length < 3) return [];
  
  try {
    let url = '';
    if (leagueId) {
      const searchSeason = season || CURRENT_SEASON;
      url = `${BASE_URL}/players?search=${encodeURIComponent(query)}&league=${leagueId}&season=${searchSeason}`;
    } else {
      url = `${BASE_URL}/players/profiles?search=${encodeURIComponent(query)}`;
    }

    const response = await fetch(url, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    
    console.log('API-Football Search Data:', data);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }

    if (!data.response || !Array.isArray(data.response) || data.response.length === 0) {
      // If we searched with a full name and got no results, try searching by last name
      if (query.includes(' ') && attempts === 0) {
        const lastName = query.split(' ').pop();
        if (lastName && lastName.length >= 3) {
          console.log(`No results for "${query}", trying last name "${lastName}"...`);
          return searchPlayers(lastName, leagueId, season, attempts + 1);
        }
      }

      // If we searched with a league and current season, try previous seasons (up to 3 years back)
      if (leagueId && attempts < 3) {
        const nextSeason = (season || CURRENT_SEASON) - 1;
        console.log(`No results for season ${season || CURRENT_SEASON}, trying ${nextSeason}...`);
        return searchPlayers(query, leagueId, nextSeason, attempts + 1);
      }
      
      // If we searched with a league and still got no results after 3 attempts, fallback to global search
      if (leagueId) {
        console.log(`No results for league ${leagueId} after 3 attempts, falling back to global search...`);
        return searchPlayers(query);
      }
      
      console.warn('No response or invalid format from API:', data);
      return [];
    }
    
    return data.response.map((item: any) => ({
      id: item.player.id,
      name: item.player.name,
      firstname: item.player.firstname,
      lastname: item.player.lastname,
      age: item.player.age,
      nationality: item.player.nationality,
      height: item.player.height,
      weight: item.player.weight,
      photo: '',
      teamId: item.statistics?.[0]?.team?.id || 0,
      teamName: item.statistics?.[0]?.team?.name || 'Unknown Team',
    }));
  } catch (error) {
    console.error('searchPlayers failed:', error);
    return [];
  }
}

export async function getTeamsByLeague(leagueId: number, season: number = CURRENT_SEASON): Promise<Team[]> {
  try {
    const response = await fetch(`${BASE_URL}/teams?league=${leagueId}&season=${season}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    if (!data.response) return [];
    return data.response.map((item: any) => ({
      id: item.team.id,
      name: item.team.name,
      logo: '',
    }));
  } catch (error) {
    console.error('getTeamsByLeague failed:', error);
    return [];
  }
}

export async function getPlayerStats(playerId: number, season: number = CURRENT_SEASON, attempts: number = 0): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/players?id=${playerId}&season=${season}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    
    // If current season returns nothing, try previous seasons (up to 3 years back)
    if ((!data.response || data.response.length === 0) && attempts < 3) {
      console.log(`No stats for player ${playerId} in season ${season}, trying ${season - 1}...`);
      return getPlayerStats(playerId, season - 1, attempts + 1);
    }
    
    return data.response?.[0];
  } catch (error) {
    console.error('getPlayerStats failed:', error);
    return null;
  }
}

export async function getTeamStats(teamId: number, leagueId: number, season: number = CURRENT_SEASON): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/teams/statistics?team=${teamId}&league=${leagueId}&season=${season}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error (getTeamStats): ${errorMsg}`);
    }
    return data.response;
  } catch (error) {
    console.error('getTeamStats failed:', error);
    return null;
  }
}

export async function getFixtures(teamId: number, last: number = 5): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures?team=${teamId}&last=${last}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    return data.response || [];
  } catch (error) {
    console.error('getFixtures failed:', error);
    return [];
  }
}

export async function getMatchPlayerStats(fixtureId: number, playerId: number): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures/players?fixture=${fixtureId}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error (getMatchPlayerStats): ${errorMsg}`);
    }
    
    if (!data.response) return null;
    
    // Find the player in the response
    for (const teamData of data.response) {
      const playerStats = teamData.players.find((p: any) => p.player.id === playerId);
      if (playerStats) return playerStats;
    }
    
    return null;
  } catch (error) {
    console.error(`getMatchPlayerStats failed for fixture ${fixtureId}:`, error);
    return null;
  }
}

export async function getRecentPlayerMatchHistory(playerId: number, teamId: number, count: number = 10): Promise<any[]> {
  try {
    // 1. Get recent fixtures for the team
    const fixtures = await getFixtures(teamId, count);
    if (fixtures.length === 0) return [];
    
    // 2. Fetch player stats for each fixture in batches of 5 to avoid rate limits
    const statsResults: any[] = [];
    for (let i = 0; i < fixtures.length; i += 5) {
      const batch = fixtures.slice(i, i + 5);
      const batchResults = await Promise.all(batch.map(f => getMatchPlayerStats(f.fixture.id, playerId)));
      statsResults.push(...batchResults);
    }
    
    // 3. Combine fixture info with player stats
    return fixtures.map((f, index) => ({
      fixture: f.fixture,
      league: f.league,
      teams: f.teams,
      goals: f.goals,
      score: f.score,
      playerStats: statsResults[index]
    })).filter(item => item.playerStats !== null);
  } catch (error) {
    console.error('getRecentPlayerMatchHistory failed:', error);
    return [];
  }
}

export async function getUpcomingFixtures(teamId: number, next: number = 1): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures?team=${teamId}&next=${next}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error (getUpcomingFixtures): ${errorMsg}`);
    }
    return data.response || [];
  } catch (error) {
    console.error('getUpcomingFixtures failed:', error);
    return [];
  }
}

export async function getH2H(team1Id: number, team2Id: number, last: number = 5): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures/headtohead?h2h=${team1Id}-${team2Id}&last=${last}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    
    return data.response || [];
  } catch (error) {
    console.error('getH2H failed:', error);
    return [];
  }
}

export async function getStandings(leagueId: number, season: number = CURRENT_SEASON): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/standings?league=${leagueId}&season=${season}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    
    return data.response?.[0]?.league?.standings?.[0] || [];
  } catch (error) {
    console.error('getStandings failed:', error);
    return [];
  }
}

export async function getOdds(fixtureId: number): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/odds?fixture=${fixtureId}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error: ${errorMsg}`);
    }
    
    // Return the first bookmaker's odds (usually 10Bet or Bet365)
    return data.response?.[0]?.bookmakers?.[0]?.bets?.find((b: any) => b.name === 'Match Winner') || null;
  } catch (error) {
    console.error('getOdds failed:', error);
    return null;
  }
}

export async function getLivePlayerStats(fixtureId: number, playerId: number): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures/players?fixture=${fixtureId}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error (getLivePlayerStats): ${errorMsg}`);
    }
    
    if (!data.response) return null;
    
    // Find the player in the response
    for (const teamData of data.response) {
      const playerStats = teamData.players.find((p: any) => p.player.id === playerId);
      if (playerStats) {
        return {
          minutes: playerStats.statistics[0].games.minutes,
          value: playerStats.statistics[0]
        };
      }
    }
    
    return null;
  } catch (error) {
    console.error(`getLivePlayerStats failed for fixture ${fixtureId}:`, error);
    return null;
  }
}

export async function getFixtureLineups(fixtureId: number): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures/lineups?fixture=${fixtureId}`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return [];
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Error (getFixtureLineups): ${errorMsg}`);
    }
    return data.response || [];
  } catch (error) {
    console.error('getFixtureLineups failed:', error);
    return [];
  }
}

export async function checkApiStatus(): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/status`, { headers });
    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }
    const text = await response.text();
    if (!text || !text.trim()) return null;
    const data = JSON.parse(text);
    if (data.errors && Object.keys(data.errors).length > 0) {
      const errorMsg = typeof data.errors === 'object' ? JSON.stringify(data.errors) : data.errors;
      throw new Error(`API-Football Status Error: ${errorMsg}`);
    }
    return data.response;
  } catch (error) {
    console.error('checkApiStatus failed:', error);
    return null;
  }
}
