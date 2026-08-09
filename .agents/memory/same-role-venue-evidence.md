---
name: Same-role venue evidence
description: Exact-opponent same-role comparison evidence must use the target player's matching home or away venue.
---

Same-role and same-position opponent evidence is venue-specific: a home target uses comparison players who faced that opponent at home, and an away target uses comparison players who faced that opponent away. Prior seasons may broaden time, never venue.

**Why:** A mixed-venue fallback widened sparse cohorts and displayed “home + away fixtures,” making opponent evidence look comparable when the match context was not.

**How to apply:** Keep the venue filter active in every comparison fetch and aggregation path. If the venue-specific sample is thin, disclose limited evidence rather than adding opposite-venue rows or relabeling the scope as mixed venue.