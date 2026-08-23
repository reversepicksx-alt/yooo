---
name: JARVIS Script Hunt evidence gate
description: Script Hunt must evaluate matching-venue control evidence before board lookup or ranking.
---

Script Hunt is an evidence-gated workflow: fixture discovery alone must never create candidates. Matching-home and corresponding-away possession/control samples are required; UNKNOWN or CONTRADICTED fixtures stay out of the ranked shortlist and are reported as rejected.

**Why:** Provisional fixture lists made the owner workflow appear intelligent while providing no verified home-control basis for the recommended prop paths.

**How to apply:** Keep fixture evaluation bounded and expose evaluated, rejected, unevaluated, board, ranking, and deep-audit counts. Provider diagnostics must distinguish a configured reasoning model from a provider call that actually executed.