
import { getStandings } from './src/services/apiFootball';

async function debugNWSLStandings() {
  const leagueId = 254; // NWSL
  const season = 2025;

  console.log(`DEBUG: Getting standings for league ${leagueId}, season ${season}...`);
  
  const standings = await getStandings(leagueId, season);
  
  console.log('DEBUG: Standings found:', standings.length);
  if (standings.length > 0) {
    console.log('DEBUG: First team in standings:', JSON.stringify(standings[0], null, 2));
  } else {
    console.log('DEBUG: No standings found.');
  }
}

debugNWSLStandings();
