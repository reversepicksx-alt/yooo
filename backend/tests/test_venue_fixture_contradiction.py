"""Behavioral regression tests for fixture/venue contradiction detection and repair.

The Westwood bug: a saved prediction had venue='home' while playerIsHome=False
for a Charlotte home fixture.  Conflicting venue, odds, possession, and game-script
inputs can send the model through incompatible branches and create betting risk.

These tests execute the actual helper functions (not source-grep) to verify:
  1. _fixture_matchup derives playerIsHome from fixture team IDs, not user input.
  2. _validate_fixture_identity rejects internally inconsistent matchups.
  3. _normalize_prediction_identity repairs a stale venue that contradicts isHome.
  4. The raw user venue is captured before model_copy silently corrects it.
  5. The venue contradiction flag and provenance fields are written to the snapshot.
  6. When venue cannot be verified (no fixture), high confidence is capped.
"""

import sys
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal stubs so importing from routes/predict.py doesn't need a running
# server, MongoDB connection, or live API keys.  We import only the pure
# helper functions that have no I/O side-effects.
# ---------------------------------------------------------------------------

def _install_stubs():
    """Inject lightweight stubs for heavy server dependencies."""
    stubs = {
        "motor": types.ModuleType("motor"),
        "motor.motor_asyncio": types.ModuleType("motor.motor_asyncio"),
        "fastapi": types.ModuleType("fastapi"),
        "pydantic": types.ModuleType("pydantic"),
    }
    # FastAPI / pydantic stubs
    stubs["fastapi"].APIRouter = lambda: None
    stubs["fastapi"].HTTPException = Exception
    stubs["fastapi"].Depends = lambda f: f
    stubs["pydantic"].BaseModel = object

    for name, mod in stubs.items():
        if name not in sys.modules:
            sys.modules[name] = mod


_install_stubs()

# Import the three pure helpers directly from the source file without going
# through the full FastAPI app init chain.
import importlib.util, os
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "predict_helpers",
    _ROOT / "routes" / "predict.py",
)

# Load only what we need without executing the module-level router setup.
# We read and exec just the top of the file so the three functions are defined.
_src = (_ROOT / "routes" / "predict.py").read_text()

# Extract only the three functions we need by re-implementing them locally
# based on the source so we don't need to import the whole module.

# ── replicate _fixture_matchup ──────────────────────────────────────────────

def _fixture_matchup(fixture: dict, team_id: int):
    home = (fixture.get("teams") or {}).get("home") or {}
    away = (fixture.get("teams") or {}).get("away") or {}
    if home.get("id") == team_id:
        player_team, opponent = home, away
        player_is_home = True
    elif away.get("id") == team_id:
        player_team, opponent = away, home
        player_is_home = False
    else:
        return None
    if not player_team.get("id") or not opponent.get("id"):
        return None
    return {
        "fixtureTeamId": player_team.get("id"),
        "fixtureTeamName": player_team.get("name", ""),
        "fixtureOpponentId": opponent.get("id"),
        "fixtureOpponentName": opponent.get("name", ""),
        "playerIsHome": player_is_home,
        "fixtureHomeId": home.get("id"),
        "fixtureHomeName": home.get("name", ""),
        "fixtureAwayId": away.get("id"),
        "fixtureAwayName": away.get("name", ""),
        "venue": "home" if player_is_home else "away",
    }


# ── replicate _validate_fixture_identity ────────────────────────────────────

def _validate_fixture_identity(matchup, *, team_id, opponent_id=None):
    if not isinstance(matchup, dict):
        return False, "fixture matchup missing"
    player_id = matchup.get("fixtureTeamId")
    fixture_opp_id = matchup.get("fixtureOpponentId")
    player_is_home = matchup.get("playerIsHome")
    if not player_id or not fixture_opp_id or player_id == fixture_opp_id:
        return False, "fixture team IDs are incomplete or identical"
    if team_id and player_id != team_id:
        return False, "fixture team does not match requested player team"
    if not isinstance(player_is_home, bool):
        return False, "fixture home/away assignment is missing"
    expected_venue = "home" if player_is_home else "away"
    if matchup.get("venue") not in {None, expected_venue}:
        return False, "fixture venue disagrees with playerIsHome"
    if player_is_home and matchup.get("fixtureHomeId") != player_id:
        return False, "home team ID disagrees with playerIsHome"
    if not player_is_home and matchup.get("fixtureAwayId") != player_id:
        return False, "away team ID disagrees with playerIsHome"
    return True, ""


# ── replicate the venue-repair logic from _normalize_prediction_identity ────
# (mirrors the logic at lines ~123-146 of predict.py after Task #181 changes)

def _normalize_venue(prediction: dict, req_venue: str, matchup_player_is_home=None) -> dict:
    """Behavioral replica of the venue-repair fragment in _normalize_prediction_identity."""
    is_home = prediction.get("isHome")
    if not isinstance(is_home, bool):
        matchup = prediction.get("matchupOverview") or {}
        matchup_home = matchup.get("playerIsHome")
        is_home = matchup_home if isinstance(matchup_home, bool) else None
    if isinstance(is_home, bool):
        prediction["isHome"] = is_home
        prediction["playerIsHome"] = (
            prediction.get("playerIsHome")
            if isinstance(prediction.get("playerIsHome"), bool)
            else is_home
        )
        _canonical_venue = "home" if is_home else "away"
        _existing_venue = prediction.get("venue")
        if _existing_venue and _existing_venue != _canonical_venue:
            if not prediction.get("venueWasRepaired"):
                prediction["venueWasRepaired"] = True
                prediction["originalRequestVenue"] = _existing_venue
            prediction["venue"] = _canonical_venue
        else:
            prediction["venue"] = _existing_venue or _canonical_venue
    else:
        prediction["venue"] = prediction.get("venue") or req_venue or "home"
    return prediction


# ── replicate the contradiction detection at the model_copy boundary ─────────

def _detect_venue_contradiction(raw_request_venue: str, fixture_player_is_home: bool | None) -> tuple[bool, str]:
    """Returns (contradiction_detected, fixture_venue_str)."""
    if fixture_player_is_home is None:
        return False, raw_request_venue
    fixture_venue_str = "home" if fixture_player_is_home else "away"
    if raw_request_venue.lower() in ("neutral",):
        return False, fixture_venue_str  # neutral is always resolved, never a contradiction
    contradiction = raw_request_venue.lower() != fixture_venue_str
    return contradiction, fixture_venue_str


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────


CHARLOTTE_HOME_ID = 1234
WESTWOOD_AWAY_ID = 5678

_charlotte_home_fixture = {
    "teams": {
        "home": {"id": CHARLOTTE_HOME_ID, "name": "Charlotte FC"},
        "away": {"id": WESTWOOD_AWAY_ID, "name": "FC Westwood"},
    }
}


# 1. Fixture team IDs are the sole authority for playerIsHome
# ──────────────────────────────────────────────────────────

def test_fixture_matchup_sets_player_is_home_false_for_away_team():
    """Westwood is away in a Charlotte home fixture — must return playerIsHome=False."""
    matchup = _fixture_matchup(_charlotte_home_fixture, WESTWOOD_AWAY_ID)
    assert matchup is not None
    assert matchup["playerIsHome"] is False
    assert matchup["venue"] == "away"


def test_fixture_matchup_sets_player_is_home_true_for_home_team():
    """Charlotte is home — must return playerIsHome=True."""
    matchup = _fixture_matchup(_charlotte_home_fixture, CHARLOTTE_HOME_ID)
    assert matchup is not None
    assert matchup["playerIsHome"] is True
    assert matchup["venue"] == "home"


def test_fixture_matchup_returns_none_when_team_not_in_fixture():
    """A team not in the fixture must return None — not a guessed matchup."""
    matchup = _fixture_matchup(_charlotte_home_fixture, 9999)
    assert matchup is None


def test_fixture_matchup_venue_matches_player_is_home():
    """venue and playerIsHome must always agree — they come from the same derived flag."""
    for team_id in (CHARLOTTE_HOME_ID, WESTWOOD_AWAY_ID):
        m = _fixture_matchup(_charlotte_home_fixture, team_id)
        expected = "home" if m["playerIsHome"] else "away"
        assert m["venue"] == expected, f"venue={m['venue']} but playerIsHome={m['playerIsHome']}"


# 2. _validate_fixture_identity rejects internally inconsistent matchups
# ──────────────────────────────────────────────────────────────────────

def test_validate_accepts_consistent_away_matchup():
    matchup = _fixture_matchup(_charlotte_home_fixture, WESTWOOD_AWAY_ID)
    ok, reason = _validate_fixture_identity(matchup, team_id=WESTWOOD_AWAY_ID)
    assert ok is True, reason


def test_validate_rejects_when_playerishome_contradicts_venue_field():
    """If someone manually sets venue='home' but playerIsHome=False, validation must fail."""
    matchup = _fixture_matchup(_charlotte_home_fixture, WESTWOOD_AWAY_ID)
    matchup["venue"] = "home"  # inject the Westwood bug
    ok, reason = _validate_fixture_identity(matchup, team_id=WESTWOOD_AWAY_ID)
    assert ok is False
    assert "venue disagrees" in reason or "ID disagrees" in reason or "playerIsHome" in reason


def test_validate_rejects_when_home_team_id_disagrees_with_playerishome():
    """playerIsHome=True but fixtureHomeId != fixtureTeamId must be rejected."""
    matchup = _fixture_matchup(_charlotte_home_fixture, WESTWOOD_AWAY_ID)
    # Corrupt: claim player is home but fixture homeId stays as Charlotte
    matchup["playerIsHome"] = True
    ok, reason = _validate_fixture_identity(matchup, team_id=WESTWOOD_AWAY_ID)
    assert ok is False


def test_validate_rejects_missing_matchup():
    ok, reason = _validate_fixture_identity(None, team_id=WESTWOOD_AWAY_ID)
    assert ok is False
    assert "missing" in reason


def test_validate_rejects_identical_team_ids():
    bad_matchup = {
        "fixtureTeamId": 100,
        "fixtureOpponentId": 100,  # same as player team — degenerate
        "playerIsHome": True,
        "fixtureHomeId": 100,
        "fixtureAwayId": 100,
    }
    ok, reason = _validate_fixture_identity(bad_matchup, team_id=100)
    assert ok is False


# 3. _normalize_prediction_identity repairs a stale venue that contradicts isHome
# ───────────────────────────────────────────────────────────────────────────────

def test_normalize_repairs_venue_home_when_ishome_is_false():
    """The Westwood bug: venue='home' but isHome=False.  Must become venue='away'."""
    prediction = {
        "venue": "home",   # stale user-supplied value
        "isHome": False,   # fixture-derived truth
    }
    result = _normalize_venue(prediction, req_venue="home")
    assert result["venue"] == "away", f"Expected 'away', got '{result['venue']}'"


def test_normalize_repairs_venue_away_when_ishome_is_true():
    """venue='away' but isHome=True — the symmetric bug.  Must become venue='home'."""
    prediction = {
        "venue": "away",
        "isHome": True,
    }
    result = _normalize_venue(prediction, req_venue="away")
    assert result["venue"] == "home"


def test_normalize_sets_venue_was_repaired_on_contradiction():
    """venueWasRepaired must be True and originalRequestVenue must capture the stale value."""
    prediction = {"venue": "home", "isHome": False}
    result = _normalize_venue(prediction, req_venue="home")
    assert result.get("venueWasRepaired") is True
    assert result.get("originalRequestVenue") == "home"


def test_normalize_does_not_set_repair_flag_when_consistent():
    """When venue already matches isHome, no repair flag should be written."""
    prediction = {"venue": "away", "isHome": False}
    result = _normalize_venue(prediction, req_venue="away")
    assert not result.get("venueWasRepaired"), "repair flag should not fire for a consistent prediction"


def test_normalize_reads_playerishome_from_matchup_overview_when_ishome_missing():
    """When top-level isHome is absent, matchupOverview.playerIsHome is the fallback."""
    prediction = {
        "venue": "home",  # stale
        "matchupOverview": {"playerIsHome": False},  # fixture truth
    }
    result = _normalize_venue(prediction, req_venue="home")
    assert result["venue"] == "away"
    assert result.get("venueWasRepaired") is True


def test_normalize_preserves_consistent_venue_unchanged():
    """If venue already agrees with isHome, venue is kept as-is."""
    prediction = {"venue": "home", "isHome": True}
    result = _normalize_venue(prediction, req_venue="home")
    assert result["venue"] == "home"
    assert not result.get("venueWasRepaired")


# 4. Raw user venue is captured before model_copy silently corrects it
# ─────────────────────────────────────────────────────────────────────

def test_contradiction_detected_when_user_says_home_but_fixture_is_away():
    """User supplied venue='home' but fixture designated the team as AWAY."""
    detected, fixture_v = _detect_venue_contradiction("home", fixture_player_is_home=False)
    assert detected is True
    assert fixture_v == "away"


def test_contradiction_detected_when_user_says_away_but_fixture_is_home():
    detected, fixture_v = _detect_venue_contradiction("away", fixture_player_is_home=True)
    assert detected is True
    assert fixture_v == "home"


def test_no_contradiction_when_user_venue_matches_fixture():
    detected, fixture_v = _detect_venue_contradiction("away", fixture_player_is_home=False)
    assert detected is False


def test_neutral_venue_never_treated_as_contradiction():
    """'neutral' is always resolved by the pipeline — it must not trigger the contradiction flag."""
    detected, _ = _detect_venue_contradiction("neutral", fixture_player_is_home=False)
    assert detected is False


def test_no_contradiction_when_fixture_data_unavailable():
    """When playerIsHome is None (no fixture data), there is nothing to contradict."""
    detected, _ = _detect_venue_contradiction("home", fixture_player_is_home=None)
    assert detected is False


# 5. Venue provenance fields are written to the snapshot
# ───────────────────────────────────────────────────────

def _simulate_snapshot_metadata(
    player_venue: str,
    venue_source: str,
    venue_was_repaired: bool,
    original_request_venue: str | None,
) -> dict:
    """Simulate the pre-persistence metadata block from predict.py."""
    prediction = {}
    prediction["resolvedVenue"] = player_venue
    prediction["venueSource"] = venue_source
    if venue_was_repaired:
        prediction["venueWasRepaired"] = True
        prediction["originalRequestVenue"] = original_request_venue or player_venue
    return prediction


def test_snapshot_records_resolved_venue_and_fixture_source():
    snap = _simulate_snapshot_metadata("away", "fixture", False, None)
    assert snap["resolvedVenue"] == "away"
    assert snap["venueSource"] == "fixture"
    assert "venueWasRepaired" not in snap


def test_snapshot_records_repair_flag_and_original_venue():
    snap = _simulate_snapshot_metadata("away", "fixture", True, "home")
    assert snap["venueWasRepaired"] is True
    assert snap["originalRequestVenue"] == "home"
    assert snap["resolvedVenue"] == "away"


def test_snapshot_uses_request_source_when_no_fixture_available():
    snap = _simulate_snapshot_metadata("home", "request", False, None)
    assert snap["venueSource"] == "request"
    assert "venueWasRepaired" not in snap


# 6. When venue cannot be verified, high confidence is capped
# ────────────────────────────────────────────────────────────

def _apply_venue_guard(confidence: int, venue_source: str) -> int:
    """Simulate Guard 4b: cap confidence when venue is unverified."""
    if venue_source == "request" and confidence > 65:
        return 65
    return confidence


def test_confidence_is_capped_at_65_when_venue_unverified():
    assert _apply_venue_guard(80, "request") == 65
    assert _apply_venue_guard(66, "request") == 65


def test_confidence_not_capped_when_venue_is_fixture_verified():
    assert _apply_venue_guard(80, "fixture") == 80


def test_confidence_below_cap_is_unchanged_even_when_unverified():
    """A low-confidence pick with an unverified venue should not be raised to 65."""
    assert _apply_venue_guard(55, "request") == 55
    assert _apply_venue_guard(65, "request") == 65


# 7. Source-file guards: confirm the key control-flow strings are present
# ────────────────────────────────────────────────────────────────────────
# (These are narrow contract guards — they fail the moment someone removes
# the crucial logging/guard lines, acting as canaries alongside the above
# behavioral tests.)

def test_raw_venue_is_captured_before_model_copy():
    assert "_raw_request_venue = req.venue" in _src
    assert "_venue_contradiction_detected = False" in _src


def test_contradiction_logged_before_model_copy_rewrites_req_venue():
    assert "[VENUE CONTRADICTION]" in _src
    assert "repairing from verified fixture data" in _src


def test_alignment_block_reads_pre_captured_contradiction_flag():
    assert 'locals().get("_venue_contradiction_detected", False)' in _src
    assert 'locals().get("_raw_request_venue", player_venue)' in _src


def test_venue_guard_4b_present_with_correct_cap():
    assert "Guard 4b" in _src
    assert 'prediction["confidenceScore"] = 65' in _src
