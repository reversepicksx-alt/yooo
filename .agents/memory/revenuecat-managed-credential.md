---
name: RevenueCat managed credential access
description: RevenueCat may appear attached while managed credentials are withheld from shell and sandbox execution.
---

RevenueCat catalog mutations require the attached managed connector with project-configuration write permissions; the environment secret may identify a project but can point at a different/insufficiently authorized API context.

**Why:** A direct API key lookup returned an empty or unauthorized project while the attached connection was marked available, and the connector credential was withheld from execution.

**How to apply:** Verify the attached connection can list the production project, offerings, and packages before mutating them. If credentials are withheld, keep safe app-side filters and defer catalog mutation instead of guessing or deleting products.