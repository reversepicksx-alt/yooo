---
name: AI spend governance
description: Durable rule for one bounded cached Gemini paragraph and deterministic math
---

External language and vision generation remains disabled except for one short Gemini paragraph per finalized soccer pick. The paragraph is wording only: projection, direction, confidence, and evidence come from the finalized deterministic ledger. API-backed data retrieval and structured sports math remain allowed.

**Why:** Shared provider budget exhaustion made explanations unavailable and multiple legacy entry points could bypass a single guard. The narrow exception restores useful customer wording without reopening chat, OCR, background enrichment, or long reports.

**How to apply:** Bound output and evidence, serialize duplicate generation by finalized ledger identity, cache successful text, count attempts against a daily limit, and fail back to compact deterministic text on errors.