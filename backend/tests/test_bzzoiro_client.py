from bzzoiro_client import compute_press_proxy, _find_event, validate_position_data, COVERAGE_CONSTRAINTS


def test_press_proxy_uses_bzzoiro_defensive_actions_and_marks_single_fixture():
    result = compute_press_proxy(
        {
            "total_tackles": 14,
            "interceptions": 9,
            "passes": 530,
            "ball_possession": 47,
        }
    )

    assert result["label"] == "Moderate"
    assert result["defensiveActions"] == 23
    assert result["passesPerDefensiveAction"] == 23.0
    assert result["sampleSize"] == 1
    assert result["evidenceStatus"] == "single_fixture_shadow"
    assert result["projectionAdjustmentStatus"] == "shadow_only"


def test_press_proxy_treats_missing_values_as_unavailable_not_zero():
    assert compute_press_proxy({}) is None
    assert compute_press_proxy({"total_tackles": None, "interceptions": None}) is None


def test_event_matching_requires_verified_opponent_and_prefers_exact_date():
    rows = [
        {
            "id": 1,
            "home_team_id": 293,
            "home_team": "Inter Miami CF",
            "away_team_id": 2282,
            "away_team": "CF Monterrey",
            "event_date": "2026-08-09T00:00:00+00:00",
        },
        {
            "id": 2,
            "home_team_id": 293,
            "home_team": "Inter Miami CF",
            "away_team_id": 2282,
            "away_team": "CF Monterrey",
            "event_date": "2026-08-08T00:00:00+00:00",
        },
    ]

    result, date_exact = _find_event(
        rows,
        team_id=293,
        team_name="Inter Miami",
        opponent_id=2282,
        opponent_name="Monterrey",
        match_date="2026-08-09T00:00:00Z",
    )

    assert result["id"] == 1
    assert date_exact is True  # exact same-day match must be flagged


def test_event_matching_returns_date_exact_false_for_nearest_date_fallback():
    """When no same-day event exists, date_exact must be False to prevent
    position data from a different fixture reaching tactical intelligence."""
    rows = [
        {
            "id": 99,
            "home_team_id": 293,
            "home_team": "Inter Miami CF",
            "away_team_id": 2282,
            "away_team": "CF Monterrey",
            "event_date": "2026-08-07T00:00:00+00:00",  # 2 days before requested
        },
    ]

    result, date_exact = _find_event(
        rows,
        team_id=293,
        team_name="Inter Miami",
        opponent_id=2282,
        opponent_name="Monterrey",
        match_date="2026-08-09T00:00:00Z",
    )

    assert result["id"] == 99
    assert date_exact is False  # nearest-date fallback must NOT be marked exact


def test_event_matching_can_bridge_provider_ids_by_verified_names():
    result, date_exact = _find_event(
        [
            {
                "id": 5170,
                "home_team_id": 293,
                "home_team": "Inter Miami CF",
                "away_team_id": 295,
                "away_team": "Columbus Crew",
                "event_date": "2026-08-02T00:10:00+00:00",
            }
        ],
        team_id=9568,  # API-Football ID, intentionally not Bzzoiro's 293
        team_name="Inter Miami",
        opponent_id=161,  # API-Football ID, intentionally not Bzzoiro's 295
        opponent_name="Columbus Crew",
        match_date="2026-08-02T00:00:00Z",
    )

    assert result["id"] == 5170
    assert date_exact is True


# ── validate_position_data tests ──────────────────────────────────────────────


def _make_enrichment(
    *,
    coverage: str = "exact_date_and_opponent",
    target_lineup: dict | None = None,
    avg_pos: dict | None = None,
    match_stats: dict | None = None,
    match_method: str = "exact_name",
) -> dict:
    """Build a minimal enrichment packet for validation tests.

    ``match_method`` is stored as ``_matchMethod`` inside the lineup target so
    that ``validate_position_data()`` can apply the identity gate.  Defaults to
    ``"exact_name"`` (reliable) so most tests exercise the happy path.
    """
    lineup_target = (
        {**target_lineup, "_matchMethod": match_method}
        if isinstance(target_lineup, dict)
        else target_lineup
    )
    return {
        "available": True,
        "status": "covered",
        "fixture": {"coverage": coverage},
        "lineup": {"target": lineup_target},
        "target": {
            "averagePosition": avg_pos,
            "matchStats": match_stats,
        },
    }


def test_validate_position_data_passes_when_all_signals_valid():
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Messi", "position": "RW"},
        avg_pos={"x": 78.3, "y": 42.1},
    )
    result = validate_position_data(enrichment)

    assert result["valid"] is True
    assert result["lineupValid"] is True
    assert result["coordinatesValid"] is True
    assert result["fixtureDateMatch"] == "exact"
    assert result["playerIdentityConfidence"] == "high"
    assert result["usableAsPositionSupplement"] is True
    assert result["issues"] == []


def test_validate_position_data_fails_when_enrichment_unavailable():
    result = validate_position_data({"available": False, "status": "unavailable"})
    assert result["valid"] is False
    assert result["usableAsPositionSupplement"] is False
    assert result["playerIdentityConfidence"] == "none"


def test_validate_position_data_fails_when_enrichment_is_none():
    result = validate_position_data({})
    assert result["valid"] is False
    assert result["usableAsPositionSupplement"] is False


def test_validate_position_data_rejects_coordinates_out_of_pitch_range():
    enrichment = _make_enrichment(
        target_lineup={"name": "Test Player", "position": "ST"},
        avg_pos={"x": 150.0, "y": 50.0},  # x out of 0-100 range
    )
    result = validate_position_data(enrichment)

    assert result["coordinatesValid"] is False
    assert result["usableAsPositionSupplement"] is False
    # Lineup is present and date is exact, so usable for lineup-only, but not
    # as a position supplement since coordinates are bad.
    assert result["valid"] is True  # lineup gate passes
    assert any("out of expected pitch range" in issue for issue in result["issues"])


def test_validate_position_data_fails_when_player_not_in_lineup():
    enrichment = _make_enrichment(
        target_lineup=None,
        avg_pos={"x": 50.0, "y": 50.0},
    )
    result = validate_position_data(enrichment)

    assert result["lineupValid"] is False
    assert result["valid"] is False
    assert result["usableAsPositionSupplement"] is False
    assert any("not found in Bzzoiro lineup" in issue for issue in result["issues"])


def test_validate_position_data_fails_when_date_match_is_fuzzy():
    enrichment = _make_enrichment(
        coverage="approximate_date",  # not exact
        target_lineup={"name": "Player", "position": "CM"},
        avg_pos={"x": 50.0, "y": 50.0},
    )
    result = validate_position_data(enrichment)

    assert result["fixtureDateMatch"] == "fuzzy"
    assert result["valid"] is False  # exact date required
    assert result["usableAsPositionSupplement"] is False


def test_validate_position_data_reports_missing_coordinates():
    enrichment = _make_enrichment(
        target_lineup={"name": "Player", "position": "CB"},
        avg_pos=None,  # no average position returned
    )
    result = validate_position_data(enrichment)

    assert result["coordinatesValid"] is False
    assert result["usableAsPositionSupplement"] is False
    assert result["valid"] is True  # lineup gate passes, just no coordinates
    assert any("average-position" in issue.lower() for issue in result["issues"])


def test_validate_position_data_identity_confidence_medium_when_stats_only():
    """Player found via match stats but not in lineup — medium confidence."""
    enrichment = _make_enrichment(
        target_lineup=None,
        avg_pos=None,
        match_stats={"goals": 1, "passes": 42},
    )
    result = validate_position_data(enrichment)

    assert result["playerIdentityConfidence"] == "medium"
    assert result["valid"] is False  # lineup required for valid gate


def test_validate_position_data_rejects_substring_name_match():
    """Substring name matches must be rejected as ambiguous identity; 'Luis' could
    match multiple players and cannot reliably anchor a position label."""
    enrichment = _make_enrichment(
        target_lineup={"id": 55, "name": "Luis Garcia", "position": "CM"},
        avg_pos={"x": 50.0, "y": 50.0},
        match_method="substring_name",  # permissive — must not pass gate
    )
    result = validate_position_data(enrichment)

    assert result["lineupValid"] is False
    assert result["valid"] is False
    assert result["usableAsPositionSupplement"] is False
    assert result["matchMethod"] == "substring_name"
    # Identity confidence is at most medium because the match is ambiguous.
    assert result["playerIdentityConfidence"] in {"medium", "low"}
    assert any("ambiguous" in issue for issue in result["issues"])


def test_validate_position_data_rejects_numeric_id_match_method():
    """'numeric_id' must NOT be accepted as a reliable identity anchor.

    In the production fetch path, the API-Football player_id was compared
    against Bzzoiro's different numeric ID namespace, making it an accidental
    collision risk.  The production code now uses exact normalized name matching
    only for the lineup anchor, so 'numeric_id' is never produced — and even if
    someone constructs such a packet manually, the gate must reject it.
    """
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Test Player", "position": "ST"},
        avg_pos={"x": 85.0, "y": 50.0},
        match_method="numeric_id",  # cross-provider collision risk — must be rejected
    )
    result = validate_position_data(enrichment)

    assert result["lineupValid"] is False  # numeric_id not in RELIABLE_MATCH_METHODS
    assert result["valid"] is False
    assert result["usableAsPositionSupplement"] is False


def test_validate_position_data_rejects_avgpos_from_different_player():
    """Average-position coordinates from a different player must be rejected even
    when the lineup match passes — the ownership cross-check must catch the mismatch."""
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Lionel Messi", "position": "RW"},
        avg_pos={"x": 75.0, "y": 40.0, "name": "Luis Suárez"},  # different player's coords
        match_method="exact_name",
    )
    result = validate_position_data(enrichment)

    # Lineup gate passes (exact_name), but coordinate ownership fails.
    assert result["lineupValid"] is True
    assert result["coordinatesValid"] is False
    assert result["usableAsPositionSupplement"] is False
    assert any("different player" in issue for issue in result["issues"])


def test_validate_position_data_accepts_matching_avgpos_owner():
    """Coordinates are valid when avg_pos player name matches the lineup player."""
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Lionel Messi", "position": "RW"},
        avg_pos={"x": 75.0, "y": 40.0, "name": "Lionel Messi"},
        match_method="exact_name",
    )
    result = validate_position_data(enrichment)

    assert result["lineupValid"] is True
    assert result["coordinatesValid"] is True
    assert result["usableAsPositionSupplement"] is True


def test_coverage_constraints_document_known_limits():
    """COVERAGE_CONSTRAINTS must record auth, rate limits, commercial use, and competition coverage."""
    assert "authentication" in COVERAGE_CONSTRAINTS
    assert COVERAGE_CONSTRAINTS["authentication"]["required"] is True

    assert "rateLimits" in COVERAGE_CONSTRAINTS
    # Rate limits are not yet documented from the provider.
    assert COVERAGE_CONSTRAINTS["rateLimits"]["documented"] is False

    assert "commercialUse" in COVERAGE_CONSTRAINTS
    # Commercial use terms must not be claimed as verified until confirmed.
    assert COVERAGE_CONSTRAINTS["commercialUse"]["verified"] is False

    assert "competitionCoverage" in COVERAGE_CONSTRAINTS
    confirmed = COVERAGE_CONSTRAINTS["competitionCoverage"]["confirmed"]
    assert "MLS" in confirmed
    assert "Liga MX" in confirmed

    assert "positionData" in COVERAGE_CONSTRAINTS
    assert COVERAGE_CONSTRAINTS["positionData"]["shadowOnly"] is True


# ── build_tactical_intelligence integration tests ─────────────────────────────


def _build_ti(bzzoiro_enrichment=None, player_position="", prediction=None):
    """Helper: call build_tactical_intelligence with minimal required inputs."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from tactical_intelligence import build_tactical_intelligence

    return build_tactical_intelligence(
        prediction=prediction or {
            "isHome": True,
            "playerName": "Test Player",
            "player": {"id": 99, "name": "Test Player"},
        },
        prop_type="passes",
        player_position=player_position,
        bzzoiro_enrichment=bzzoiro_enrichment,
    )


def test_ti_uses_bzzoiro_position_when_api_football_position_missing():
    """When API-Football has no position, validated Bzzoiro lineup position is used."""
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Test Player", "position": "CM"},
        avg_pos={"x": 55.0, "y": 30.0},
    )
    enrichment["positionValidation"] = validate_position_data(enrichment)

    packet = _build_ti(bzzoiro_enrichment=enrichment, player_position="")

    player = packet["player"]
    # Bzzoiro-normalized "CM" should be in the packet.
    assert player["position"] == "CM"
    assert player["positionSource"] == "bzzoiro_shadow_confirmed_lineup"
    # Grid coordinates from Bzzoiro must be forwarded with labeled provenance.
    grid = player["providerGridPosition"]
    assert grid["x"] == 55.0
    assert grid["y"] == 30.0
    assert grid["source"] == "bzzoiro_shadow"
    # A limitation note must warn that position came from the shadow source.
    assert any("bzzoiro shadow" in lim.lower() for lim in packet.get("limitations", []))


def test_ti_prefers_api_football_position_label_over_bzzoiro():
    """API-Football position label always wins; validated Bzzoiro grid coordinates
    may still supplement when the API-Football lineup has no x/y data."""
    enrichment = _make_enrichment(
        target_lineup={"id": 100, "name": "Test Player", "position": "CAM"},
        avg_pos={"x": 70.0, "y": 45.0},
    )
    enrichment["positionValidation"] = validate_position_data(enrichment)

    packet = _build_ti(bzzoiro_enrichment=enrichment, player_position="RW")

    player = packet["player"]
    # API-Football "RW" must win as the position label and source.
    assert player["position"] == "RW"
    assert player["positionSource"] == "lineup-provider"
    # Grid source: API-Football lineup has no x/y in the stub, so validated
    # Bzzoiro coordinates are used for the grid — this is the correct behavior.
    # The grid source is either bzzoiro_shadow (Bzzoiro coordinates applied) or
    # unavailable (no coordinates at all), never api_football (no x/y in stub).
    grid = player["providerGridPosition"]
    assert grid["source"] in {"bzzoiro_shadow", "unavailable"}
    # Regardless, the position label itself did not come from Bzzoiro.
    assert player["positionSource"] == "lineup-provider"


def test_ti_rejects_bzzoiro_position_when_date_match_is_fuzzy():
    """Fuzzy date match must not allow Bzzoiro to supply a position."""
    enrichment = _make_enrichment(
        coverage="approximate_date",
        target_lineup={"name": "Test Player", "position": "ST"},
        avg_pos={"x": 80.0, "y": 50.0},
    )
    enrichment["positionValidation"] = validate_position_data(enrichment)

    packet = _build_ti(bzzoiro_enrichment=enrichment, player_position="")

    player = packet["player"]
    # Gate failed — position must stay empty, not be populated from Bzzoiro.
    assert not player.get("position")
    assert player.get("positionSource") != "bzzoiro_shadow_confirmed_lineup"


def test_ti_rejects_bzzoiro_position_when_player_not_in_lineup():
    """Player absent from Bzzoiro lineup must not supply a position."""
    enrichment = _make_enrichment(
        target_lineup=None,  # player not found
        avg_pos={"x": 60.0, "y": 40.0},
    )
    enrichment["positionValidation"] = validate_position_data(enrichment)

    packet = _build_ti(bzzoiro_enrichment=enrichment, player_position="")

    player = packet["player"]
    assert not player.get("position")
    assert player.get("positionSource") != "bzzoiro_shadow_confirmed_lineup"


def test_ti_rejects_bzzoiro_coordinates_when_out_of_pitch_range():
    """Out-of-range grid coordinates must not be forwarded even if the lineup passes."""
    enrichment = _make_enrichment(
        target_lineup={"name": "Test Player", "position": "LW"},
        avg_pos={"x": 150.0, "y": 50.0},  # x out of range
    )
    enrichment["positionValidation"] = validate_position_data(enrichment)

    packet = _build_ti(bzzoiro_enrichment=enrichment, player_position="")

    grid = packet["player"]["providerGridPosition"]
    # Bzzoiro position normalizes through (lineup valid + exact date), but
    # coordinates must NOT be forwarded because coordinatesValid=False.
    assert grid["source"] != "bzzoiro_shadow"
    assert grid.get("x") is None or grid["source"] == "unavailable"