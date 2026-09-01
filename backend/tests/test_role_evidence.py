from role_evidence import build_role_evidence_packet


def test_verified_fixture_role_packet_answers_role_specific_questions():
    packet = build_role_evidence_packet(
        position="CDM",
        role="Anchor",
        source="fixture_lineup_observation",
        confidence="high",
        lineup_status="confirmed",
        fixture_id=123,
        venue="away",
        role_stats={"passes_total": 420, "tackles_total": 31},
        player_logs=[{"minutes": 90, "passes_total": 52}] * 6,
        comparable_players=[
            {"role": "Anchor", "venue": "away"},
            {"role": "Box-to-Box", "venue": "home"},
        ],
        prop_type="passes",
    )
    assert packet["status"] == "verified"
    assert packet["evidenceCounts"]["exactRole"] == 1
    assert packet["sameRoleEvidence"]["sampleSize"] == 1
    assert packet["sameVenueEvidence"]["sampleSize"] == 1
    assert packet["projectionInfluence"] == "shadow_only"
    assert "first phase" in " ".join(packet["questions"]).lower()


def test_generic_lineup_category_is_partial_not_same_role_evidence():
    packet = build_role_evidence_packet(
        position="MID",
        role="",
        source="fixture_lineup_category",
        confidence="low",
        lineup_status="confirmed",
        fixture_id=123,
        venue="home",
        player_logs=[],
        comparable_players=[],
        prop_type="passes",
    )
    assert packet["status"] == "partial"
    assert packet["evidenceCounts"]["exactRole"] == 0
    assert packet["sameRoleEvidence"]["status"] == "unavailable"