import asyncio
from copy import deepcopy

from routes import jarvis


def test_full_audit_route_attaches_news_to_audit_and_jarvis_brief(monkeypatch):
    rp_prediction = {
        "fixtureId": 123,
        "playerId": 456,
        "playerName": "Audit Player",
        "teamName": "Home FC",
        "opponentName": "Away FC",
        "leagueId": 253,
        "resolvedVenue": "home",
        "propType": "pass_attempts",
        "line": 57.5,
        "recommendation": "under",
        "projectedValue": 48.2,
        "pOver": 22.0,
        "pUnder": 78.0,
        "confidenceScore": 71,
        "bayesianMetrics": {},
        "evidenceQuality": {"score": 64},
    }
    immutable_values = {
        key: deepcopy(rp_prediction[key])
        for key in ("recommendation", "projectedValue", "pOver", "pUnder", "confidenceScore")
    }
    context = {
        "fixture_id": 123,
        "player_id": 456,
        "player_name": "Audit Player",
        "team_id": 1,
        "team_name": "Home FC",
        "opponent_id": 2,
        "opponent_name": "Away FC",
        "venue": "home",
        "league_id": 253,
        "season": 2026,
    }
    news_packet = {
        "status": "available",
        "source": "dynamic_news_research_and_confirmed_lineups",
        "projection_influence": "shadow_only",
        "math_unchanged": True,
        "expected_lineup": {"status": "CONFIRMED"},
        "target_start_probability": 0.0,
        "minutes_risk": "HIGH",
        "expected_role": "UNKNOWN",
        "formation": {"target_team": "4-2-3-1", "opponent": "4-3-3"},
        "important_teammate_changes": "UNKNOWN",
        "lineup_confidence": {"level": "HIGH", "score": 0.99},
        "regime_changes": "UNKNOWN",
        "news_warnings": [{"code": "CONFIRMED_LINEUP_MATERIAL_DRIFT"}],
        "news_brief": "Confirmed lineup requires review.",
        "confirmed_lineup_comparison": {
            "rerun_required": True,
            "flag_prediction": True,
        },
    }

    async def fake_predict(_body):
        return context, rp_prediction

    async def fake_first_goal(result, _context, _prop_type):
        result["firstGoalMarket"] = {"available": False, "reason": "not needed"}

    async def fake_news(result, _context, _fixture_id):
        result["newsIntelligence"] = news_packet

    monkeypatch.setattr(jarvis, "_require_auth", lambda _authorization: None)
    monkeypatch.setattr(jarvis, "audit_enabled", lambda: True)
    monkeypatch.setattr(jarvis, "_run_soccer_prediction", fake_predict)
    monkeypatch.setattr(jarvis, "_ensure_full_audit_first_goal_context", fake_first_goal)
    monkeypatch.setattr(jarvis, "_ensure_full_audit_news_context", fake_news)

    response = asyncio.run(
        jarvis.jarvis_full_audit_soccer(
            jarvis.JarvisSoccerPredictBody(
                fixture_id=123,
                player_id=456,
                prop_type="pass_attempts",
                line=57.5,
            ),
            authorization="Bearer test",
        )
    )

    assert response["math_unchanged"] is True
    assert response["production_influence"] is False
    assert response["audit"]["modules"]["news_intelligence"]["values"] == news_packet
    assert response["jarvis_brief"]["news_intelligence"] == news_packet
    assert response["jarvis_brief"]["news_brief"] == "Confirmed lineup requires review."
    assert response["jarvis_brief"]["lineup_rerun_required"] is True
    for key, value in immutable_values.items():
        assert rp_prediction[key] == value


def test_news_context_failure_is_unknown_safe(monkeypatch):
    result = {"recommendation": "under", "pOver": 30.0, "pUnder": 70.0}

    async def fake_sports_get_safe(*_args, **_kwargs):
        return None

    async def fail_research(**_kwargs):
        raise RuntimeError("search unavailable")

    monkeypatch.setattr(jarvis, "_sports_get_safe", fake_sports_get_safe)
    monkeypatch.setattr("news_intelligence.run_news_intelligence", fail_research)

    asyncio.run(
        jarvis._ensure_full_audit_news_context(
            result,
            {
                "fixture_id": 123,
                "player_id": 456,
                "player_name": "Audit Player",
                "team_id": 1,
                "team_name": "Home FC",
                "opponent_id": 2,
                "opponent_name": "Away FC",
                "league_id": 253,
            },
            123,
        )
    )

    packet = result["newsIntelligence"]
    assert packet["status"] == "unavailable"
    assert packet["target_start_probability"] == "UNKNOWN"
    assert packet["math_unchanged"] is True
    assert result["recommendation"] == "under"
    assert result["pOver"] == 30.0
    assert result["pUnder"] == 70.0