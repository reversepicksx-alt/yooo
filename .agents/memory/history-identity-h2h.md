---
name: History identity and H2H coverage
description: Durable rules for preventing repeated player-history rows and silent H2H blanks.
---

Player history must be deduplicated before sample counts, Bayesian inputs, hit rates, and response payloads are built. Prefer a stable fixture ID; when unavailable, use a bounded date/team/opponent/venue/stat fallback and retain the richest verified row.

**Why:** Stage-0 cached player rows and Stage-1 fixture hydration can describe the same appearance, while provider responses may arrive in either a bare-list or API-envelope shape. Without normalization and identity deduplication, the UI and model report inflated history or silently empty H2H.

**How to apply:** Normalize provider payloads at each boundary, dedupe both recent player logs and player-specific H2H appearances, and return an explicit H2H coverage/status object even when direct meetings or player appearances are unavailable.