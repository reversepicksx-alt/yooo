---
name: xAI search API deprecated
description: xAI live search endpoint returns 410 as of June 2026. Press intensity must use knowledge-only path with reframed prompt.
---

## The Rule
Never call `_grok_search_call` / `_gemini_search_call` for press intensity. Use `_grok_call` (knowledge-only) directly.

**Why:** As of 2026-06, xAI deprecated their live search API. All calls return HTTP 410 with message "Live search is deprecated. Please switch to the Agent Tools API". The old fallback chain (search → knowledge) still hit the knowledge call, but the prompt had a `{"score": null}` escape clause that the model used for any 2025/2026 season-specific question it couldn't answer.

**How to apply:**
- `fetch_ai_press_intensity` in `backend/grok_engine.py` now calls `_grok_call` directly (strategy 1: temperature=0, strategy 2: temperature=0.3 retry).
- Prompt reframed as **tactical identity** (year-stable characteristic, not season stats) with 14 hard-coded reference anchors:
  - Liverpool≈0.72, Arsenal≈0.60, Dortmund≈0.60, Marseille≈0.60
  - Man City≈0.55, Bayern≈0.55, Napoli≈0.55
  - Barcelona≈0.50, Chelsea≈0.50, PSG≈0.45
  - Tottenham≈0.45, Inter≈0.45
  - Real Madrid≈0.40
  - Atletico Madrid≈0.30
- `{"score": null}` escape removed — model instructed to give best estimate using nearest reference team.
- This produces real scores (Liverpool=0.72, Chelsea=0.50) instead of universal null.

**Note on cache poisoning:** The old search path returned `""` on 410. If `""` was cached under the same key that the knowledge fallback read, it caused an instant cache-hit returning `""` → null parse → "No confident assessment". Fixed by removing the search path entirely.
