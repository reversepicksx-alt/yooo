
import { searchPlayers } from './src/services/apiFootball';

async function debugNWSLSearch() {
  const query = 'Trinity Rodman';
  const leagueId = 254; // NWSL
  const season = 2025;

  console.log(`DEBUG: Searching for player: ${query} in league ${leagueId}, season ${season}...`);
  
  // I will call the function directly, but I need to make sure I'm not using the cached version if any.
  // Actually, I'll just call it and see the logs.
  const players = await searchPlayers(query, leagueId, season);
  
  console.log('DEBUG: Players found:', players.length);
  if (players.length > 0) {
    console.log('DEBUG: First player found:', JSON.stringify(players[0], null, 2));
  } else {
    console.log('DEBUG: No players found.');
  }
}

debugNWSLSearch();
