---
name: Gemini migration (Grok → Gemini)
description: Full replacement of xAI Grok with Google Gemini across all AI functions.
---

## What changed
Every AI call that previously used xAI (`api.x.ai/v1`) now goes through the Google Gemini API.

## Key helpers (grok_engine.py)
- `_gemini_call()` — base text call; `thinking_budget=0` default (see below)
- `_gemini_search_call()` — same but adds `"tools": [{"google_search": {}}]` for live web grounding; also `thinkingBudget=0`
- `_ai_call()` — thin wrapper, delegates to `_gemini_call`

## Files changed
- `grok_engine.py` — `_ai_call`, `fetch_web_intel`, `fetch_opponent_ppda`, `_fetch_ai_press_intensity_inner`, `grok_scan_prop`, `_try_settle_wc_via_grok`
- `grok_positions.py` — `_grok_resolve_batch` → `_gemini_resolve_batch` using `GEMINI_API_KEY`
- `routes/miss_analysis.py` — Grok model removed; replaced with `call_gemini_direct` (JSON mode) + emergent `gemini-2.5-flash`
- `routes/tactical.py` — Grok fallback replaced with Gemini Flash fallback

## Critical: thinkingBudget=0
Gemini 2.5 Flash is a **thinking model**. With default settings it spends tokens on internal reasoning BEFORE writing output. With a low `maxOutputTokens` (e.g. 10–20), it exhausts the budget on thinking and returns empty `parts`. Fix: always pass `"thinkingConfig": {"thinkingBudget": 0}` for short/structured calls.

**Why:** `thoughtsTokenCount` counts against `maxOutputTokens`. A call with `maxOutputTokens=20` and 17 thought tokens returns `content: {role: "model"}` with no `parts`.

**How to apply:** `_gemini_call` and `_gemini_search_call` both default to `thinkingBudget=0`. For deep reasoning (synthesis, tactical chat), pass `thinking_budget=1024` or higher explicitly.

## Scan / vision
`grok_scan_prop` now uses Gemini vision via `inline_data` (base64 PNG). Prompt and JSON normalization unchanged.

## Position resolution
`grok_positions.py` uses `responseMimeType: "application/json"` (JSON mode) for reliable parsing. Cache-first logic unchanged.
