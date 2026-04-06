// Shared constants for ReversePicks

export const PROP_TYPES = [
  { key: 'goals', label: 'Goals', stat: 'goals.total', desc: 'Goals scored' },
  { key: 'assists', label: 'Assists', stat: 'goals.assists', desc: 'Goal assists' },
  { key: 'shots_assisted', label: 'Shots Assisted', stat: 'passes.key', desc: 'Passes leading to a shot' },
  { key: 'pass_attempts', label: 'Pass Attempts', stat: 'passes.total', desc: 'Total passes attempted' },
  { key: 'shots', label: 'Shots', stat: 'shots.total', desc: 'Total shots taken' },
  { key: 'shots_on_target', label: 'Shots on Target', stat: 'shots.on', desc: 'Shots on goal' },
  { key: 'tackles', label: 'Tackles', stat: 'tackles.total', desc: 'Total tackles won' },
  { key: 'key_passes', label: 'Key Passes', stat: 'passes.key', desc: 'Passes leading to a shot' },
  { key: 'saves', label: 'Saves', stat: 'goals.saves', desc: 'Goalkeeper saves' },
  { key: 'interceptions', label: 'Interceptions', stat: 'tackles.interceptions', desc: 'Passes intercepted' },
  { key: 'blocks', label: 'Blocks', stat: 'tackles.blocks', desc: 'Shots/passes blocked' },
  { key: 'dribbles', label: 'Dribble Attempts', stat: 'dribbles.attempts', desc: 'Dribble attempts made' },
  { key: 'dribbles_success', label: 'Successful Dribbles', stat: 'dribbles.success', desc: 'Dribbles completed' },
  { key: 'fouls_drawn', label: 'Fouls Drawn', stat: 'fouls.drawn', desc: 'Fouls won by player' },
  { key: 'fouls_committed', label: 'Fouls Committed', stat: 'fouls.committed', desc: 'Fouls committed' },
  { key: 'crosses', label: 'Crosses', stat: 'passes.crosses', desc: 'Cross attempts' },
  { key: 'clearances', label: 'Clearances', stat: 'tackles.clearances', desc: 'Defensive clearances' },
  { key: 'duels_won', label: 'Duels Won', stat: 'duels.won', desc: 'Duels won' },
  { key: 'yellow_cards', label: 'Yellow Cards', stat: 'cards.yellow', desc: 'Yellow cards received' },
];

export function getPropLabel(key) {
  const p = PROP_TYPES.find(pt => pt.key === key);
  return p ? p.label : key.replace(/_/g, ' ');
}

export const OWNER_EMAIL = 'josselj001@gmail.com';
