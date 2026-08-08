"""
Root conftest — registers pytest markers and applies them automatically based
on the subdirectory a test lives in, so callers never need to decorate
individual test files.

Marker rules:
  tests/integration/*  → @pytest.mark.integration (live HTTP to a running backend)
  tests/external/*     → @pytest.mark.external    (live requests to third-party providers)
"""
import os
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: live HTTP requests to a running backend server",
    )
    config.addinivalue_line(
        "markers",
        "external: live requests to external data providers (API-Football, BDL, etc.)",
    )
    # pytest-timeout marker — registered to suppress PytestUnknownMarkWarning
    # even when the plugin itself is not installed.
    config.addinivalue_line(
        "markers",
        "timeout: per-test timeout in seconds (requires pytest-timeout plugin to enforce)",
    )


def pytest_collection_modifyitems(items):
    """Automatically stamp markers based on the test file's directory."""
    for item in items:
        path = str(item.fspath)
        # Normalise to forward-slash segments for reliable matching
        parts = path.replace(os.sep, "/").split("/")
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        elif "external" in parts:
            item.add_marker(pytest.mark.external)
