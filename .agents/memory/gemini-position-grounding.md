---
name: Gemini position grounding
description: Grounded identity verification must require actual Search citations because the managed proxy may return model-only answers.
---

Only accept a Gemini player-position result when the response contains real Search grounding chunks with web URLs. A valid JSON shape or a plausible answer is not sufficient; when citations are absent, keep the provider-category fallback and leave the tactical role unavailable.

**Why:** The managed Gemini proxy can expose a Search-capable response while returning zero grounding chunks, so accepting the text would turn model memory into unverified identity data.

**How to apply:** Extract both SDK and serialized grounding metadata, retry once with an explicit live-search instruction if needed, reject and avoid caching ungrounded results, and preserve the conservative provider-category fallback.