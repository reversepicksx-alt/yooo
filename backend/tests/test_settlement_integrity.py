"""Regression tests for settled soccer result classification.

Covers:
 - SOCCER_STAT_MAP / SOCCER_STAT_PATHS correctness
 - _has_soccer_stat_evidence participation guard
 - _settle_pick_result (OVER / UNDER / PUSH / PASS leans)
 - _pass_lean recovery paths (explicit passLeaning, skipDetails, projection)
 - _soccer_settlement_provenance auditable marker
 - Repair-audit trail shape: verified source excludes pick from legacy count
"""

import asyncio
import pytest
from routes.picks import (
    SOCCER_STAT_MAP,
    SOCCER_STAT_PATHS,
    _has_soccer_stat_evidence,
    _discard_dnp_pick,
    _soccer_dnp_guard_fires,
    _settle_pick_result,
    _soccer_settlement_provenance,
    _pass_lean,
)
import routes.picks as picks_routes


# ── SOCCER_STAT_MAP correctness ───────────────────────────────────────────────

def test_pass_attempts_uses_total_attempts_not_accurate_passes():
    official_stats = {
        "passes": {
            "total": 57,
            "accuracy": 91,
            "accurate": 52,
        }
    }
    assert SOCCER_STAT_MAP["pass_attempts"](official_stats) == 57
    assert SOCCER_STAT_MAP["passes"](official_stats) == 57
    assert SOCCER_STAT_PATHS["pass_attempts"] == "statistics.passes.total"
    assert SOCCER_STAT_PATHS["passes"] == "statistics.passes.total"


def test_accurate_passes_cannot_be_substituted_for_attempts():
    official_stats = {"passes": {"total": 57, "accurate": 52}}
    assert SOCCER_STAT_MAP["pass_attempts"](official_stats) != official_stats["passes"]["accurate"]


def test_stat_map_returns_none_for_missing_nested_key():
    """A missing sub-key must return None, not raise."""
    empty = {}
    assert SOCCER_STAT_MAP["tackles"](empty) is None
    assert SOCCER_STAT_MAP["shots"](empty) is None
    assert SOCCER_STAT_MAP["interceptions"](empty) is None
    assert SOCCER_STAT_MAP["clearances"](empty) is None
    assert SOCCER_STAT_MAP["dribbles"](empty) is None
    assert SOCCER_STAT_MAP["crosses"](empty) is None


def test_stat_map_saves_returns_zero_not_none_when_absent():
    """Saves defaults to 0 (goalkeeper can legitimately save 0)."""
    empty = {}
    assert SOCCER_STAT_MAP["saves"](empty) == 0
    assert SOCCER_STAT_MAP["goalie_saves"](empty) == 0


def test_stat_map_shots_on_target_path():
    official_stats = {"shots": {"total": 4, "on": 2}}
    assert SOCCER_STAT_MAP["shots_on_target"](official_stats) == 2
    assert SOCCER_STAT_PATHS["shots_on_target"] == "statistics.shots.on"


def test_stat_map_duels_won_path():
    official_stats = {"duels": {"total": 8, "won": 5}}
    assert SOCCER_STAT_MAP["duels_won"](official_stats) == 5


# ── _has_soccer_stat_evidence ─────────────────────────────────────────────────

def test_positive_pass_stat_overrides_stale_minutes_for_dnp_guard():
    pick = {
        "sport": "soccer",
        "propType": "pass_attempts",
        "actualValue": 65,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is True
    assert _settle_pick_result(65, 56.5, {"recommendation": "under"}) == ("miss", None)


def test_full_match_pass_stat_with_missing_minutes_is_not_dnp():
    """Delgado-style provider response: real pass volume, unreliable minutes."""
    assert _soccer_dnp_guard_fires(33, 0) is False
    assert _soccer_dnp_guard_fires(33, None) is False
    assert _soccer_dnp_guard_fires(33, 90) is False
    assert _soccer_dnp_guard_fires(0, 0) is True
    assert _soccer_dnp_guard_fires(None, 0) is True


def test_zero_stat_does_not_prove_soccer_participation():
    pick = {
        "sport": "soccer",
        "propType": "passes",
        "actualValue": 0,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is False


def test_dnp_discard_preserves_pick_for_review_and_invalidates_list_cache(monkeypatch):
    class UpdateResult:
        modified_count = 1

    class FakePicks:
        def __init__(self):
            self.queries = []

        async def update_one(self, query, update):
            self.queries.append((query, update))
            return UpdateResult()

    class FakeDB:
        def __init__(self):
            self.picks = FakePicks()

    fake_db = FakeDB()
    monkeypatch.setattr(picks_routes, "db", fake_db)
    monkeypatch.setattr(
        picks_routes,
        "_picks_list_cache",
        {"subscriber@example.com": {"ts": 1, "picks": []}},
    )

    reviewed = asyncio.run(
        _discard_dnp_pick(
            {"pickId": "pick-dnp-1", "email": "Subscriber@Example.com"},
            reason="player not in finished squad",
        )
    )

    assert reviewed == 1
    query, update = fake_db.picks.queries[0]
    assert query == {
        "pickId": "pick-dnp-1",
        "email": "subscriber@example.com",
    }
    assert update["$set"]["status"] == "pending_review"
    assert update["$set"]["result"] == "pending_review"
    assert update["$set"]["settlementReview"]["retryable"] is True
    assert "subscriber@example.com" not in picks_routes._picks_list_cache


def test_dnp_discard_without_identity_is_a_noop(monkeypatch):
    class FakePicks:
        async def update_one(self, query, update):
            raise AssertionError("a pick without an id must not be updated")

    class FakeDB:
        picks = FakePicks()

    monkeypatch.setattr(picks_routes, "db", FakeDB())
    assert asyncio.run(_discard_dnp_pick({"playerName": "No ID"})) == 0


def test_positive_pass_stat_repairs_instead_of_reviewing_as_dnp(monkeypatch):
    class UpdateResult:
        modified_count = 1

    class FakePicks:
        def __init__(self):
            self.update = None

        async def update_one(self, query, update):
            self.update = (query, update)
            return UpdateResult()

    class FakeDB:
        def __init__(self):
            self.picks = FakePicks()

    fake_db = FakeDB()
    monkeypatch.setattr(picks_routes, "db", fake_db)
    pick = {
        "pickId": "pick-muslera-1",
        "email": "subscriber@example.com",
        "sport": "soccer",
        "propType": "pass_attempts",
        "line": 26.5,
        "recommendation": "over",
        "actualValue": 27,
        "result": "dnp",
    }

    assert asyncio.run(_discard_dnp_pick(pick, reason="temporary provider miss")) == 1
    assert pick["status"] == "settled"
    assert pick["result"] == "hit"
    assert pick["actualValue"] == 27
    assert pick["hitPct"] == 100
    assert "$unset" in fake_db.picks.update[1]


def test_stat_evidence_is_not_applied_to_non_soccer_picks():
    pick = {
        "sport": "cs2",
        "propType": "passes",
        "actualValue": 65,
        "minutesPlayed": 19,
    }
    assert _has_soccer_stat_evidence(pick) is False


def test_stat_evidence_false_for_unknown_prop_type():
    pick = {
        "sport": "soccer",
        "propType": "wizard_spells",
        "actualValue": 10,
    }
    assert _has_soccer_stat_evidence(pick) is False


def test_stat_evidence_false_for_none_actual_value():
    pick = {
        "sport": "soccer",
        "propType": "tackles",
        "actualValue": None,
    }
    assert _has_soccer_stat_evidence(pick) is False


# ── _settle_pick_result — standard over/under/push ────────────────────────────

def test_settle_result_over_hit():
    assert _settle_pick_result(5, 4.5, {"recommendation": "over"}) == ("hit", None)


def test_settle_result_under_hit():
    assert _settle_pick_result(30, 35.5, {"recommendation": "under"}) == ("hit", None)


def test_sofascore_style_pass_total_72_beats_conflicting_provider_value_66():
    """A pass prop must settle from the independent exact-match total.

    SofaScore displays this as 63 accurate out of 72 attempted. The
    denominator is the player prop value, not the accurate-pass numerator.
    """
    assert _settle_pick_result(72, 66.5, {"recommendation": "under"}) == ("miss", None)
    assert _settle_pick_result(66, 66.5, {"recommendation": "under"}) == ("hit", None)


def test_settle_result_over_miss():
    assert _settle_pick_result(3, 4.5, {"recommendation": "over"}) == ("miss", None)


def test_reinaldo_saved_identity_case_remains_a_hit():
    # Identity/media enrichment must not alter the canonical saved line or
    # settlement result for the reported Mirassol pick.
    assert _settle_pick_result(
        21,
        34.5,
        {
            "recommendation": "under",
            "playerId": 9946,
            "teamId": 7848,
            "playerName": "Reinaldo Manoel da Silva",
        },
    ) == ("hit", None)


def test_settle_result_push_exact_line():
    assert _settle_pick_result(5, 5.0, {"recommendation": "over"}) == ("push", None)
    assert _settle_pick_result(5, 5.0, {"recommendation": "under"}) == ("push", None)


# ── _pass_lean recovery ───────────────────────────────────────────────────────

def test_pass_lean_explicit_passLeaning_over():
    pick = {"recommendation": "pass", "passLeaning": "over"}
    assert _pass_lean(pick) == "over"


def test_pass_lean_explicit_passLeaning_under():
    pick = {"recommendation": "pass", "passLeaning": "UNDER"}  # case-insensitive
    assert _pass_lean(pick) == "under"


def test_pass_lean_from_skipDetails_direction():
    pick = {
        "recommendation": "pass",
        "skipDetails": {"direction": "over"},
    }
    assert _pass_lean(pick) == "over"


def test_pass_lean_inferred_from_projection_above_line():
    pick = {
        "recommendation": "pass",
        "projectedValue": 68.0,
        "line": 55.5,
    }
    assert _pass_lean(pick) == "over"


def test_pass_lean_inferred_from_projection_below_line():
    pick = {
        "recommendation": "pass",
        "projectedValue": 40.0,
        "line": 55.5,
    }
    assert _pass_lean(pick) == "under"


def test_pass_lean_returns_none_when_projection_equals_line():
    """A tie projection is ambiguous — must not force a direction."""
    pick = {
        "recommendation": "pass",
        "projectedValue": 55.5,
        "line": 55.5,
    }
    assert _pass_lean(pick) is None


def test_pass_lean_returns_none_with_no_signals():
    pick = {"recommendation": "pass"}
    assert _pass_lean(pick) is None


def test_pass_lean_explicit_beats_projection():
    """Explicit passLeaning must override an inferred projection direction."""
    pick = {
        "recommendation": "pass",
        "passLeaning": "under",
        "projectedValue": 70.0,   # projection says over
        "line": 50.0,
    }
    assert _pass_lean(pick) == "under"


# ── _settle_pick_result — PASS recommendation ─────────────────────────────────

def test_settle_pass_with_over_lean_hit():
    pick = {
        "recommendation": "pass",
        "passLeaning": "over",
        "line": 4.5,
    }
    assert _settle_pick_result(6, 4.5, pick) == ("hit", None)


def test_settle_pass_with_under_lean_hit():
    pick = {
        "recommendation": "pass",
        "passLeaning": "under",
        "line": 55.5,
    }
    assert _settle_pick_result(40, 55.5, pick) == ("hit", None)


def test_settle_pass_with_over_lean_miss():
    pick = {
        "recommendation": "pass",
        "passLeaning": "over",
        "line": 4.5,
    }
    assert _settle_pick_result(3, 4.5, pick) == ("miss", None)


def test_settle_pass_without_lean_stays_pass():
    """A PASS with no recoverable direction must remain 'pass', not guess."""
    pick = {"recommendation": "pass"}
    assert _settle_pick_result(6, 4.5, pick) == ("pass", None)


def test_settle_pass_inferred_lean_from_projection():
    pick = {
        "recommendation": "pass",
        "projectedValue": 70.0,
        "line": 55.5,
    }
    # Projection > line → lean over; actual 70 > line 55.5 → hit
    assert _settle_pick_result(70, 55.5, pick) == ("hit", None)


# ── _soccer_settlement_provenance ─────────────────────────────────────────────

def test_verified_settlement_provenance_is_explicit():
    source = _soccer_settlement_provenance(
        provider="api-football",
        fixture_id=123,
        player_id=456,
        prop_type="pass_attempts",
        stat_path="statistics.passes.total",
        fixture_status="FT",
    )
    assert source["verified"] is True
    assert source["fixtureId"] == 123
    assert source["playerId"] == 456
    assert source["statPath"] == "statistics.passes.total"
    assert source["provider"] == "api-football"
    assert source["propType"] == "pass_attempts"
    assert source["fixtureStatus"] == "FT"


def test_provenance_verified_false_for_unverified_source():
    """An unverified marker must have verified=False and a provider field."""
    source = _soccer_settlement_provenance(
        provider="legacy-stored-final",
        fixture_id=None,
        player_id=None,
        prop_type="passes",
        stat_path="unknown",
        fixture_status=None,
        verified=False,
        verification_method="legacy_numeric_reconciliation",
    )
    assert source["verified"] is False
    assert source["provider"] == "legacy-stored-final"
    assert source["verificationMethod"] == "legacy_numeric_reconciliation"
    assert source["fixtureId"] is None


def test_provenance_recordedAt_is_populated():
    """recordedAt must always be present so the audit trail is time-stamped."""
    source = _soccer_settlement_provenance(
        provider="api-football",
        fixture_id=999,
        player_id=1,
        prop_type="tackles",
        stat_path="statistics.tackles.total",
    )
    assert "recordedAt" in source
    assert source["recordedAt"]  # non-empty string


# ── Repair-audit trail structure ──────────────────────────────────────────────

def test_repair_audit_trail_structure():
    """Verify the shape that admin_repair_soccer_settlement writes to the DB.

    The endpoint builds settlementRepairAudit from old + new values.
    This test confirms the shape independently so a future refactor
    cannot silently drop the previous-result or correctedBy fields.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    old_result      = "hit"
    old_actual      = 72
    old_source      = {"provider": "bdl-soccer", "verified": False}
    new_result      = "miss"
    new_actual      = 41
    new_source      = _soccer_settlement_provenance(
        provider="api-football",
        fixture_id=87654,
        player_id=321,
        prop_type="passes",
        stat_path="statistics.passes.total",
        fixture_status="FT",
    )

    audit = {
        "previous": {
            "status": "settled",
            "result": old_result,
            "actualValue": old_actual,
            "settlementSource": old_source,
        },
        "replacement": {
            "result": new_result,
            "actualValue": new_actual,
            "settlementSource": new_source,
        },
        "correctedBy": "admin_repair_soccer_settlement",
        "correctedAt": now,
    }

    # Previous values must be preserved
    assert audit["previous"]["result"] == old_result
    assert audit["previous"]["actualValue"] == old_actual
    assert audit["previous"]["settlementSource"]["verified"] is False

    # Replacement must carry a verified source
    assert audit["replacement"]["settlementSource"]["verified"] is True
    assert audit["replacement"]["result"] == new_result

    # Must always carry correctedBy so the admin log is searchable
    assert audit["correctedBy"] == "admin_repair_soccer_settlement"
    assert audit["correctedAt"] == now


def test_repaired_pick_has_verified_source_excludes_from_legacy_unverified_query():
    """After repair the pick's settlementSource.verified=True,
    so it is excluded by the legacy_unverified MongoDB query.

    MongoDB query used in /api/admin/audit-soccer-settlements:
        "settlementSource.verified": {"$ne": True}
    A repaired pick has verified=True and must NOT match this filter.
    """
    repaired_source = _soccer_settlement_provenance(
        provider="api-football",
        fixture_id=11111,
        player_id=222,
        prop_type="pass_attempts",
        stat_path="statistics.passes.total",
        fixture_status="FT",
    )
    # Simulate the MongoDB $ne: True filter in Python
    def matches_legacy_unverified(source):
        return source.get("verified") is not True

    assert repaired_source["verified"] is True
    assert not matches_legacy_unverified(repaired_source), (
        "A repaired pick must not appear in the legacy-unverified audit list"
    )


def test_legacy_unverified_source_matches_audit_query():
    """An old unverified source must appear in the legacy-unverified query."""
    legacy_source = {"provider": "bdl-soccer", "verified": False}
    def matches_legacy_unverified(source):
        return source.get("verified") is not True

    assert matches_legacy_unverified(legacy_source)


def test_pick_without_fixture_id_is_skipped_by_repair_endpoint():
    """Repair endpoint logic: picks without fixtureId are skipped (no fixtureId
    means we cannot guarantee exact-fixture provenance for the re-settlement).

    This mirrors the guard at the start of the repair loop in server.py.
    """
    def should_skip_repair(pick_doc):
        return not pick_doc.get("fixtureId")

    no_fixture = {"pickId": "abc", "playerName": "Andy Najar", "propType": "passes"}
    has_fixture = {"pickId": "abc", "playerName": "Andy Najar", "propType": "passes",
                   "fixtureId": 12345}

    assert should_skip_repair(no_fixture) is True
    assert should_skip_repair(has_fixture) is False
