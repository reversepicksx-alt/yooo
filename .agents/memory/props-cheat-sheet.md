---
name: Cross-sport props cheat sheet
description: Hit rate findings from 3357+ settled picks — which props/directions reliably hit or miss.
---

## Top signals (n≥20)
| Prop | Dir | Hit | n | Note |
|---|---|---|---|---|
| shots | UNDER | 80.9% | 89 | Best prop in system, venue-independent |
| pitcher_strikeouts | UNDER | 80.0% | 135 | Lines 2.5Ks too high |
| maps_1_2_kills | UNDER | 74.4% | 86 | Lines ~7 kills too high |
| hitter_fantasy_points | UNDER | 74.2% | 31 | Hitters always underperform |
| maps_1_2_headshots | OVER | 22.2% | 27 | Nearly unplayable |
| hitter_fantasy_points | OVER | 33.0% | 91 | Worst OVER in system |
| shots | OVER | 33.3% | 48 | Away shots OVER = 15.8% |
| maps_1_2_kills | OVER | 42.7% | 164 | Chronic underperformer |
| clearances | OVER | 0.0% | 11 | Has NEVER hit |

## GK pass_attempts inversion (vs outfield)
- Outfield (CB/CDM): more poss = OVER (68% when >62% poss)
- GK: more poss = UNDER (73% when >62% poss) — inverted effect
- Best GK plays: home + 42-55% poss → OVER 90-92%; Sweeper Keeper UNDER = 92.9%

## Confidence calibration trap
- UNDER at 55-64% conf → 73-75% hit (best UNDER band)
- UNDER at 65-74% conf → 33-45% hit (TRAP — fade it)
- OVER at 70%+ conf → 73-77% hit (genuine signal)

## Data quality issues
- dribbles actualValue stored in wrong unit (avg_actual=59.8 vs line=2.9) — do not use for handicapping

## League calibration
- Saudi Pro League (307): 79.5% GK hit rate
- La Liga (140): 50.0% GK hit rate — coin flip, avoid
