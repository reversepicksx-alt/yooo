---
name: Automatic Community pick images
description: The contract for automatically publishing the strongest active pick with its rendered card image.
---

Automatic Community posting must use the actual rendered OwnerPickCard image, not a server-reconstructed text-only card. The Picks screen captures only the highest-confidence active qualifying card and submits the image; the backend independently rechecks that the submitted pick is still the top active pick with confidence at least 60%.

**Why:** A save-time server post could publish a lower-confidence pick before the UI had selected/captured the strongest card, and server-side rendering would not match the user-facing card.

**How to apply:** Keep the post idempotent by pickId. Allow a later image submission to upgrade an existing caption-only legacy/early post, but never create a second auto post. Preserve Bayesian-direction probability and projection edge as tie-breakers after confidence.