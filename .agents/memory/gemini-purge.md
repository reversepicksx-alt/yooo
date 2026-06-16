---
name: Full Gemini purge
description: All Gemini API calls have been replaced with Grok (xAI) throughout the entire backend. Relevant patterns and what replaced them.
---

Trigger: Gemini 429 rate limiting caused WC prediction timeouts when background settlement + user predictions ran concurrently.

**What was replaced:**
- `_grok_call(prompt, system, temperature, max_tokens, timeout, model, json_mode)` — primary call, xAI API
- `_grok_search_call(prompt, max_tokens, timeout, model)` — Grok with `search_parameters: {mode: "auto"}` for live web search
- `_gemini_call` / `_gemini_search_call` kept as backward-compat aliases (route to Grok)
- `gemini_scan_prop()` → uses `grok-2-vision-1212` model for OCR
- `fetch_web_intel()` → uses `_grok_search_call` + 10-min in-memory cache (key: `player_team|opponent|date`)
- Sport routes (nba/nhl/nfl/pga/mma/lol/dota2/cbase/ncaab/ncaaw/ncaaf/f1/atp/wnba): `genai.GenerativeModel` → `_grok_call(prompt, temperature=0.7, max_tokens=1500, timeout=30)`
- LlmChat calls: `.with_model("gemini", "gemini-2.5-flash")` → `.with_model("xai", "grok-3")`
- LlmChat defaults changed to `_provider="xai"`, `_model="grok-3"`
- `grok_positions.py` fully rewritten with `_grok_call`
- `server.py` batch position resolver uses `_grok_call`
- `miss_analysis.py` uses two `_grok_call` calls (json_mode=True + fallback)
- `ai_sports_routes.py`, `cs2_routes.py`, `wta_routes.py`, `mlb_routes.py` all converted

**Why:** Gemini has a single shared quota and no retry budget for concurrent calls. xAI Grok has much higher limits and per-call retry logic (3 attempts with exponential backoff) is built into `_grok_call`.

**How to apply:** Any new AI call in the backend must use `_grok_call` or `_grok_search_call` from `grok_engine.py`. Never add new `generativelanguage.googleapis.com` calls. The GEMINI_API_KEY env var still exists in config.py but nothing calls it.
