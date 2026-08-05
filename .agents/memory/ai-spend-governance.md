---
name: AI spend governance
description: Durable rule that external language and vision generation is disabled.
---

External language and vision generation is permanently disabled. User-facing explanations must be deterministic and derived only from the finalized model ledger and recorded evidence. API-backed data retrieval and structured sports math remain allowed.

**Why:** Shared provider budget exhaustion made explanations unavailable and multiple legacy entry points could bypass a single guard. A deterministic-only policy removes that failure mode and keeps explanations reproducible.

**How to apply:** Do not add provider clients, provider keys, generation calls, OCR, tactical generation, or background enrichment. Return an explicit unavailable response for features that cannot be implemented with structured data.