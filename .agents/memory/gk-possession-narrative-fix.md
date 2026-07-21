---
name: AI prediction narrative bugs — possession and player identity
description: Root causes of GK possession flip AND player identity/team hallucination — dom_notes, missing anchor injection, game log label, identity lock
---

## Rule
For GK pass_attempts props, two specific things cause the AI to apply the inverted possession rule backwards ("low poss → fewer GK passes" instead of "low poss → MORE GK passes"):

1. **dom_notes in dom_context** — `match_dominance["notes"]` entries contain "home 65% / away 35%" formatted text. The AI quotes this verbatim and then uses "home/away" labels (not team names) to assign possession to the player. If the player is listed as AWAY in the first prompt line, the AI assigns the "away %" to them — even if the explicit team-name instruction says their team has 65%.
   **Fix**: In the GK branch of `dom_context`, strip `{dom_notes}` entirely. Only use the team-name-labelled possession lines + GK-specific instruction.

2. **Missing GK causal explanation in bayesian_prompt_anchor** — The anchor only has the DIRECTION LOCK ("verdict is UNDER — support it") but no explanation of WHY the direction follows from possession. Without it, the AI falls back on outfield logic ("low possession = fewer passes") to explain the UNDER, producing the exact wrong narrative.
   **Fix**: After `gk_pass_context` is built (predict.py ~line 4748), if `_is_gk_for_passes` is True and `bayesian_prompt_anchor` is non-empty, append a `[GK PASS PROP — POSSESSION NARRATIVE RULE]` block that gives the explicit causal chain: "Cruz Azul = 65% → DOMINANT → GK barely touched → UNDER BECAUSE dominance, NOT struggle."

**Why:**
The AI gets ~10,000 chars of game log data and many competing instructions. Without the anchor injection, it reconciles "DIRECTION=UNDER" + "high possession" by assuming the team must have low possession. The anchor breaks this by providing the correct causal chain before the AI reasons about anything.

**How to apply:**
- predict.py: `dom_context` GK branch — no `{dom_notes}`
- predict.py: after `gk_pass_context` built — append to `bayesian_prompt_anchor` with ⛔ FORBIDDEN phrases for "struggles with possession" etc.
- Always clear `ai_response_cache` entries with `soc|.*pass_attempts` regex after changing GK prompt logic.
