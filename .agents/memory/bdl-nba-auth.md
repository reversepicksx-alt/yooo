---
name: Live NBA provider authorization
description: BallDontLie NBA stats can fail authorization while cached player identities still make search look healthy.
---

Live NBA validation requires a working BallDontLie credential in the backend workflow. A cached player-search result does not prove that stats requests are authenticated; always exercise `/stats` before claiming that real NBA predictions were generated.

**Why:** During the August 18, 2026 NBA re-enable work, player search returned a cached Nikola Jokic identity while live stats requests returned HTTP 401 and produced no game logs.

**How to apply:** Keep prediction data failures explicit and user-safe, and use a real stats request plus a limited settled-row sanity check after any credential repair.