"""Focused tests for public-source tactical enrichment and final invariants."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge_base import (
    _fbref_find_row,
    _fbref_ppda_label,
    _fbref_pressure_label_from_rank,
    _parse_pct,
    _parse_fbref_tables,
)
from routes.predict import (
    _recompute_landing_bands,
    _reconcile_deterministic_confidence,
)
from tactical_intelligence import build_tactical_explanation


def test_fbref_parser_reads_visible_and_commented_tables():
    html = """
    <table id="pressing"><tbody>
      <tr><th data-stat="team">Rayo Vallecano</th>
          <td data-stat="pressures">1234</td>
          <td data-stat="ppda">8.7</td></tr>
    </tbody></table>
    <!-- <table id="standard"><tbody>
      <tr><th data-stat="player"><a>Florian Lejeune</a></th>
          <td data-stat="squad">Rayo Vallecano</td>
          <td data-stat="touches_def_3rd">455</td>
          <td data-stat="touches_mid_3rd">320</td>
          <td data-stat="touches_att_3rd">25</td>
      </tr>
    </tbody></table> -->
    """
    tables = _parse_fbref_tables(html)
    rows = [row for table in tables for row in table["rows"]]

    team = _fbref_find_row(rows, "Rayo Vallecano")
    player = _fbref_find_row(rows, "Florian Lejeune", "Rayo Vallecano")
    assert team is not None
    assert team["ppda"] == "8.7"
    assert player is not None
    assert player["touches_def_3rd"] == "455"


def test_markdown_transport_fallback_maps_only_known_schema_fields():
    markdown = """
    | Player | Squad | Touches Def 3rd | Progressive Carries |
    |---|---|---:|---:|
    | Florian Lejeune | Rayo Vallecano | 455 | 12 |
    """
    tables = _parse_fbref_tables(markdown)
    row = tables[0]["rows"][0]
    assert row == {
        "player": "Florian Lejeune",
        "squad": "Rayo Vallecano",
        "touches_def_3rd": "455",
        "carries_progressive": "12",
    }


def test_existing_percentage_parser_remains_intact():
    assert _parse_pct("48.3%") == 48.3
    assert _parse_pct(None) is None


def test_deterministic_tactical_explanation_uses_role_cohort_and_venue_h2h():
    explanation = build_tactical_explanation({
        "playerName": "Florian Lejeune",
        "teamName": "Rayo Vallecano",
        "opponentName": "Sevilla",
        "venue": "away",
        "propType": "pass_attempts",
        "position": "CB",
        "role": "Ball-Playing CB",
        "line": 64.5,
        "projectedValue": 52,
        "recommendation": "UNDER",
        "pOver": 11.6,
        "pUnder": 88.4,
        "seasonAverage": 55.97,
        "venueAverage": 53.82,
        "recentAverage": 53.82,
        "expectedPossession": 48.3,
        "opponentExpectedPossession": 51.7,
        "teamPassAverage": 415.2,
        "positionCohort": {
            "positionShort": "CB",
            "avgStatValue": 41.3,
            "sampleSize": 15,
            "venue": "away",
        },
        "h2h": {
            "sampleSize": 8,
            "venueSplits": {
                "away": {
                    "sampleSize": 4,
                    "average": 48.8,
                    "overPct": 25,
                    "underPct": 75,
                },
            },
        },
    })
    assert "Ball-Playing CB" in explanation
    assert "same-role opponent cohort" in explanation
    assert "15 comparable centre-back (CB) players" in explanation
    assert "matching away fixtures" in explanation
    assert "4 verified away appearances" in explanation
    assert "48.8 pass attempts" in explanation
    assert "52 against 64.5" in explanation


def test_fbref_ppda_thresholds_are_explicit_and_fallback_is_not_ppda():
    assert _fbref_ppda_label(8.0) == "high_press"
    assert _fbref_ppda_label(10.5) == "mid_block"
    assert _fbref_ppda_label(12.0) == "low_block"
    assert _fbref_ppda_label(None) is None

    rows = [{"pressures": str(value)} for value in (10, 20, 30, 40, 50, 60)]
    fallback = _fbref_pressure_label_from_rank(rows, {"pressures": "60"})
    assert fallback == "high_press"


def test_final_confidence_reconciliation_preserves_probability_statements():
    text = (
        "Confidence: 87% (Very High)\n"
        "TL;DR — 87% confidence (Very High)\n"
        "P(UNDER): 86.4%"
    )
    result = _reconcile_deterministic_confidence(text, 68, "Medium")
    assert "Confidence: 68% (Medium)" in result
    assert "68% (Medium) confidence" in result
    assert "P(UNDER): 86.4%" in result
    assert "87% (Very High)" not in result


def test_landing_bands_share_final_line_and_sum_to_one_hundred():
    source = [
        {"label": "≤50", "lower": None, "upper": 50.0, "probability": 20.0},
        {"label": "51–64", "lower": 50.0, "upper": 64.5, "probability": 30.0},
        {"label": "66+", "lower": 64.5, "upper": None, "probability": 50.0},
    ]
    result = _recompute_landing_bands(source, 53.0, 64.5, 10.45)
    assert result[-1]["lower"] == 64.5
    assert abs(sum(item["probability"] for item in result) - 100.0) <= 0.1

    p_over = 100 * (
        0.5
        * (1 - __import__("math").erf((64.5 - 53.0) / (10.45 * __import__("math").sqrt(2))))
    )
    assert abs(result[-1]["probability"] - round(p_over, 1)) <= 0.1