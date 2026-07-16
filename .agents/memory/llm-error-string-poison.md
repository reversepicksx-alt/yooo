---
name: LLM error string poison in AI cache
description: Replit Gemini integration returns "[LLM Error: ...]" as response TEXT on quota/API errors — must be filtered before caching.
---

The Replit Gemini integration library (`backend/emergentintegrations/llm/chat.py` line 88) catches API exceptions (including `429 RESOURCE_EXHAUSTED`) and returns them as the response **text** string:

```python
return f"[LLM Error: {e}]"
```

**Why this is dangerous:** `_ai_call` in `ai_engine.py` checks `if _result:` (non-empty string) to decide whether to cache and return. A `[LLM Error: ...]` string is non-empty → gets cached in `ai_response_cache` → returned to the caller → ends up verbatim in `tacticalBreakdown`, `sharpSummary`, etc. Users see the raw error message.

**Fix applied:** Before caching, check `_result.startswith("[LLM Error:")`. If true, treat it as a failed attempt (fall through to retry loop), not a successful response.

**How to apply:** Any code that calls `_ai_call` / `_gemini_call` and then displays the result to users must guard against this. The filter is in `_ai_call` itself, so all callers are protected once it's there. If the filter is ever removed or bypassed, the poison will reappear in predictions.

**Related:** Also clear any cached `[LLM Error:]` entries from `ai_response_cache` when deploying this fix (query: `{v: {$regex: "^\\[LLM Error"}}`).
