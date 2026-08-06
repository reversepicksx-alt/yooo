---
name: Universal selection flow
description: Required behavior after a verified Soccer, MLB, or NFL result is tapped.
---

Selecting a universal player result must immediately commit the verified identity, switch the read-only sport state, and start that sport's existing context/next-match lookup. It must not leave MLB/NFL in an uncommitted pending-only state.

**Why:** A search result can be returned correctly while the UI still appears inert if the tap only updates text and never starts the sport-specific lookup.

**How to apply:** Keep provider response normalization and result-row callbacks separate from parent selection behavior; test the full tap path through player state, opponent, venue, and next-match loading for every supported sport.