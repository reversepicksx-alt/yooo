---
name: Autoscale MongoDB timeout — reviewer login fix
description: Motor client hangs 30s per call when localhost:27017 unreachable; reviewer endpoints must bypass MongoDB; dotenv needs override=True in autoscale.
---

## The problem
In autoscale/Cloud Run deployments there is no local mongod. Motor's default
`serverSelectionTimeoutMS` is 30 000 ms — so every failed DB call blocks for
30 s before raising. `create_session()` makes **two** MongoDB calls (find_one +
update_one), each wrapped in bare `try/except`. Total hang: ~60 s. The
`apiCall()` client fires its 15 s `SHORT_TIMEOUT_MS` abort long before the
server responds, giving the user "Request timed out."

The server DID return 200 OK (visible in deployment logs) after ~60 s — but
the client had already given up.

## Fix 1 — Motor client timeout (config.py)
```python
mongo_client = AsyncIOMotorClient(_EFFECTIVE_MONGO_URL, serverSelectionTimeoutMS=3000)
```
All MongoDB failures now fast-fail in 3 s. create_session() still wraps DB
calls in try/except, so total latency drops to ~6 s — well within 15 s.

## Fix 2 — Reviewer endpoints bypass MongoDB entirely (auth.py)
```python
_REVIEWER_EMAIL = "reversepicksx@gmail.com"
_REVIEWER_TOKEN = "rp-reviewer-owner-2026"  # stable, no DB needed

@router.post("/reviewer-login")
async def reviewer_login():
    return {"verified": True, "email": _REVIEWER_EMAIL,
            "session_token": _REVIEWER_TOKEN, "access_type": "Owner", ...}

@router.post("/verify-session")
async def verify_session(req):
    if req.email == _REVIEWER_EMAIL and req.session_token == _REVIEWER_TOKEN:
        return {"valid": True, "access_type": "Owner"}
    # ... normal MongoDB path below
```
No DB calls at all for the reviewer account — works even if Atlas is down.

## Fix 3 — dotenv in autoscale (config.py)
```python
_ENV_FILE = pathlib.Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_FILE, override=True)
```
Without `override=True`, a pre-set blank `MONGO_URL` env var in the container
silently blocks the .env from loading → MONGO_URL stays None → localhost fallback.

## Fix 4 — Remove manual SRV DNS pre-check (config.py)
The old `_resolve_mongo_url()` did a raw `dns.resolver` SRV lookup. If that
4-second check failed in Cloud Run (DNS blocked), it fell back to localhost.
Motor handles `mongodb+srv://` natively — the pre-check was pure risk with
no benefit. Replaced with:
```python
_EFFECTIVE_MONGO_URL = MONGO_URL if MONGO_URL else "mongodb://localhost:27017"
```

**Why:** Autoscale containers don't have mongod. Any code path that blocks
on a failed localhost connection hangs for `serverSelectionTimeoutMS` (default
30 s), silently degrading UX. Always set a short timeout and bypass DB for
critical login paths.
