---
name: Gemini credit cutoff
description: Emergency operating mode and narrowly controlled exception for short cached match explanations
---

Gemini generation remains disabled globally except for the explicitly bounded soccer match-explanation path: one short call after the final ledger, with a compact evidence packet, strict output cap, daily attempt budget, and ledger-bound cache. Chat, OCR/vision, background enrichment, and direct legacy adapters remain disabled.

**Why:** The earlier broad disable protected against repeated background, fallback, chat, and vision requests. The exception restores useful customer wording without reopening those surfaces or the old long report.

**How to apply:** Keep `api_version: ''` on the Replit Gemini client, never pass provider names to customers, and verify cache/budget/fallback behavior after deployment. Do not re-enable other generation surfaces.