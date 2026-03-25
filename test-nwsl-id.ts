
import { SUPPORTED_LEAGUES } from './src/services/apiFootball';

async function findNWSL() {
  const nwsl = SUPPORTED_LEAGUES.find(l => l.name === 'NWSL');
  console.log('NWSL League ID:', nwsl);
}

findNWSL();
