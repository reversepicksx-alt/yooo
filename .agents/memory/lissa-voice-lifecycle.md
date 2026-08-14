---
name: Lissa voice lifecycle
description: Architecture decisions for Lissa's activation, context wiring, AI path, and prompt design
---

# Lissa Voice Lifecycle

## Core architecture (as of 2026-08-14)
- **Activation**: Tap → silent (no intro speech). `primeSpeech()` just sets `setSpeechReady(true)` without speaking. iOS audio session primed by tap gesture; first Lissa answer unlocks TTS.
- **Wake word**: "Lissa" or "Lisa" detected by frontend regex. `requireWakeWord=true` on global instance.
- **Speech rate**: 1.08 (up from 0.94). Sounds faster and more natural.
- **Presence check**: "are you there/hello" → instant "Yeah, I'm here." (no backend call).

## Context wiring — all tabs
Every tab feeds `LissaScreenContext` so Lissa sees the full screen:
- **Predict** (`scan.tsx`): sets context when `phase === 'result'` with full `predictionState` (pick, analysis, factors, ledger)
- **My Picks** (`picks.tsx`): sets context when analysis modal is open
- **Community** (`community.tsx`): sets context with last 6 messages, participantCount, onlineCount
- **Account** (`account.tsx`): sets context with accountType, isOwner, isLifetime, subscriptionStatus
- **`_layout.tsx`**: `effectiveContext = { screen: screenContext, ...context }` — always merges; no tab filtering

## Backend screen guard
`_analysis_packet()` accepts screens: `{"my picks", "analysis", "pick analysis", "predict"}`. Other screens pass raw context fields (feed, accountType) directly to `_build_gemini_prompt`.

## AI path (Gemini-first)
1. Instant fast response (greeting/identity/screen-name) — no I/O
2. Match/fixture search (5.5s timeout)
3. `_smart_primary_response` — Gemini with full context (14s timeout)
4. Deterministic fallback: `_analysis_fallback` (pick open) or `_match_player` + `_summary_text`

## Picks cache
`_load_owner_picks_cached()` caches Atlas reads for 30s. Reduces Lissa latency for repeated questions.

## Frontend timeout
`LISSA_TIMEOUT_MS = 22_000` in `mobile/lib/api.ts`. Atlas + AI can take 12-15s.

## Prompt design rules (anti-bot)
- Never say "Certainly", "Of course", "Great question", "I understand"
- Never mention access limitations
- First sentence = direct answer
- Use real player names and actual numbers
- Two or three short paragraphs max
- Soccer intelligence block injected into every Gemini call

**Why:** The default Gemini output sounds like customer support. Explicit instructions produce natural speech.

## Error handling
- Timeout → spoken: "I took too long to respond. Try again."
- Unavailable → spoken: "Lissa is temporarily unavailable. Try again in a moment."
- Errors are always spoken AND shown as text.
