---
name: Gemini credit cutoff
description: Emergency operating mode that disables all Gemini generation while preserving deterministic prediction math
---

Gemini generation must remain disabled globally when credit protection is active. This includes shared prediction calls, background narrative synthesis, tactical chat, OCR/vision, and direct LLM adapter calls; deterministic Bayesian/math predictions must continue without waiting for AI.

**Why:** Repeated background, fallback, chat, and vision requests consumed credits too quickly. A single wrapper guard was insufficient because several routes instantiated the LLM adapter or Google client directly.

**How to apply:** Re-enable only by deliberately changing the global feature flag, restoring any client-side AI requests, rebuilding the web bundle, restarting services, and publishing the updated deployment. Do not infer production is protected from local verification alone.