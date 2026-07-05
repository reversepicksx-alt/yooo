---
name: AI narrative permanently stuck on loading placeholder
description: Why the mobile "AI analysis loading..." card could get permanently stranded, and the general pattern to prevent it
---

## Symptom
A frontend section that polls a backend for an async AI-generated result (e.g. a "SHARP ANGLE" tactical breakdown) stays on its loading placeholder forever, even though the underlying math/prediction result loaded fine.

## Root causes found
1. A duplicate function definition of the same name existed later in the module and shadowed the real implementation, turning every call into infinite recursion / a silent crash swallowed by a bare `except Exception: return ""`.
2. The AI call itself (Gemini) had no retry and no enforced per-attempt timeout. A single transient failure (rate-limit blip, empty response, brief network hiccup) permanently returned `""`, and the polling job was marked `failed=True` forever with no way to recover.
3. Fire-and-forget `asyncio.create_task(...)` for background AI synthesis had no strong reference held anywhere — tasks can be silently garbage-collected mid-flight.

**Why:** Any async background-job pattern (fire a task, poll a status doc) needs the *called* function itself to be internally resilient (retry+timeout), not just the polling wrapper — the polling loop retrying doesn't help if the underlying job already permanently recorded `failed=True`.

**How to apply:** When debugging "stuck on loading forever" bugs in this codebase, check: (1) no shadowed/duplicate function defs in the AI/engine module, (2) the AI call wrapper has retry+timeout, not just a bare try/except, (3) background tasks are held in a strong-ref set, (4) the workflow has `PYTHONUNBUFFERED=1` so retry/error prints aren't stuck in a stdout buffer during live debugging — buffered output made an already-fixed retry loop look like it wasn't running.

## Critical: dev fix != production fix (VM deployments)
This app is a `vm` deployment (always-on process, not autoscale) that shares the SAME Atlas MongoDB cluster as the dev workspace. Fixing and verifying the bug in dev does **not** patch production — a VM deployment only picks up code changes on the next explicit redeploy/publish.

**Why this bit us:** a genuinely-fixed dev backend kept looking successful in local testing, while `fetch_deployment_logs` simultaneously showed production throwing a *different, older* error (`_ai_call() got an unexpected keyword argument 'json_mode'`) on ~100% of real AI calls — a stale build predating a function-signature change. Both dev and prod were writing to the same `ai_pending_jobs`/`ai_response_cache` collections, so querying "recent" job docs from dev mixed genuine dev-test successes with real production failures, which was very confusing until timestamps + `fetch_deployment_logs` were cross-checked.

**Also:** the math-only fallback text (`[PURE MATH] AI summary absent`) is well-written enough to read as genuine tactical analysis — a Playwright UI-text check alone can't tell them apart and will false-positive. To verify AI is *actually* working, query the DB record for `_source` (or check for `[AI-BG] ... succeeded` in logs / absence of `[MULTI-AI] ... failed` in **deployment** logs, not just dev workflow logs), never just eyeball the rendered paragraph.

**How to apply:** Whenever a user reports a persistent bug that dev testing says is "fixed," and this project is deployed, always check `fetch_deployment_logs` for the same error signature in production before declaring victory — and if production is stale, the fix isn't done until the user (re)publishes.
