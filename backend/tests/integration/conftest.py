"""
Integration-tier conftest.

Sets INTEGRATION_BASE_URL for all integration tests.  Tests should use the
INTEGRATION_BASE_URL fixture (or read the BASE_URL module-level variable) to
target the running backend.  Defaults to http://127.0.0.1:8000 so the
documented release command always hits the local server unless overridden.

Override via environment variable:
    BACKEND_URL=https://staging.example.com pytest tests/integration/
"""
import os
import pytest


# Module-level default: always target the local backend unless explicitly
# overridden.  BACKEND_URL takes precedence over any pre-existing
# REACT_APP_BACKEND_URL (e.g. a leftover env var from a previous session).
_DEFAULT = "http://127.0.0.1:8000"

# Precedence (highest to lowest):
#   1. BACKEND_URL  — explicit caller override (e.g. staging URL)
#   2. _DEFAULT     — unconditional local fallback
# We intentionally ignore any pre-existing REACT_APP_BACKEND_URL so that a
# stale env var from a previous session cannot silently redirect the local
# release run to a remote service.
_resolved = os.environ.get("BACKEND_URL") or _DEFAULT
os.environ["REACT_APP_BACKEND_URL"] = _resolved


@pytest.fixture(scope="session")
def integration_base_url():
    """Return the backend URL used by this integration run."""
    return os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
