
import { searchPlayers } from './src/services/apiFootball';

async function testSearchPlayersFallback() {
  const query = 'Trinity Rodman';
  const leagueId = 254; // NWSL
  const season = 2025;

  console.log(`Searching for player: ${query} in league ${leagueId}, season ${season}...`);
  
  const players = await searchPlayers(query, leagueId, season);
  
  console.log('Players found:', players.length);
  if (players.length > 0) {
    console.log('First player found:', players[0].name);
  }
}

testSearchPlayersFallback();
