---
name: StatsBomb Open Data evidence boundary
description: StatsBomb Open Data supplies exact-match historical event metrics and optional 360 snapshots with restricted public coverage.
---

StatsBomb Open Data is a separate, projection-neutral evidence layer. Match identity requires an exact published date plus normalized home/away opponent names within a mapped competition season. Event-derived PPDA uses explicit 120x80 thirds and attacking-direction inference from event data; it is not continuous tracking. Pressure events, pressure by third, passes under pressure, defensive actions in the press zone, pressure regains, and optional 360 freeze-frame summaries may support tactical explanations and audits. Missing competition, match, event, lineup, or 360 coverage must remain explicitly unavailable rather than zero.

**Why:** StatsBomb provides high-quality public historical event data, but only for selected competitions/matches and without universal tracking. Treating restricted coverage or absent optional fields as measured zeros would overstate evidence and contaminate explanations.

**How to apply:** Keep API-Football authoritative for current fixture identity, projections, calibration, and settlement. Attach StatsBomb packets to tactical context, model snapshots, and deterministic explanations only. Require leakage-safe settled replay before activating any projection adjustment.