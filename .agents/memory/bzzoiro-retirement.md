---
name: Bzzoiro retirement
description: Bzzoiro is retired from the active prediction product and must not influence user-facing analysis.
---

Bzzoiro is retired from the active product surface. Predictions should use the verified first-party fixture and lineup evidence paths, with no Bzzoiro fetch, prompt context, saved supplement, or customer-facing UI.

**Why:** The product owner explicitly removed the Bzzoiro-related behavior and UI; retaining it in the active prediction path could reintroduce unsupported provider data.

**How to apply:** Keep any remaining provider or historical replay code dormant and compatibility-only. Do not wire it back into prediction enrichment, tactical packets, persisted prediction records, or mobile screens without an explicit request.