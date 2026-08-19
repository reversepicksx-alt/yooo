import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from news_intelligence import (
    UNKNOWN,
    _fetch_article,
    analyze_news_evidence,
    unknown_news_intelligence,
)


NOW = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)


def _context():
    return {
        "fixture_id": 9001,
        "fixture_date": "2026-08-19T19:00:00+00:00",
        "player_id": 11,
        "player_name": "Player One",
        "team_id": 100,
        "team_name": "FC Alpha",
        "opponent_id": 200,
        "opponent_name": "Beta United",
        "league_id": 253,
        "league_name": "Major League Soccer",
    }


def _record(
    *,
    title,
    source_name,
    source_url,
    url,
    side="target",
    entity_id=100,
    entity_name="FC Alpha",
    content="",
    published_at="2026-08-19T12:00:00+00:00",
):
    return {
        "title": title,
        "snippet": title,
        "content": content,
        "url": url,
        "source_url": source_url,
        "source_name": source_name,
        "domain": source_url.replace("https://", "").strip("/"),
        "published_at": published_at,
        "retrieved_at": "2026-08-19T15:00:00+00:00",
        "entity_type": "club",
        "entity_id": entity_id,
        "entity_name": entity_name,
        "entity_side": side,
    }


def _confirmed_lineups(*, formation="3-5-2", target_starts=True):
    target_starters = [
        {"player": {"id": 11 if target_starts else 91, "name": "Player One" if target_starts else "Replacement One", "pos": "D", "grid": "2:2"}},
        *[
            {"player": {"id": 20 + index, "name": f"Confirmed Alpha {index}", "pos": "M", "grid": f"3:{index}"}}
            for index in range(1, 11)
        ],
    ]
    target_substitutes = []
    if not target_starts:
        target_substitutes.append({"player": {"id": 11, "name": "Player One", "pos": "D"}})
    opponent_starters = [
        {"player": {"id": 200 + index, "name": f"Beta Player {index}", "pos": "M", "grid": f"3:{index}"}}
        for index in range(1, 12)
    ]
    return {
        "response": [
            {
                "team": {"id": 100, "name": "FC Alpha"},
                "formation": formation,
                "startXI": target_starters,
                "substitutes": target_substitutes,
                "coach": {"name": "Alpha Coach"},
            },
            {
                "team": {"id": 200, "name": "Beta United"},
                "formation": "4-2-3-1",
                "startXI": opponent_starters,
                "substitutes": [],
                "coach": {"name": "Beta Coach"},
            },
        ]
    }


def test_unavailable_packet_uses_unknown_instead_of_guessing():
    packet = unknown_news_intelligence("Search timed out.")

    assert packet["status"] == "unavailable"
    assert packet["expected_lineup"] == UNKNOWN
    assert packet["target_start_probability"] == UNKNOWN
    assert packet["minutes_risk"] == UNKNOWN
    assert packet["expected_role"] == UNKNOWN
    assert packet["formation"] == UNKNOWN
    assert packet["projection_influence"] == "shadow_only"
    assert packet["math_unchanged"] is True


def test_official_current_source_wins_over_uncorroborated_aggregator():
    records = [
        _record(
            title="FC Alpha confirms Player One will miss the match with injury",
            source_name="FC Alpha",
            source_url="https://alpha.example",
            url="https://alpha.example/team-news",
            content="FC Alpha confirmed Player One will miss the match and is ruled out with injury.",
        ),
        _record(
            title="Predicted lineup: Player One expected to start for FC Alpha",
            source_name="Sports Mole",
            source_url="https://sportsmole.example",
            url="https://sportsmole.example/preview",
            content="Player One could be in the predicted lineup and is expected to start.",
        ),
    ]

    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=records,
        now=NOW,
    )

    assert packet["status"] == "available"
    assert packet["target_start_probability"] == 0.0
    contradiction = next(row for row in packet["contradictions"] if row["subject"] == "target_player")
    assert contradiction["winning_assertion"] == "unavailable"
    assert contradiction["winning_finding"]["source_tier"]["rank"] == 1
    assert any(warning["code"] == "CONTRADICTORY_NEWS" for warning in packet["news_warnings"])


def test_confirmed_lineup_drift_flags_prediction_without_mutating_it():
    predicted_xi = (
        "Expected XI: Player One, Alpha A, Alpha B, Alpha C, Alpha D, Alpha E, "
        "Alpha F, Alpha G, Alpha H, Alpha I, Alpha J"
    )
    records = [
        _record(
            title=predicted_xi,
            source_name="Alpha Gazette",
            source_url="https://alphagazette.example",
            url="https://alphagazette.example/expected-xi",
            content=f"{predicted_xi}. FC Alpha could use a 4-3-3 formation.",
        )
    ]
    prediction = {
        "recommendation": "UNDER",
        "projectedValue": 66.2,
        "pOver": 0.37,
        "pUnder": 0.63,
        "tacticalContext": {
            "lineupStatus": "predicted",
            "lineupFormation": "4-3-3",
        },
    }
    before = deepcopy(prediction)

    packet = analyze_news_evidence(
        context=_context(),
        prediction=prediction,
        records=records,
        lineups_payload=_confirmed_lineups(),
        now=NOW,
    )

    comparison = packet["confirmed_lineup_comparison"]
    assert comparison["material_difference"] is True
    assert comparison["rerun_required"] is True
    assert comparison["flag_prediction"] is True
    assert comparison["action"] == "FLAG_FOR_RERUN"
    assert packet["target_start_probability"] == 1.0
    assert packet["expected_lineup"]["status"] == "CONFIRMED"
    assert packet["formation"]["target_team"] == "3-5-2"
    assert comparison["pre_match_assumption"]["expected_formation"] == "4-3-3"
    assert comparison["pre_match_assumption"]["target_start_probability"] == UNKNOWN
    assert any(warning["code"] == "CONFIRMED_LINEUP_MATERIAL_DRIFT" for warning in packet["news_warnings"])
    assert prediction == before


def test_confirmed_bench_status_is_high_minutes_risk():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        lineups_payload=_confirmed_lineups(target_starts=False),
        now=NOW,
    )

    assert packet["target_start_probability"] == 0.0
    assert packet["minutes_risk"] == "HIGH"
    assert packet["confirmed_lineup_comparison"]["rerun_required"] is True


def test_confirmed_starter_flags_pre_match_bench_assumption():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={"starterStatus": "bench"},
        records=[],
        lineups_payload=_confirmed_lineups(target_starts=True, formation="4-3-3"),
        now=NOW,
    )

    comparison = packet["confirmed_lineup_comparison"]
    assert comparison["rerun_required"] is True
    assert comparison["pre_match_assumption"]["target_status"] == "bench"
    assert comparison["pre_match_assumption"]["target_start_probability"] == 0.18
    assert comparison["confirmed"]["target_status"] == "STARTER"
    assert any("pre-match assumption was bench" in reason for reason in comparison["reasons"])


def test_malformed_empty_lineup_rows_do_not_claim_confirmed_target_status():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        lineups_payload={
            "response": [
                {
                    "team": {"id": 100, "name": "FC Alpha"},
                    "formation": "4-3-3",
                    "startXI": [],
                    "substitutes": [],
                }
            ]
        },
        now=NOW,
    )

    comparison = packet["confirmed_lineup_comparison"]
    assert packet["target_start_probability"] == UNKNOWN
    assert comparison["status"] == "PENDING_CONFIRMED_LINEUPS"
    assert comparison["material_difference"] == UNKNOWN
    assert comparison["rerun_required"] is False


def test_partial_unrelated_starter_does_not_claim_target_absence():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        lineups_payload={
            "response": [
                {
                    "team": {"id": 100, "name": "FC Alpha"},
                    "formation": "4-3-3",
                    "startXI": [
                        {"player": {"id": 99, "name": "Only Returned Player", "pos": "M"}}
                    ],
                    "substitutes": [],
                }
            ]
        },
        now=NOW,
    )

    assert packet["target_start_probability"] == UNKNOWN
    assert packet["minutes_risk"] == UNKNOWN
    assert packet["confirmed_lineup_comparison"]["status"] == "PENDING_CONFIRMED_LINEUPS"


def test_complete_starters_without_substitutes_keep_missing_target_unknown():
    starters = [
        {"player": {"id": 300 + index, "name": f"Other Starter {index}", "pos": "M"}}
        for index in range(11)
    ]
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        lineups_payload={
            "response": [
                {
                    "team": {"id": 100, "name": "FC Alpha"},
                    "formation": "4-3-3",
                    "startXI": starters,
                    "substitutes": [],
                }
            ]
        },
        now=NOW,
    )

    assert packet["target_start_probability"] == UNKNOWN
    assert packet["minutes_risk"] == UNKNOWN
    comparison = packet["confirmed_lineup_comparison"]
    assert comparison["status"] == "PENDING_CONFIRMED_TARGET_STATUS"
    assert comparison["material_difference"] == UNKNOWN
    assert comparison["rerun_required"] is False


def test_incomplete_substitute_list_cannot_prove_target_out_of_squad():
    starters = [
        {"player": {"id": 400 + index, "name": f"Other Starter {index}", "pos": "M"}}
        for index in range(11)
    ]
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        lineups_payload={
            "response": [
                {
                    "team": {"id": 100, "name": "FC Alpha"},
                    "formation": "4-3-3",
                    "startXI": starters,
                    "substitutes": [
                        {"player": {"id": 999, "name": "Only Returned Substitute", "pos": "F"}}
                    ],
                }
            ]
        },
        now=NOW,
    )

    assert packet["target_start_probability"] == UNKNOWN
    assert packet["minutes_risk"] == UNKNOWN
    assert packet["confirmed_lineup_comparison"]["status"] == "PENDING_CONFIRMED_TARGET_STATUS"


def test_newer_same_tier_source_wins_conflict_before_confidence_tiebreak():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="Player One ruled out for FC Alpha with injury",
                source_name="Alpha Gazette",
                source_url="https://alpha-gazette.example",
                url="https://alpha-gazette.example/older",
                content="Player One is ruled out and will miss the match through injury.",
                published_at="2026-08-18T12:00:00+00:00",
            ),
            _record(
                title="Player One expected to start in FC Alpha predicted lineup",
                source_name="Alpha Herald",
                source_url="https://alpha-herald.example",
                url="https://alpha-herald.example/newer",
                content="Player One is expected to start in the predicted lineup.",
                published_at="2026-08-19T14:00:00+00:00",
            ),
        ],
        now=NOW,
    )

    contradiction = next(row for row in packet["contradictions"] if row["subject"] == "target_player")
    assert contradiction["winning_assertion"] == "starts"
    assert contradiction["winning_finding"]["source"]["name"] == "Alpha Herald"
    assert packet["target_start_probability"] == 0.82


def test_aggregator_quote_stays_low_tier_and_cannot_self_verify():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="Manager said Player One may start in predicted lineup",
                source_name="Sports Mole",
                source_url="https://sportsmole.example",
                url="https://sportsmole.example/manager-quote",
                content='The manager said, "Player One may start in the predicted lineup."',
            )
        ],
        now=NOW,
    )

    assert packet["findings"]
    assert all(finding["source_tier"]["rank"] == 6 for finding in packet["findings"])
    assert packet["target_start_probability"] == UNKNOWN


def test_irrelevant_team_search_result_is_not_promoted_to_evidence():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="Inter Miami confirms Lionel Messi returns to action",
                source_name="National Football News",
                source_url="https://national.example",
                url="https://national.example/inter-miami",
                content="Inter Miami confirmed Lionel Messi returns to action after injury.",
            )
        ],
        now=NOW,
    )

    assert packet["findings"] == []
    assert packet["status"] == "unavailable"


def test_stale_match_news_is_not_treated_as_current_evidence():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="FC Alpha expected lineup includes Player One",
                source_name="Alpha Gazette",
                source_url="https://alpha-gazette.example",
                url="https://alpha-gazette.example/old-lineup",
                content="FC Alpha expected lineup includes Player One after training.",
                published_at="2026-06-01T12:00:00+00:00",
            )
        ],
        now=NOW,
    )

    assert packet["findings"] == []
    assert packet["target_start_probability"] == UNKNOWN


def test_undated_lineup_article_is_source_discovery_only():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="FC Alpha expected lineup includes Player One",
                source_name="Alpha Gazette",
                source_url="https://alpha-gazette.example",
                url="https://alpha-gazette.example/undated-lineup",
                content="FC Alpha expected lineup includes Player One.",
                published_at=None,
            )
        ],
        now=NOW,
    )

    assert packet["findings"] == []
    assert packet["target_start_probability"] == UNKNOWN


def test_invalid_publication_timestamp_cannot_drive_availability():
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="FC Alpha says Player One is ruled out",
                source_name="FC Alpha",
                source_url="https://alpha.example",
                url="https://alpha.example/invalid-date",
                content="FC Alpha says Player One is ruled out with injury.",
                published_at="not-a-timestamp",
            )
        ],
        now=NOW,
    )

    assert packet["findings"] == []
    assert packet["target_start_probability"] == UNKNOWN


def test_private_article_url_is_rejected_before_client_use():
    assert asyncio.run(_fetch_article("http://127.0.0.1/internal")) == {}


def test_teammate_injury_and_every_finding_keep_provenance_fields():
    injuries = {
        "response": [
            {
                "team": {"id": 100, "name": "FC Alpha"},
                "player": {"id": 77, "name": "Key Teammate", "reason": "Hamstring injury"},
            }
        ]
    }
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[
            _record(
                title="FC Alpha coach said the team may switch formation after training",
                source_name="Alpha Chronicle",
                source_url="https://alphachronicle.example",
                url="https://alphachronicle.example/manager",
                content='The FC Alpha manager said, "We may switch formation after training."',
            )
        ],
        injuries_payload=injuries,
        now=NOW,
    )

    assert packet["important_teammate_changes"] != UNKNOWN
    assert any("Key Teammate" in row["statement"] for row in packet["important_teammate_changes"])
    assert packet["lineup_confidence"] != UNKNOWN
    for finding in packet["findings"]:
        assert finding["source"]["url"]
        assert finding["source"]["name"]
        assert finding["timestamp"]["retrieved_at"]
        if finding["source"]["domain"] == "api-football.com":
            assert finding["timestamp"]["published_at"] == UNKNOWN
            assert finding["timestamp"]["observed_at"] == finding["timestamp"]["retrieved_at"]
        assert finding["source_tier"]["rank"]
        assert finding["classification"] in {"fact", "analysis", "speculation"}
        assert set(finding["evidence_weights"]) == {"relevance", "freshness", "reliability"}
        assert 0 <= finding["confidence"] <= 1


def test_duplicate_teammate_injuries_do_not_inflate_findings_or_lineup_confidence():
    duplicate = {
        "team": {"id": 100, "name": "FC Alpha"},
        "player": {"id": 77, "name": "Key Teammate", "reason": "Hamstring injury"},
    }
    packet = analyze_news_evidence(
        context=_context(),
        prediction={},
        records=[],
        injuries_payload={"response": [duplicate, duplicate]},
        now=NOW,
    )

    injury_findings = [
        finding
        for finding in packet["findings"]
        if finding["topic"] == "injury"
    ]
    assert len(injury_findings) == 1
    assert packet["lineup_confidence"] == UNKNOWN