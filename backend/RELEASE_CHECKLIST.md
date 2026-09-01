# Release Test Checklist

## Quick guide to the three test tiers

| Tier | What it covers | Command | Must pass before shipping? |
|------|---------------|---------|---------------------------|
| **Unit** (default) | Deterministic engine logic — Bayesian math, calibration, settlement, model metrics, next-fixture selection | `cd backend && python3.12 -m pytest` | ✅ Yes |
| **Integration** | Live HTTP against a running backend server | `cd backend && python3.12 -m pytest tests/integration/ -m integration -v` | Recommended on staging |
| **External** | Live queries to third-party providers (BDL, API-Football, StatsBomb) | `cd backend && python3.12 -m pytest tests/external/ -m external -v` | Optional — provider availability varies |

---

## 1 — Deterministic unit tests (always run)

```bash
cd backend && python3.12 -m pytest
```

These must be **green** before any release.  A failure here is a code regression.
**Verified result: 143 passed, 0 failed.**

The `integration/`, `retired/`, and `external/` subdirectories are excluded from
this default run via `pytest.ini` (`norecursedirs`).  Because those directories
are excluded, running `python3.12 -m pytest -m integration` from the backend root
collects nothing — the subdirectory path must be supplied explicitly (see tier 2
below).  The `integration` and `external` markers are applied by `conftest.py` at
collection time — no per-file decoration is required.

Current deterministic test modules (all pass, no network needed):

| Module | What it checks |
|--------|---------------|
| `test_bayesian_engine.py` | Covariate caps, momentum, streak detection, variance, edge cases |
| `test_calibration_alerts.py` | Calibration alert thresholds |
| `test_fusion.py` | Tactical route deterministic-policy contract |
| `test_lineup_response_shape.py` | Lineup API response shape |
| `test_model_metrics.py` | Model accuracy metric calculations |
| `test_model_replay.py` | Offline prediction replay logic |
| `test_next_fixture_selection.py` | Nearest-future fixture selection logic |
| `test_pass_calibration_metrics.py` | Pass prop calibration metric calculations |
| `test_pass_projection_calibration.py` | Pass projection calibration math |
| `test_positional_reality.py` | Position-accuracy system |
| `test_prediction_quality.py` | Evidence-quality gate and confidence caps |
| `test_pressure_response.py` | Player pressure-response profile logic |
| `test_settlement_integrity.py` | Settlement state-machine and void logic |
| `test_statsbomb_client.py` | StatsBomb open-data event parsing |

---

## 2 — Integration tests (requires running backend)

```bash
# Start the backend first (separate terminal):
mkdir -p /home/runner/.reversepicks_db
mongod --dbpath /home/runner/.reversepicks_db --logpath /home/runner/.reversepicks_mongo.log --fork --quiet
cd backend && PYTHONUNBUFFERED=1 python3.12 -m uvicorn server:app --host 0.0.0.0 --port 8000

# Then in another terminal, run the integration tier:
cd backend && python3.12 -m pytest tests/integration/ -m integration -v
```

By default this targets `http://127.0.0.1:8000` unconditionally — any pre-existing
`REACT_APP_BACKEND_URL` in the shell is ignored so a stale env var cannot
silently redirect to a remote service.  Override explicitly with `BACKEND_URL`:

```bash
BACKEND_URL=https://staging.example.com python3.12 -m pytest tests/integration/ -m integration -v
```

**Verified result (against a running local backend): 352 tests collected.**

A failure here may indicate:
- A **code regression** in a route handler (compare with unit tests first)
- A **missing environment variable** or secret (check `.env`)
- A **database connectivity** issue (check Atlas / local mongod)

Integration test results are **environment-dependent**.  Distinguish "service
unavailable" errors (connection refused, 503) from assertion failures before
treating them as regressions.

---

## 3 — External provider probes

```bash
cd backend && python3.12 -m pytest tests/external/ -m external -v
```

**Verified result: 17 tests collected.**

A failure here means one of:
- **BDL quota exhausted** for the day — not a code regression
- **BDL endpoint changed** — investigate and update the client
- **Network unavailable** in CI — skip for automated runs

### Reading probe output

| pytest outcome | What it means |
|---------------|--------------|
| `PASSED` | Provider returned live data; covariate logic verified end-to-end |
| `SKIPPED` | Provider returned empty data — quota exhausted or endpoint unreachable; **not a code bug** |
| `FAILED` | Assertion failed on returned data — likely an upstream data-shape change; investigate |
| `ERROR` | Import error or unexpected exception in test setup — likely a code regression |

Sections 3 and 4 (`TestDataGapFill`, `TestBayesianSpatialXgCovariate`) are
deterministic and do not make network requests; they should always pass.

---

## Release go/no-go rules

1. `python3.12 -m pytest` (unit tier) **must be 100% green**.
2. Integration tier failures require investigation; a "connection refused" error means the backend is not running, not a code regression.  An assertion failure in a route that unit tests also cover **is** a regression.
3. External tier failures are **informational only** — they document provider availability, not code correctness.
4. `tests/retired/` contains tests for removed features (basketball, Square payments) and obsolete `/app/backend` path checks.  They are never run and exist for historical reference only.
