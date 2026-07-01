---
name: Deployment backend supervisor + ASGI startup safety
description: Why the vm deployment must supervise uvicorn and why the ASGI startup event must never raise
---

# Backend must be supervised in production; ASGI startup must never raise

Two structural failure modes caused a full production outage ("Backend unavailable"
on login; proxy returns 502 because uvicorn on localhost:8000 was down):

1. **No supervisor.** The `vm` deployment run command started uvicorn once via a
   backgrounded subshell (`(sleep 5 && uvicorn) &`). A single startup crash left
   the backend permanently dead for the whole deployment lifetime — nothing
   restarted it. The dev workflow hid this because it runs uvicorn as its own
   long-lived foreground workflow.
   **Fix:** run uvicorn inside a `while true; do uvicorn; sleep 3; done` loop in
   the deploy run command (set via `deployConfig`, NOT by editing `.replit`
   directly — that is blocked). Proxy still runs in the foreground.

2. **Lifespan can crash the worker.** `@app.on_event("startup")` had unwrapped
   imports/awaits (team_resolver, grok_engine, league_priors, data_prefetch). Any
   exception there aborts uvicorn's lifespan → worker exits → (with no supervisor)
   backend stays down.
   **Fix:** the startup body lives in `async def _run_startup_tasks()`; the
   `on_event("startup")` handler wraps it in try/except so the lifespan NEVER
   raises. The port always binds and the API (login/predict) stays reachable even
   if background init fails.

**Why:** deploy log stream drops lines (`EIO: i/o error, read`), so the real
startup traceback was never captured — you cannot rely on prod logs to diagnose a
one-shot startup crash. The supervisor loop turns a permanent silent death into
either auto-recovery (transient cause) or a visible crash-loop whose repeated
tracebacks are finally readable.

**How to apply:** any always-on backend on a Replit `vm` deployment needs a
restart loop around the server process, and its framework startup/lifespan hook
must be exception-safe. mongod robustness in the same command: `rm -f mongod.lock`
before start + a `--repair` fallback; `mongod --fork` blocks until ready so no
`sleep` race with uvicorn. Residual gap: if `_run_startup_tasks` throws midway,
background loops (auto-settlement, calibration, prefetch) silently never start —
consider surfacing a startup-status flag on `/api/health`.
