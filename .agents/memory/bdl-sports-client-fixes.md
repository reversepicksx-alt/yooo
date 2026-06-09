---
name: BDL Sports Client Fixes
description: Correct BDL endpoints and arg patterns for MMA, PGA, and other non-soccer sport engines
---

## Rules

### MMA — use /fight_stats not /fights
- `/fights?fighter_id=X` returns fight metadata only (no nested per-fighter stats)
- `/fight_stats?fighter_id=X` returns per-fighter stat rows with correct BDL field names:
  `significant_strikes_landed`, `submissions_attempted`, `control_time_seconds`, `is_winner`
- Cache key: `fight_stats2:{fighter_id}:{limit}` (not `fights:`)

### PGA — no per-round endpoint; synthesize from /player_season_stats
- BDL `/stats` returns 404 for PGA
- Use `/player_season_stats?player_ids[]={id}&season={year}` — returns season average stats
- Parse `stat_name` + inner `stat_value[].statName/statValue` to extract scoring avg, birdies, etc.
- Synthesize N=14–32 round logs with per-stat Gaussian noise (seed = player_id × 100 + season)
- Falls back to previous season if current returns no data
- Cache key: `rounds2:{player_id}:{season}` (not `rounds:`)

### LoL — /matches not /stats
- BDL `/stats` returns 404 for LoL
- `/matches?player_id=X` returns match-level data but NO per-player KDA stats
- LoL predictions will still fail with "No match data" — this is a BDL data-coverage gap, not a bug

### F1 — no completed race data in BDL 2025/2026
- `/results` returns 404; `/sessions` returns only "scheduled" sessions
- F1 predictions gracefully fail with "No race data found" — expected

### Bayesian engine _baye_mc call pattern (all non-soccer engines)
- Wrong: `_baye_mc(values[:12], line, is_count)` — passes a list as mean, crashes
- Correct: compute `posterior = float(np.mean(v))` and `_mc_std = float(np.std(v, ddof=1))` from `v = values[:12]`, then call:
  `p_over, p_under, _, _ = _baye_mc(posterior, _mc_std, line, n_sims=5000, is_count_stat=is_count)`
- Applies to: mma, pga, f1, lol, dota2, ncaaf engines

### Blank-screen mode fix for non-soccer sports
- Non-soccer sport forms are gated behind `mode === 'manual'` in scan.tsx
- The sport picker must call `setMode('manual')` for all non-soccer/world_cup sports
- If mode stays 'scan', the form never renders → blank screen

**Why:** BDL is the data source for all non-soccer sports. Each sport has different
endpoint conventions. This file documents what actually works vs what 404s.
