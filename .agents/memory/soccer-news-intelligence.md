---
name: Soccer news intelligence
description: Durable evidence, lineup, network-safety, and math-isolation rules for current soccer news audits.
---

**Rule:** Soccer news intelligence is observational only. It may explain current context and flag a confirmed-lineup drift for review, but it must never alter the Reverse Picks projection, probabilities, recommendation, saved pick, or calibration inputs.

**Why:** The product contract keeps Reverse Picks as the sole production model while allowing JARVIS to expose independent, auditable context.

**How to apply:** Snapshot production math before research, attach news afterward, and preserve explicit `shadow_only` / `math_unchanged` metadata on every success and fallback response.

**Rule:** A web article can become current evidence only when it explicitly matches its assigned club, player, or competition and has a parseable publication time inside the topic's freshness window. Missing, invalid, stale, or unrelated results may inform source discovery but must not drive findings.

**Why:** Live search validation returned plausible football stories about unrelated clubs and old articles that query assignment alone made look relevant.

**How to apply:** Gate relevance before fetch and persistence, retain source provenance, deduplicate provider rows, and use `UNKNOWN` when no qualifying current finding remains.

**Rule:** Player lineup status requires direct player-ID observation in the starters or substitutes. Omission from a partial XI or incomplete substitute list never proves bench, absence, or zero start probability.

**Why:** Provider lineup payloads can be truncated; treating omission as confirmed absence fabricates material drift and minutes risk.

**How to apply:** Compare full XIs and formations only when structurally credible, keep target status pending when the player is not directly observed, and flag rather than automatically rerun when real drift exists.

**Rule:** Direct article fetching must use the exact validated public IP for the connection while retaining the original hostname for TLS validation, with redirects revalidated per hop and strict time/size limits.

**Why:** DNS preflight followed by a separately resolved connection leaves a DNS-rebinding SSRF gap.

**How to apply:** Use a pinned resolver or equivalent enforced egress control; reject private/reserved addresses, userinfo, non-web ports, automatic redirects, and oversized responses.