
import { getPlayerStats } from './src/services/apiFootball';

async function debugPlayerStats() {
  const playerId = 310540; // Trinity Rodman's ID

  console.log(`DEBUG: Getting stats for player ${playerId}...`);
  
  const stats = await getPlayerStats(playerId);
  
  console.log('DEBUG: Stats found:', JSON.stringify(stats, null, 2));
}

debugPlayerStats();
