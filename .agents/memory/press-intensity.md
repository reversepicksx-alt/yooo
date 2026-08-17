---
name: Press Intensity
description: Active API-Football-based soccer pressure signal and its conservative passing-projection contract.
---

Press Intensity is the active replacement for Understat/PPDA enrichment. It combines same-fixture opponent pass volume with weighted defensive actions from API-Football. Possession is context only, not pressure evidence.

Missing or incomplete defensive-action inputs must produce an explicit unavailable state; do not infer a measured pressure score from fallback possession or odds. Available packets must carry sample size, coverage, source, and the applied multiplier.

**Why:** API-Football does not provide defensive-third coordinates, recoveries, or timestamped pressure locations, so the metric is a transparent synthetic proxy rather than literal PPDA.

**How to apply:** Emit the packet on every soccer Bayesian path. Apply only to `pass_attempts`/`passes`, use the verified selection position/role, keep the direction role-aware, and cap the multiplier to a modest bounded range. Keep legacy Understat code dormant and out of prediction-time/background flows.

Stable pressure evidence requires at least seven valid defensive-action rows. Fewer rows remain usable only as an explicitly limited sample; opponent-pass volume cannot inflate the sample count.

**Why:** A smaller action sample can still provide useful context, but labeling it stable overstates reliability and makes provider coverage look better than it is.

**How to apply:** Sort fixture and history rows newest-first before applying any lookback limit. Keep the actual valid action count in the response and show limited/stable status in the UI. For wide players, broad provider-category comparison rows may be shown as context only when exact rows are unavailable; they must not influence projection or calibration.

For exact historical match labels, fixture-level API-Football team statistics may provide a real limited packet from observed fouls when the optional fixture-player endpoint is rate-limited. Keep the packet marked limited and retain its source; never replace it with possession, odds, or an aggregate opponent baseline.

**Why:** The player endpoint can stall or return empty while the exact fixture statistics endpoint remains usable; serial player enrichment made the history card time out and showed “not yet warmed” instead of available evidence.

**How to apply:** Bound optional player-action enrichment and let the exact fixture team-stat row classify from its available defensive field. Preserve the seven-row threshold for stable aggregate evidence.

The displayed 0–100 pressure value is a bounded synthetic index, not a count of pressure events; zero means the floor of the low band, not that the opponent applied no pressure. Exact-fixture history packets are explanation-only and must be cache-versioned when their response contract changes.

**Why:** Users can reasonably read “LOW 0/100” as literal zero pressure, and old cached packets otherwise hide new provenance or interpretation fields after a contract update.

**How to apply:** Label the UI as an index, show limited sample status, and explain the defensive-action/pass-volume inputs. Bump the exact-fixture cache identity whenever packet fields or semantics change.