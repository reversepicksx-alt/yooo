---
name: Press Intensity
description: Active API-Football-based soccer pressure signal and its conservative passing-projection contract.
---

Press Intensity is the active replacement for Understat/PPDA enrichment. It combines same-fixture opponent pass volume with weighted defensive actions from API-Football. Possession is context only, not pressure evidence.

Missing or incomplete defensive-action inputs must produce an explicit unavailable state; do not infer a measured pressure score from fallback possession or odds. Available packets must carry sample size, coverage, source, and the applied multiplier.

**Why:** API-Football does not provide defensive-third coordinates, recoveries, or timestamped pressure locations, so the metric is a transparent synthetic proxy rather than literal PPDA.

**How to apply:** Emit the packet on every soccer Bayesian path. Apply only to `pass_attempts`/`passes`, use the verified selection position/role, keep the direction role-aware, and cap the multiplier to a modest bounded range. Keep legacy Understat code dormant and out of prediction-time/background flows.

Stable pressure evidence requires at least seven valid defensive-action rows. Fewer rows remain usable only as an explicitly limited sample; opponent-pass volume cannot inflate the sample count. Reusable opponent profiles require at least five recent completed opponent fixtures, with the actual valid-row count retained in the packet.

**Why:** A smaller action sample can still provide useful context, but labeling it stable overstates reliability and makes provider coverage look better than it is.

**How to apply:** Sort fixture and history rows newest-first before applying any lookback limit. Keep the actual valid action count in the response and show limited/stable status in the UI. For wide players, broad provider-category comparison rows may be shown as context only when exact rows are unavailable; they must not influence projection or calibration.

Opponent pressure history is cached as one versioned profile per opponent and reused across all matching player-history rows. The current profile contract is `opponent-pressure-v3`: target five recent completed matches, bounded candidate lookback, explicit `sampleTarget`/`sampleMatches`, and `projectionInfluence=explanation_only`.

**Why:** Scoring only the one fixture represented by a player-history row produced misleading `N=1`/`0` cards and repeated provider work for the same opponent.

**How to apply:** Build profiles from verified opponent team IDs, never from the player row's single match alone. Display opponent coverage separately from row coverage, and never convert an unavailable packet's null score into `0/100`.

Large opponent histories must warm incrementally rather than through one all-or-nothing `gather()`. Return completed profiles within the prediction window, persist the remaining profiles in background work, and let later renders consume the versioned cache.

**Why:** An 18-opponent batch previously hit the 20-second response boundary and discarded profiles that had already completed, producing `0/18` even when provider data was available.

**How to apply:** Use a bounded `wait()` over per-opponent tasks, prioritize the newest/current opponents, and keep unresolved rows explicitly warming or unavailable instead of replacing the entire response with a synthetic empty packet.

For exact historical match labels, fixture-level API-Football team statistics may provide a real limited packet from observed fouls when the optional fixture-player endpoint is rate-limited. Keep the packet marked limited and retain its source; never replace it with possession, odds, or an aggregate opponent baseline.

**Why:** The player endpoint can stall or return empty while the exact fixture statistics endpoint remains usable; serial player enrichment made the history card time out and showed “not yet warmed” instead of available evidence.

**How to apply:** Bound optional player-action enrichment and let the exact fixture team-stat row classify from its available defensive field. Preserve the seven-row threshold for stable aggregate evidence.

The displayed 0–100 pressure value is the custom Reverse Picks Pressure Index, not PPDA, a raw provider statistic, or a count of pressure events. Zero means the floor of the low band, not that the opponent applied no pressure. Exact-fixture history packets are explanation-only and must be cache-versioned when their response contract changes.

**Why:** Users can reasonably read “LOW 0/100” as literal zero pressure, and old cached packets otherwise hide new provenance or interpretation fields after a contract update.

**How to apply:** Label the UI as the Reverse Picks Pressure Index, show limited sample status, and keep raw provider inputs behind the audit/math boundary. Bump the exact-fixture cache identity whenever packet fields or semantics change.