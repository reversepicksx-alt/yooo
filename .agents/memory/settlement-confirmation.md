---
name: User settlement confirmation
description: Safety boundary for subscriber-triggered rechecks of saved soccer settlement results.
---

User-triggered settlement confirmation must be a distinct operation from a generic analysis refresh and from an owner/admin correction. It should force a new exact-fixture and exact-player provider read, validate the finished fixture/player/stat identity, and persist the provider provenance with the recalculated result.

**Why:** A cached or incrementally populated fixture/player response can make an already-settled player prop look final with the wrong value. Reusing a repair flag for a fresh read is unsafe because repair paths may intentionally bypass zero-stat or incomplete-data deferrals.

**How to apply:** Add a protected per-pick confirmation path that owns the result calculation and persistence. Force-refresh the exact provider calls, keep ordinary data-quality guards active, reject unverified/missing rows without changing the saved pick, and retain a bounded before/after audit record.

Once an exact-match confirmation resolves a provider conflict, normal background
settlement must not overwrite it with a later stale or unavailable canonical-
provider response. If the canonical provider is temporarily empty, a forced
confirmation may use the already-saved exact fixture identity only to query an
independent exact-match source; if that source cannot verify the stat, leave the
saved result unchanged.

**Why:** API-Football can return a contradictory player row or no exact fixture
row under quota pressure. Re-running the ordinary poll after a successful
correction otherwise silently restores the old result when the subscriber
refreshes another pick.

**How to apply:** Persist an auditable settlement lock for confirmed exact-match
corrections, exclude locked picks from list/background settlement writes, and
keep provider disagreement/availability in provenance rather than presenting a
silent fallback as canonical-provider confirmation.