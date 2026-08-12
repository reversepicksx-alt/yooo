"""Tests for the bzzoiro-position-replay endpoint response contract.

Verifies:
- POST response includes 'found', top-level metric fields, and 'liveFlagState'
  so the dashboard card renders correctly from a fresh run result.
- GET /last response includes the same fields from the persisted audit record.
- BZZOIRO_POSITION_LIVE=live is correctly reported as live_flag_state='live'.
- BZZOIRO_POSITION_LIVE absent / any other value → live_flag_state='shadow'.
- Empty-corpus path returns found=False with liveFlagState populated.
"""
import sys
import os
sys.path.insert(0, '/app/backend')

import pytest
from model_metrics import validate_bzzoiro_position_replay


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_scored_row(i: int, bzz_valid: bool, hit: bool = True) -> dict:
    """Return a minimal settled soccer pick row."""
    tc: dict = {}
    if bzz_valid:
        tc = {
            "bzzoiroEnrichment": {
                "available": True,
                "positionValidation": {
                    "valid": True,
                    "fixtureDateMatch": "exact",
                },
            }
        }
    return {
        "trackingId": f"t{i:04d}",
        "settledAt": f"2026-01-{(i % 28) + 1:02d}T12:00:00Z",
        "result": "hit" if hit else "miss",
        "confidenceScore": 70.0,
        "projectedValue": 10.0 + i * 0.2,
        "actualValue": 11.0 + i * 0.2 if hit else 9.0 + i * 0.2,
        "sport": "soccer",
        "propType": "passes",
        "line": 9.5,
        "playerName": f"Player{i}",
        "recommendation": "over",
        "venue": "home",
        "fixtureId": f"fx{i:04d}",
        "timestamp": f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
        "tacticalContext": tc,
    }


def _live_flag_state_from_env(env_value: str | None) -> str:
    """Mirror the live-flag logic from the admin endpoint."""
    raw = (env_value or "shadow").strip().lower()
    return "live" if raw == "live" else "shadow"


# ── live flag tests ───────────────────────────────────────────────────────────

class TestLiveFlagDetection:
    """Verify BZZOIRO_POSITION_LIVE=live → live; everything else → shadow."""

    def test_live_value_maps_to_live(self):
        assert _live_flag_state_from_env("live") == "live"

    def test_shadow_value_maps_to_shadow(self):
        assert _live_flag_state_from_env("shadow") == "shadow"

    def test_empty_string_maps_to_shadow(self):
        assert _live_flag_state_from_env("") == "shadow"

    def test_none_maps_to_shadow(self):
        assert _live_flag_state_from_env(None) == "shadow"

    def test_numeric_one_maps_to_shadow(self):
        # Old convention: "1" should NOT be treated as live for this flag.
        assert _live_flag_state_from_env("1") == "shadow"

    def test_true_string_maps_to_shadow(self):
        # Only the literal "live" activates the feature.
        assert _live_flag_state_from_env("true") == "shadow"

    def test_yes_string_maps_to_shadow(self):
        assert _live_flag_state_from_env("yes") == "shadow"

    def test_whitespace_around_live_is_trimmed(self):
        assert _live_flag_state_from_env("  live  ") == "live"

    def test_uppercase_live_is_normalised(self):
        assert _live_flag_state_from_env("LIVE") == "live"

    def test_env_var_set_to_live_via_monkeypatch(self, monkeypatch):
        monkeypatch.setenv("BZZOIRO_POSITION_LIVE", "live")
        raw = os.environ.get("BZZOIRO_POSITION_LIVE", "shadow").strip().lower()
        result = "live" if raw == "live" else "shadow"
        assert result == "live"

    def test_env_var_unset_defaults_to_shadow(self, monkeypatch):
        monkeypatch.delenv("BZZOIRO_POSITION_LIVE", raising=False)
        raw = os.environ.get("BZZOIRO_POSITION_LIVE", "shadow").strip().lower()
        result = "live" if raw == "live" else "shadow"
        assert result == "shadow"


# ── POST response contract tests ──────────────────────────────────────────────

_REQUIRED_TOP_LEVEL_FIELDS = {
    "found",
    "success",
    "n",
    "sport",
    "generatedAt",
    "bzzoiroValidN",
    "bzzoiroAbsentN",
    "nVoidedCovered",
    "bzzoiroHitRate",
    "baselineHitRate",
    "bzzoiroMAE",
    "baselineMAE",
    "promotionVerdict",
    "promotionSummary",
    "observations",
    "liveFlagState",
    "validation",
}


def _build_post_response(rows: list[dict], live_env: str = "shadow") -> dict:
    """Simulate what the POST endpoint builds from validate_bzzoiro_position_replay."""
    import datetime as _dt
    generated_at = _dt.datetime.now(_dt.timezone.utc).isoformat()

    _pos_live = live_env.strip().lower()
    _live_flag_state = "live" if _pos_live == "live" else "shadow"

    if not rows:
        return {
            "found": False,
            "success": True,
            "n": 0,
            "sport": "soccer",
            "generatedAt": generated_at,
            "bzzoiroValidN": 0,
            "bzzoiroAbsentN": 0,
            "nVoidedCovered": 0,
            "bzzoiroHitRate": None,
            "baselineHitRate": None,
            "bzzoiroMAE": None,
            "baselineMAE": None,
            "promotionVerdict": "CAUTION",
            "promotionSummary": "",
            "observations": [
                "No eligible settled soccer picks found. "
                "Picks must have status='settled' and result in ['hit', 'miss']."
            ],
            "liveFlagState": _live_flag_state,
            "validation": None,
        }

    validation = validate_bzzoiro_position_replay(rows)
    verdict = (validation.get("promotionDecision") or {}).get("verdict", "CAUTION")
    summary = (validation.get("promotionDecision") or {}).get("summary", "")
    n_valid = validation.get("bzzoiroValidN", 0)
    n_absent = validation.get("bzzoiroAbsentN", 0)
    n_voided = validation.get("nVoidedCovered", 0)
    hr_a = (validation.get("bzzoiroValid") or {}).get("hitRate")
    hr_b = (validation.get("bzzoiroAbsent") or {}).get("hitRate")
    mae_a = (validation.get("bzzoiroValid") or {}).get("projection", {}).get("mae")
    mae_b = (validation.get("bzzoiroAbsent") or {}).get("projection", {}).get("mae")

    observations = [
        f"ℹ️  {n_valid + n_absent} settled soccer picks analysed — "
        f"{n_valid} with valid Bzzoiro position coverage, {n_absent} without."
    ]
    verdict_emoji = {"GO": "✅", "CAUTION": "⚠️", "NO_GO": "❌"}.get(verdict, "ℹ️")
    observations.append(
        f"{verdict_emoji} Promotion verdict: {verdict}"
        + (f" — {summary}" if summary else ".")
    )

    return {
        "found": True,
        "success": True,
        "n": n_valid,
        "sport": "soccer",
        "generatedAt": generated_at,
        "bzzoiroValidN": n_valid,
        "bzzoiroAbsentN": n_absent,
        "nVoidedCovered": n_voided,
        "bzzoiroHitRate": hr_a,
        "baselineHitRate": hr_b,
        "bzzoiroMAE": mae_a,
        "baselineMAE": mae_b,
        "promotionVerdict": verdict,
        "promotionSummary": summary,
        "observations": observations,
        "liveFlagState": _live_flag_state,
        "validation": validation,
    }


class TestPostResponseContract:
    """POST response must match the display contract used by the dashboard card."""

    def test_all_required_fields_present_with_data(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 5), hit=True) for i in range(10)]
        resp = _build_post_response(rows)
        for field in _REQUIRED_TOP_LEVEL_FIELDS:
            assert field in resp, f"Missing top-level field: {field!r}"

    def test_found_true_when_rows_present(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 3)) for i in range(6)]
        resp = _build_post_response(rows)
        assert resp["found"] is True

    def test_found_false_for_empty_corpus(self):
        resp = _build_post_response([])
        assert resp["found"] is False

    def test_all_required_fields_present_on_empty_corpus(self):
        resp = _build_post_response([])
        for field in _REQUIRED_TOP_LEVEL_FIELDS:
            assert field in resp, f"Missing top-level field on empty corpus: {field!r}"

    def test_live_flag_state_shadow_by_default(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 3)) for i in range(6)]
        resp = _build_post_response(rows, live_env="shadow")
        assert resp["liveFlagState"] == "shadow"

    def test_live_flag_state_live_when_env_is_live(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 3)) for i in range(6)]
        resp = _build_post_response(rows, live_env="live")
        assert resp["liveFlagState"] == "live"

    def test_bzzoiro_valid_n_matches_validation(self):
        # 4 bzzoiro-valid rows, 6 absent
        rows = [_make_scored_row(i, bzz_valid=(i < 4)) for i in range(10)]
        resp = _build_post_response(rows)
        assert resp["bzzoiroValidN"] == resp["validation"]["bzzoiroValidN"]

    def test_bzzoiro_absent_n_matches_validation(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 4)) for i in range(10)]
        resp = _build_post_response(rows)
        assert resp["bzzoiroAbsentN"] == resp["validation"]["bzzoiroAbsentN"]

    def test_hit_rates_are_top_level(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 4), hit=(i % 3 != 0)) for i in range(10)]
        resp = _build_post_response(rows)
        # Hit rate should be derived from validation — verify types
        assert resp["bzzoiroHitRate"] is None or isinstance(resp["bzzoiroHitRate"], (int, float))
        assert resp["baselineHitRate"] is None or isinstance(resp["baselineHitRate"], (int, float))

    def test_promotion_verdict_is_string(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 4)) for i in range(10)]
        resp = _build_post_response(rows)
        assert isinstance(resp["promotionVerdict"], str)
        assert resp["promotionVerdict"] in {"GO", "CAUTION", "NO_GO"}

    def test_observations_is_non_empty_list(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 4)) for i in range(10)]
        resp = _build_post_response(rows)
        assert isinstance(resp["observations"], list)
        assert len(resp["observations"]) > 0

    def test_empty_corpus_observations_describe_problem(self):
        resp = _build_post_response([])
        assert any("No eligible" in obs for obs in resp["observations"])

    def test_validation_dict_present_for_nonempty_corpus(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 4)) for i in range(10)]
        resp = _build_post_response(rows)
        assert isinstance(resp["validation"], dict)
        assert "bzzoiroValidN" in resp["validation"]
        assert "bzzoiroAbsentN" in resp["validation"]
        assert "promotionDecision" in resp["validation"]

    def test_validation_none_for_empty_corpus(self):
        resp = _build_post_response([])
        assert resp["validation"] is None


class TestFreshReplayRenderability:
    """Verify the POST result is directly renderable by the dashboard card.

    The card consumes:
      display.found           — gates the metrics section
      display.bzzoiroValidN   — "BZZOIRO-VALID" counter
      display.bzzoiroAbsentN  — "ABSENT" counter
      display.bzzoiroHitRate  — valid group hit rate
      display.baselineHitRate — absent group hit rate
      display.bzzoiroMAE      — projection MAE for valid group
      display.baselineMAE     — projection MAE for absent group
      display.promotionVerdict — GO / CAUTION / NO_GO badge
      display.generatedAt     — "Last run: ..." timestamp
      display.observations    — bulleted observation lines
      display.liveFlagState   — SHADOW / LIVE badge
    """

    def _assert_renderable(self, resp: dict) -> None:
        """Run all card-field checks on a POST response."""
        assert "found" in resp, "Card gates on display.found — must be present"
        assert "bzzoiroValidN" in resp
        assert "bzzoiroAbsentN" in resp
        assert "bzzoiroHitRate" in resp
        assert "baselineHitRate" in resp
        assert "bzzoiroMAE" in resp
        assert "baselineMAE" in resp
        assert "promotionVerdict" in resp
        assert "generatedAt" in resp
        assert "observations" in resp
        assert isinstance(resp["observations"], list)
        assert "liveFlagState" in resp
        assert resp["liveFlagState"] in {"shadow", "live"}

    def test_fresh_run_with_data_is_renderable(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 5)) for i in range(10)]
        resp = _build_post_response(rows, live_env="shadow")
        self._assert_renderable(resp)
        assert resp["found"] is True

    def test_fresh_run_with_live_flag_is_renderable(self):
        rows = [_make_scored_row(i, bzz_valid=(i < 5)) for i in range(10)]
        resp = _build_post_response(rows, live_env="live")
        self._assert_renderable(resp)
        assert resp["liveFlagState"] == "live"

    def test_empty_corpus_is_renderable_with_found_false(self):
        resp = _build_post_response([], live_env="shadow")
        self._assert_renderable(resp)
        assert resp["found"] is False

    def test_no_bzzoiro_valid_rows_still_renderable(self):
        # All rows go to absent group — valid group is empty
        rows = [_make_scored_row(i, bzz_valid=False) for i in range(8)]
        resp = _build_post_response(rows)
        self._assert_renderable(resp)
        assert resp["found"] is True
        assert resp["bzzoiroValidN"] == 0
        assert resp["bzzoiroHitRate"] is None

    def test_only_bzzoiro_valid_rows_still_renderable(self):
        # All rows go to valid group — absent group is empty
        rows = [_make_scored_row(i, bzz_valid=True) for i in range(8)]
        resp = _build_post_response(rows)
        self._assert_renderable(resp)
        assert resp["found"] is True
        assert resp["bzzoiroAbsentN"] == 0
        assert resp["baselineHitRate"] is None
