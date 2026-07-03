---
name: Silent asyncio alias NameError swallowed by bare except
description: A bare `except Exception: pass` around an asyncio.gather call hid a NameError caused by an import-alias mismatch, silently disabling live-fixture odds/possession lookup for any in-progress match.
---

## What happened
`backend/routes/predict.py` imports `import asyncio as aio` (project convention in several backend files to avoid shadowing local variables named `asyncio`), but one code path inside `get_match_odds()` called `asyncio.gather(...)` directly (the un-aliased name). That raised `NameError: name 'asyncio' is not defined` on every call — but it was caught by a bare `except Exception: pass` and never logged, so it looked identical to "no data available" from the outside.

## Why it mattered
That specific `asyncio.gather` call was the "check today's live/in-progress fixtures" fallback — needed because API-Football's `next: N` fixtures endpoint only returns **not-yet-started** matches. So this bug meant: for ANY fixture that had already kicked off (live match), the odds/possession/fixture-context lookup silently failed and fell through to "no fixture_match found", even though standalone testing of the exact same API calls (outside the running function) succeeded — because standalone test scripts don't have the `as aio` aliasing mismatch.

## Why it was hard to find
Debugging by re-running the *isolated* API call steps outside the app (e.g. in a scratch script) will always "work" and mask this class of bug, since the scratch script doesn't reproduce the aliased-import shadowing. The only way to catch it was adding print statements at each internal step *inside the running function* and checking for `isinstance(batch, Exception)` results from `return_exceptions=True` gathers, or removing bare `except: pass` temporarily to see the swallowed traceback.

**Why:** Multiple backend files intentionally alias `import asyncio as aio` (to avoid conflicts with local vars), but this isn't 100% consistent across the codebase — some files use the plain `import asyncio`. Copy-pasting code between these files is a common way to introduce this exact bug.

**How to apply:** When a data-lookup path that provably works in isolation still fails "silently" inside the running server (especially wrapped in `except Exception: pass`), suspect a swallowed exception before suspecting missing/absent data. Grep the file for its actual `import asyncio` line before trusting any `asyncio.X` call within it. Prefer `except Exception as e: print(...)` (even temporarily) over bare `pass` when debugging "no data found" symptoms — bare `except: pass` blocks are a common way real bugs get misdiagnosed as data-availability issues.
