# ReversePicks Soccer Prediction Pipeline — Complete Technical Reference

## Table of Contents
1. [The 14 Stages](#part-1-the-14-stages)
2. [Every Formula](#part-2-every-formula)
3. [Improvements Found](#part-3-improvements-found)

---

## PART 1: THE 14 STAGES

### Stage 0: Request Reception
**Entry point:** `POST /api/predict` in `routes/predict.py:85`
- Validates session + active subscription
- Resolves IDs from names if the scan/frontend did not supply them
- **World Cup detection:** `leagueId=1` or `wcMode=true` triggers tournament treatment

### Stage 1: Fixture Lookup
- Fetches next 10 fixtures for `teamId`
- Cross-references `opponentId` to identify the right match
- **H2H fallback:** If no fixture found, queries `fixtures/headtohead?h2h={tid}-{oppId}&next=2`
- Fetches odds via `api_football_request("odds", {"fixture": fid})`

### Stage 2: Player Data Fetching
- Queries MongoDB `player_season_stats` (primary) or API-Football live (fallback)
- **All stats normalized to per-90:** `V_90 = V_raw * (90 / max(minutes, 30))`
- Enriches each game log with **team possession** from fixture cache

### Stage 3: Team & Opponent Stats
- `team_stats_task`: Season averages for the player's team
- `opponent_stats_task`: Season averages for the opponent
- `standings_task`: League position, form, goals for/against

### Stage 4: Expected Possession Computation
**Formula (Possession Squeeze):**
```
dominance_ratio = team_avg_poss / (team_avg_poss + opp_against_avg_poss)
expected_poss = dominance_ratio * 100
```
- **H2H blend:** If >=2 H2H games with same venue, blends in at 50-70% weight:
  - 2 games = 50%, 3 = 56%, 4 = 62%, 5+ = 68%
  - **Formula:** `weight = min(0.70, 0.50 + (n - 2) * 0.06)`

### Stage 5: Web Intel & Situation Engine (Parallel)
Five async tasks run simultaneously:
1. `ai_digest_task` — Gemini tactical summary of the matchup
2. `situation_task` — Match stakes classification (`classify_match_stakes` in `situation_engine.py`)
3. `web_intel_task` — Live injury/lineup intel via Gemini web search (`fetch_web_intel`)
4. `ai_press_task` — AI-rated press intensity score (0.0-1.0) via `fetch_ai_press_intensity`
5. `h2h_task` — Head-to-head fixture history

### Stage 6: Bayesian Momentum Engine
**File:** `bayesian_engine.py`

**Layer 1 — Prior (Season Average):**
- **Decay weight:** `W_i = 0.93^i` (exponential, `i=0` is newest game)
- **Hyperprior shrinkage** (if n < 6):
  - `Shrinkage = (6 - n) / 6.0`
  - `mu_prior = mu_prior * (1 - S) + mu_hyper * S`
- **Prior precision:** `max(n / Variance, n^0.6, 2.0)`

**Layer 2 — Momentum (Last 5 games):**
Position-specific decay weights:
| Position | Weights (newest to oldest) |
|---|---|
| Attacker | `[1.0, 0.75, 0.55, 0.38, 0.25]` |
| Midfielder | `[1.0, 0.82, 0.67, 0.55, 0.45]` |
| Defender | `[1.0, 0.88, 0.77, 0.67, 0.58]` |
| Goalkeeper | `[1.0, 0.90, 0.80, 0.70, 0.60]` |

- **Momentum precision:** `(TotalW * 3) / Variance_mom`, floor = **2.5**
- **Trend consistency bonus:** If >=75% of last 5 follow the linear trend -> boost precision up to **30%**

**Layer 3 — Covariate Adjustments:**
| Adjustment | Formula | Cap |
|---|---|---|
| Venue (home/away) | `(mu_venue - mu_prior) * min(1.0, n_venue/10)` | n>=3 games |
| Opponent strength | `(OppConcession - mu_prior) * 0.15` | — |
| Covariate total | Added to posterior numerator | **33%** of (Prior + Momentum) weight |

**Posterior computation:**
```
mu_post = (mu_prior * Prec_prior + mu_mom * Prec_mom + CovAdj) / (Prec_prior + Prec_mom)
```
Then denormalized to expected minutes:
```
Final = mu_post * (max(30, min(90, expected_minutes)) / 90)
```

**P(OVER)/P(UNDER):** Monte Carlo with 5,000 samples
- Continuous stats: `random.gauss(mean, std)`
- Count stats (goals, shots, cards): Negative Binomial via Gamma-Poisson mixture
- **Market correction:** Line inflated by **1.5%** (`line * 1.015`)

### Stage 7: Position-Specific Adjustments

**1. Positional Baseline Squeeze** (`positional_baseline.py`)
| Sample Size | Behavior |
|---|---|
| n=0 | Pull 70% toward median: `adj = posterior*0.30 + p50*0.70` |
| n=1-7 | If outside `1.5*IQR`, squeeze with weight `0.55 * (1 - n/8)` |
| n>=8 | No adjustment |

**Key baselines (p50 pass_attempts):**
| Role | High Poss | Mid Poss | Low Poss |
|---|---|---|---|
| CDM (DLP/Regista) | 92 | 78 | 62 |
| CDM (Ball-winner) | 58 | 46 | 36 |
| CB (Ball-playing) | 88 | 70 | — |
| CB (Stopper) | 58 | 46 | — |
| ST (Pressing) | High passes, low shots | | |
| ST (Poacher) | Low passes, high shots | | |

**2. CDM Inversion Layer** (away CDM, pinned back)
- Trigger: `poss_ratio = expected_poss / season_avg_poss < 0.90`
- Formula: `boost = min(1.06, (1.0 / max(poss_ratio, 0.50))^0.30)`
- **Max boost: +6%** (capped)
- Stacks with game-script boost (chasing = +6% cap)

**3. Home CDM Deep-Block Boost** (dominant home team facing parked bus)
- Trigger: home expected > 60% AND opponent expected < 40%
- `Depth = min(1.0, (40 - opp_poss) / 18)`
- `Dominance = min(1.0, (home_poss - 60) / 12)`
- `Raw = Depth * (0.55 + 0.45 * Dominance)`
- Multiplier: `1.0 + min(0.15, Raw * 0.22)`
- **Max boost: +15%**

**4. GK Inverted Possession Model**
| Scenario | Trigger | Formula | Cap |
|---|---|---|---|
| Low possession boost | `poss_ratio < 0.87` | `(1.0 / max(poss_ratio, 0.50))^0.30` | **+10%** |
| Dominant penalty | `poss_ratio > 1.05` | `(poss_ratio - 1.0) * 1.0` | **-20%** |
| Hard floor | — | Never below **72%** of season prior |

**5. Defender Pass Multiplier Override**
- Uses **absolute 50% baseline** (not relative to season avg)
- `_def_raw_adj = (expected_poss - 50) / 50`
- Capped: `max(-0.40, min(0.55, _def_raw_adj))`
- Multiplier: `1.0 + _def_capped`

**6. Match Stakes Adjustments** (`situation_engine.py`)
| Stakes Type | Effect on Volume Props | Effect on Defensive Stats |
|---|---|---|
| `MUST_WIN_RELEGATION` | **-10%** (direct play) | **+6%** |
| `DEAD_RUBBER` | **-10%** flat | — |
| Title race (<=8 games, <=3pts) | — | — |
| Knockout 2nd leg (trailing) | Possession boost: `urgency * 5.5 + 2.5` | — |

**7. Press Intensity**
- Heuristic PPDA proxy: `(tackles + interceptions + fouls + blocks) / opponent_passes`
- AI-driven: 0.0-1.0 score from `fetch_ai_press_intensity` (tactical identity, year-stable)
- **Very High press:** `p75 * 0.84`, `p25 * 1.10`
- **High press:** `p75 * 0.91`, `p25 * 1.05`

**8. Team Pass-Rate Multiplier**
- Team avg passes >= 620: **1.15x**
- Team avg passes < 480: **0.83x**

**9. Fatigue Layer**
| Rest Days | Physical Stats | Volume Stats |
|---|---|---|
| <=1 day | **0.88x** | **0.92x** |
| <=3 days | **0.93x** | **0.96x** |

### Stage 8: Lineup Check
- Fetches confirmed lineup from API-Football
- If **NOT in lineup** or **substitute** -> confidence floor = **45%**
- Applied before BAYESIAN TRUTH

### Stage 9: H2H Blending (post-Bayes)
- Weight: **5% per H2H game** (up to 25%), **13% per game for GKs** (up to 40%)
- **Unanimous signal:** If >=75% of H2H games cleared the line at same venue -> pull projection toward H2H target with up to **55% weight**

### Stage 10: AI Synthesis (Grok primary, Gemini fallback)
**System prompt** (`PREDICTION_SYSTEM`) locks the AI:
- `projectedValue` is math-only — AI is an explainer, not a decider
- Direction MUST match `[MATHEMATICAL ENGINE]` verdict
- Never says "Bayesian" — always says "Reverse Formula"

**Fields produced:**
- `tacticalBreakdown` (~1800 chars, 7 mandatory sections)
- `sharpSummary` (2 decisive sentences)
- `reasoning`, `scenarioAnalysis`, `keyEvidence`, `gameFlowDynamics`, etc.

### Stage 11: [CONSISTENCY GUARD]
Ensures `projectedValue` and `recommendation` never contradict:
- If rec=UNDER but proj > line -> `proj = round((line - 0.5) * 2) / 2`
- If rec=OVER but proj < line -> `proj = round((line + 0.5) * 2) / 2`

### Stage 12: [BAYESIAN TRUTH] Override
**This is the source of truth for direction AND confidence:**
```
recommendation = "over" if pOver >= pUnder else "under"
confidenceScore = round(max(pOver, pUnder))
confidenceLevel = "High" if >=70, "Medium" if >=60, else "Low"
```
- If direction flips -> projectedValue flips to correct side of line
- Clears stale `coinFlip` flag if probability >=70%
- **Sharp Summary Direction Guard:** Detects if AI text contradicts math direction -> wipes all narrative fields and regenerates

### Stage 13: Post-Truth Adjustments

**Low Conviction Filter:**
- If `max(pOver, pUnder) < 57%` -> `lowConviction = True`, cap confidence at **58%**

**Calibration (DISABLED):**
- `CALIBRATION_ENABLED = False` — raw Bayesian proved more accurate than learned offsets
- When enabled: dampens to 40% of mean error, capped at +/-20%

**Prop Safety / AVOID:**
- `hitRate <= 44%` AND `n >= 5` -> **AVOID** (confidence capped at hit rate, floor 50)
- `hitRate 45-49%` -> **RISKY** (confidence capped at marginal)

**Final Response Format:**
```json
{
  "projectedValue": <bayesian posterior>,
  "recommendation": "over|under",
  "confidenceScore": 0-100,
  "confidenceLevel": "Low|Medium|High|Very High",
  "rawConfidence": <pre-calibration>,
  "bayesianMetrics": {priorMean, momentumEffect, covariateAdjustment, reversalFlag},
  "matchFactors": {possession, stakes, pressure, ...},
  "tacticalBreakdown": "...",
  "sharpSummary": "...",
  "scenarioProbabilities": {best, base, worst},
  "probabilityCurve": [...],
  "tacticalAlerts": [...],
  "lowConviction": true|false,
  "coinFlip": true|false
}
```

---

## PART 2: EVERY FORMULA

| Formula | Location | Value |
|---|---|---|
| Per-90 normalization | `predict.py` | `V * (90 / max(minutes, 30))` |
| Prior decay | `bayesian_engine.py` | `0.93^i` |
| Prior precision floor | `bayesian_engine.py` | `max(n/Var, n^0.6, 2.0)` |
| Momentum precision | `bayesian_engine.py` | `max(TotalW*3/Var, 2.5)` |
| Covariate cap | `bayesian_engine.py` | `33%` of (Prior + Momentum) |
| Possession squeeze | `predict.py` | `team_avg / (team_avg + opp_against_avg)` |
| H2H possession weight | `predict.py` | `min(0.70, 0.50 + (n-2)*0.06)` |
| CDM inversion | `bayesian_engine.py` | `min(1.06, (1/max(poss_ratio,0.50))^0.30)` |
| Home CDM deep block | `bayesian_engine.py` | `1.0 + min(0.15, Depth*Dominance*0.22)` |
| GK low-poss boost | `bayesian_engine.py` | `min(1.10, (1/max(poss_ratio,0.50))^0.30)` |
| GK dom penalty | `bayesian_engine.py` | `min(0.20, (poss_ratio-1.0)*1.0)` |
| GK hard floor | `bayesian_engine.py` | `72%` of season prior |
| Defender pass mult | `predict.py` | `1.0 + max(-0.40, min(0.55, (exp_poss-50)/50))` |
| Rel. stakes pass | `bayesian_engine.py` | **-10%** |
| Rel. stakes def | `bayesian_engine.py` | **+6%** |
| Dead rubber | `bayesian_engine.py` | **-10%** |
| Team pass rate high | `bayesian_engine.py` | **1.15x** |
| Team pass rate low | `bayesian_engine.py` | **0.83x** |
| Fatigue <=1 day phys | `bayesian_engine.py` | **0.88x** |
| Fatigue <=1 day vol | `bayesian_engine.py` | **0.92x** |
| Fatigue <=3 day phys | `bayesian_engine.py` | **0.93x** |
| Fatigue <=3 day vol | `bayesian_engine.py` | **0.96x** |
| H2H blend weight | `predict.py` | `5%` per game (GK: `13%`) |
| H2H unanimous | `predict.py` | Up to `55%` weight |
| Lineup not-starting | `predict.py` | Confidence floor `45%` |
| Market correction | `bayesian_engine.py` | `line * 1.015` |
| Monte Carlo samples | `bayesian_engine.py` | `5,000` |
| Low conviction cap | `predict.py` | `58%` |
| Tight edge cap | AI prompt | `+/-1.0` of line -> `60%` |
| Binary line cap | AI prompt | `UNDER 0.5` -> `55%` max |
| Consistency guard flip | `predict.py` | `(line +/- 0.5) * 2 / 2` |
| Bayesian truth levels | `predict.py` | High>=70, Medium>=60, Low |

---

## PART 3: IMPROVEMENTS FOUND

### 1. Confidence Calibration is DISABLED
`CALIBRATION_ENABLED = False` in `routes/predict.py:31`. The empirical calibration system (6-hour refresh from settled picks) is completely offline. The comment says "raw Bayesian projections proved more accurate" — but you have 6 months of settled data now. **Fix:** Re-enable with a 2-week A/B test against raw Bayes.

### 2. WC Settlement Bot Architecture (FIXED THIS SESSION)
WC picks used a separate settlement path that called Grok AI when no FT fixture was found — Grok has no real-time data and guessed "0". **Fixed** by fetching WC fixtures (`league=1&season=2026`) alongside club fixtures so WC picks flow through the normal soccer path with FT gate + zero-value guard.

### 3. Live Tracking vs Settlement Bot Gap
The live tracker runs every 15 seconds and correctly updates `currentValue`/`pace`. But the settlement bot runs every 15 minutes. For fast props (saves, shots), a player could hit the line in minute 75 and the pick would not settle until the next bot run (up to 15 min delay). **Fix:** Add a "settle-on-final" trigger inside `_build_soccer_update` when it detects `status=FT` during live polling — settlements happen in real-time during the picks tab refresh, not waiting for the 15-minute bot.

### 4. AI Search is Dead
`fetch_ai_press_intensity` uses `_grok_call` with a knowledge-only prompt since xAI search returns 410. The press score is now entirely heuristic-based (no live data). **Fix:** Either remove the AI press path entirely and use the heuristic PPDA proxy consistently, or integrate a live PPDA feed (Understat API or similar).

### 5. Positional Baseline Squeeze — Too Aggressive for n=0
When a player has zero cached game logs (new player, transferred, WC pick), the squeeze pulls the projection **70% toward the median** — essentially ignoring the Bayesian engine entirely. For WC players with no club data in the system, this means the projection is basically a generic position baseline. **Fix:** Add a "tournament mode" that uses the player's **club stats** (different league) as prior when n=0, rather than pulling toward the generic median.

### 6. Fatigue Layer — Missing for Tournaments
The fatigue layer checks rest days between fixtures, but tournament schedules (WC, Euro) have compressed rest (3-4 days). The current thresholds (<=1 day, <=3 days) do not capture the tournament-specific fatigue accumulation across 3+ games in 10 days. **Fix:** Add a "tournament fatigue" multiplier that compounds: game 1 = 1.0, game 2 = 0.96, game 3 = 0.92 for volume props.

### 7. H2H Weight — Too Low for Defensive Props
H2H blending uses 5% per game (up to 25%) for most props, but 13% per game for GKs. For CB pass props, the H2H sample against a specific opponent's press shape is highly predictive — yet it is capped at 25%. **Fix:** Extend the GK-style higher H2H weight (13%/game) to all **defensive volume props** (pass_attempts for CB/CDM, tackles, clearances).

### 8. Consistency Guard + Bayesian Truth Order
The CONSISTENCY GUARD runs **before** BAYESIAN TRUTH. If Bayesian Truth flips the direction, the projected value gets flipped again in the Truth block. This double-flip is correct but confusing in logs. **Fix:** Collapse both into a single "Post-Bayesian Alignment" step.

### 9. Prop Safety Cache — Missing WC
`prop_safety_cache.py` builds hit-rate tables from settled picks, but WC picks have their own `wcSettled` flag and are not included in the general prop safety buckets. **Fix:** Include `wcSettled` picks in the prop safety cache with a `tournament` tag so WC-specific patterns (e.g., "passes UNDER in knockout rounds") get learned.

### 10. Settlement Bot 15-Minute Interval
The bot sleeps 900 seconds between runs. In a live betting context, this means a pick could sit "pending" for 14 minutes after FT before settling. **Fix:** Add an "urgent settlement" trigger: when the live tracker sees `status=FT` during the picks tab poll, it immediately calls `_try_settle_soccer` for that specific pick — settlements happen in seconds, not minutes.
