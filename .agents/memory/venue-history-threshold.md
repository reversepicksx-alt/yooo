---
name: Verified venue-history threshold
description: Venue-specific soccer priors require a substantial verified player sample and an auditable fallback.
---

Venue-specific soccer priors require 30 verified player appearances at the selected venue, with older seasons and competitions searched before activation. If the target is unavailable, use the full verified history rather than a thin venue slice, and expose the fallback status.

**Why:** A recent team-fixture window can contain only a few player appearances at one venue; letting five rows replace the broader history overweights noise and makes the UI look more certain than the evidence supports.

**How to apply:** Count only exact-player rows with minutes and the requested provider stat present. Keep opposite-venue rows out of the venue prior, but preserve full verified history for the below-target fallback and label the sample status for customers. Team-fixture discovery may be empty under the shared provider budget, so the bounded player-fixture fallback must remain available.