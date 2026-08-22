---
name: Manifest validation
description: Large machine-readable exports must be validated with a strict parser and kept patchable.
---

Structured exports should use valid JSON numeric syntax, contain exactly one root object, and be checked with `json.loads` plus required-section assertions before delivery.

**Why:** One-line manifests are difficult to patch safely; delimiter mistakes and shorthand decimals can survive visual inspection while failing strict parsing.

**How to apply:** Prefer readable multi-line JSON for documentation exports, then run strict parsing and verify the required schema keys.