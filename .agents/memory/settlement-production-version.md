---
name: Settlement production version
description: Settlement repairs can appear to regress when production is still serving an older backend build.
---

Terminal settlement-state fixes must be published before production behavior can be considered verified; a healthy older VM build may continue rewriting completed picks back to review.

**Why:** The workspace and production database checks disagreed because the live deployment was still running the pre-fix consistency guard.

**How to apply:** After changing settlement or consistency logic, verify the local ledger, confirm the deployment build version, and publish before diagnosing any remaining production review records as a code failure.