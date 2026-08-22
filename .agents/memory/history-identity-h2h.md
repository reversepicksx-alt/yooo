---
name: History identity and H2H coverage
description: Durable rules for preventing repeated player-history rows and silent H2H blanks.
---

Player history must be deduplicated before sample counts, Bayesian inputs, hit rates, and response payloads are built. Prefer a stable fixture ID; when unavailable, use a bounded date/team/opponent/venue/stat fallback and retain the richest verified row. Display-only H2H must have its own bounded window and may reuse exact fixture/player cache rows after the core prediction budget is spent.

**Why:** Stage-0 cached player rows and Stage-1 fixture hydration can describe the same appearance, while provider responses may arrive in either a bare-list or API-envelope shape. The main prediction pipeline can also consume its global budget before the H2H display fan-out runs, even when exact player appearances are already available.

**How to apply:** Normalize provider payloads at each boundary, dedupe both recent player logs and player-specific H2H appearances, and return an explicit H2H coverage/status object even when direct meetings or player appearances are unavailable.