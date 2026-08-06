---
name: Universal player search
description: Product interaction for selecting among the currently supported player-prop sports.
---

The player entry point is intentionally universal: search soccer, MLB, and NFL from one field, show each result's sport, and switch the read-only sport badge and form only after the user selects a verified result. There is no manual sport selector.

**Why:** A sport picker made the user choose the sport before identity verification and hid the fact that the player search itself can determine the correct provider.

**How to apply:** Keep provider-specific confirmation, fixture lookup, props, and prediction handlers behind the shared search result; do not reintroduce a separate sport selector unless additional supported sports require a deliberate product decision.