---
name: AI spend governance
description: Durable rules for keeping Gemini explanations available while bounding shared daily spend.
---

The AI policy is two-tiered: user-facing prediction, chat, and OCR explanations may use Gemini; web intelligence, pressing identity, tactical DNA, position backfills, match reviews, and daily-pick enrichment are background work and are disabled unless explicitly enabled.

**Why:** A call-count guard was easy to bypass because several routes used direct LlmChat or background helpers, and VM-local counters could reset. The shared persistent token reservation is the only reliable ceiling.

**How to apply:** New Gemini entry points should use `_ai_call` or `reserve_ai_budget`, classify calls with a `budget_source`, and treat cache hits as free. Background sources must begin with `background` so the default policy rejects them. Keep the MongoDB conditional increment atomic across workers.