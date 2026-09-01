from routes.predict import compute_team_quality_gap


def _context(*, player_is_home=True, player_prob=None, team_rank=1, opp_rank=18, poss=65):
    odds = {
        "playerIsHome": player_is_home,
        "matchLeagueId": 253,
        "matchLeague": "MLS",
    }
    if player_prob is not None:
        # Build decimal odds that normalize to approximately the requested
        # probability, keeping the helper's fixture-home normalization under test.
        odds["bookmakerOdds"] = {
            "homeWin": round(1 / player_prob, 3) if player_is_home else 2.6,
            "awayWin": 2.6 if player_is_home else round(1 / player_prob, 3),
        }
    return {
        "match_odds": odds,
        "standing_data": {"teamRank": team_rank, "oppRank": opp_rank},
        "match_dominance": {
            "expectedPoss": poss,
            "hasRealPossData": True,
        },
    }


def test_strong_favorite_gets_bounded_quality_uplift():
    ctx = _context(player_prob=0.78)
    result = compute_team_quality_gap(
        **ctx, requested_league_id=253, prop_type="pass_attempts", position="CDM"
    )

    assert result["eligible"] is True
    assert result["applied"] is True
    assert result["direction"] == "up"
    assert 1.0 < result["multiplier"] <= 1.12
    assert result["competition"]["leagueId"] == 253


def test_fixture_away_odds_are_normalized_to_player_perspective():
    ctx = _context(player_is_home=False, player_prob=0.78)
    result = compute_team_quality_gap(
        **ctx, requested_league_id=253, prop_type="passes", position="CM"
    )

    assert result["applied"] is True
    assert result["direction"] == "up"
    market = next(s for s in result["signals"] if s["source"] == "market_implied_probability")
    assert market["playerTeamProbability"] > 0.5


def test_cdm_passes_are_eligible_but_goalkeeper_passes_are_not():
    ctx = _context(player_prob=0.78)
    cdm = compute_team_quality_gap(
        **ctx, requested_league_id=253, prop_type="pass_attempts", position="CDM"
    )
    gk = compute_team_quality_gap(
        **ctx, requested_league_id=253, prop_type="pass_attempts", position="GK"
    )

    assert cdm["eligible"] is True
    assert cdm["applied"] is True
    assert gk["eligible"] is True
    assert gk["applied"] is False
    assert gk["multiplier"] == 1.0


def test_possession_is_corroboration_only_and_not_a_second_multiplier():
    strong = _context(player_prob=0.78, poss=65)
    weak_poss = _context(player_prob=0.78, poss=52)
    strong_result = compute_team_quality_gap(
        **strong, requested_league_id=253, prop_type="passes", position="CDM"
    )
    weak_result = compute_team_quality_gap(
        **weak_poss, requested_league_id=253, prop_type="passes", position="CDM"
    )

    assert strong_result["multiplier"] == weak_result["multiplier"]
    assert all(
        signal.get("usedForNumericAdjustment") is not True
        for signal in strong_result["signals"]
        if signal["source"] == "verified_possession"
    )


def test_missing_quality_evidence_is_explicitly_not_applied():
    result = compute_team_quality_gap(
        match_odds={"matchLeagueId": 253, "matchLeague": "MLS"},
        standing_data={},
        match_dominance={"expectedPoss": 65, "hasRealPossData": True},
        requested_league_id=253,
        prop_type="pass_attempts",
        position="CDM",
    )

    assert result["applied"] is False
    assert result["multiplier"] == 1.0
    assert "Insufficient" in result["reason"]
