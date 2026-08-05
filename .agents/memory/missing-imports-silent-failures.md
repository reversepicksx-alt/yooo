---
name: Missing imports cause silent endpoint failures
description: asyncio.gather / module-level names used in routes/misc.py were never imported — NameErrors caught by except Exception produced silent wrong results for years
---

## The Rule
Any time `asyncio.gather()` or a module-level constant (e.g. `INTERNATIONAL_LEAGUES`) is added to a route file, verify the import exists at the top of that file. A `NameError` inside an `except Exception: pass` block silently sets a local variable to `[]` and falls through to a history fallback — the endpoint returns 200 OK with wrong data, no traceback, no log.

**Why:** `routes/misc.py` used `asyncio.gather()` in `team_next_match` steps 0 and 1 without `import asyncio`, and referenced `INTERNATIONAL_LEAGUES` (defined in `config.py`) without importing it. Both failures were caught by `except Exception: pass`, so the endpoint returned `found: false` with `leagueFromHistory: true` for every MLS/Liga MX team playing in Leagues Cup — while isolated Python tests worked fine because they had no competing `except` handler.

**How to apply:**
- After writing any new `asyncio.gather()` call in a route file, grep for `^import asyncio` in that file.
- After referencing any config constant in a route file, grep for the import at the top.
- When an endpoint returns a "fallback" response (found:false, leagueFromHistory:true, etc.) but isolated tests succeed, suspect a silently-caught NameError in the primary path.
- Debug by temporarily printing inside the `except Exception` handler before assuming the API is at fault.

**Second bug in same incident:** `fast_endpoint` was used but never defined in `routes/players.py` — should be `"players/profiles"`. Same pattern: no crash, just every player search falling through to exception handler and logging a confusing NameError message.
