
import { League, Player, Team } from '../types';

const API_KEY = '8154742f66d14cb52548c73c3edfbee3'; // Hardcoded as requested or from env
const BASE_URL = 'https://v3.football.api-sports.io';
const CURRENT_SEASON = 2025; // 2025/2026 season

const headers = {
  'x-apisports-key': API_KEY,
  'x-rapidapi-key': API_KEY,
};

export const SUPPORTED_LEAGUES = [
  { id: 39, name: 'Premier League' },
  { id: 140, name: 'La Liga' },
  { id: 135, name: 'Serie A' },
  { id: 78, name: 'Bundesliga' },
  { id: 61, name: 'Ligue 1' },
  { id: 2, name: 'Champions League' },
  { id: 3, name: 'Europa League' },
  { id: 40, name: 'Championship' },
  { id: 188, name: 'A-League' },
  { id: 253, name: 'MLS' },
  { id: 262, name: 'Liga MX' },
  { id: 128, name: 'Liga Profesional Argentina' },
  { id: 71, name: 'Brasileirao' },
  { id: 307, name: 'Saudi Pro League' },
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
    const data = await response.json();
    
    console.log('API-Football Search Data:', data);
    
    if (data.errors && Object.keys(data.errors).length > 0) {
      console.error('API-Football Errors:', data.errors);
      return [];
    }

    if (!data.response || !Array.isArray(data.response) || data.response.length === 0) {
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
      photo: item.player.photo,
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
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    if (!data.response) return [];
    return data.response.map((item: any) => ({
      id: item.team.id,
      name: item.team.name,
      logo: item.team.logo,
    }));
  } catch (error) {
    console.error('getTeamsByLeague failed:', error);
    return [];
  }
}

export async function getPlayerStats(playerId: number, season: number = CURRENT_SEASON, attempts: number = 0): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/players?id=${playerId}&season=${season}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    
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
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    return data.response;
  } catch (error) {
    console.error('getTeamStats failed:', error);
    return null;
  }
}

export async function getFixtures(teamId: number, last: number = 5): Promise<any[]> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures?team=${teamId}&last=${last}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    return data.response || [];
  } catch (error) {
    console.error('getFixtures failed:', error);
    return [];
  }
}

export async function getMatchPlayerStats(fixtureId: number, playerId: number): Promise<any> {
  try {
    const response = await fetch(`${BASE_URL}/fixtures/players?fixture=${fixtureId}`, { headers });
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    
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
    
    // 2. Fetch player stats for each fixture in parallel
    const statsPromises = fixtures.map(f => getMatchPlayerStats(f.fixture.id, playerId));
    const statsResults = await Promise.all(statsPromises);
    
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
    if (!response.ok) throw new Error(`API Error: ${response.status}`);
    const data = await response.json();
    return data.response;
  } catch (error) {
    console.error('getUpcomingFixtures failed:', error);
    return [];
  }
}
