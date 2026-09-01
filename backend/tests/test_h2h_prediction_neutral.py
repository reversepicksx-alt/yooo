"""H2H remains visible evidence but has zero predictive influence."""

from pathlib import Path

from wta_engine import _h2h_mult


ROOT = Path(__file__).resolve().parents[2]
PREDICT_SOURCE = (ROOT / "backend" / "routes" / "predict.py").read_text()
MLB_SOURCE = (ROOT / "backend" / "mlb_engine.py").read_text()
CS2_SOURCE = (ROOT / "backend" / "cs2_engine.py").read_text()
WTA_SOURCE = (ROOT / "backend" / "wta_engine.py").read_text()
BAYESIAN_SOURCE = (ROOT / "backend" / "bayesian_engine.py").read_text()


def test_soccer_h2h_weights_are_explicitly_zero_but_evidence_is_retained():
    assert 'real_bayes["opponentH2HWeight"] = 0' in PREDICT_SOURCE
    assert 'real_bayes["h2hLineWeight"] = 0' in PREDICT_SOURCE
    assert 'real_bayes["opponentH2HAvg"]' in PREDICT_SOURCE
    assert 'real_bayes["h2hLineOverRate"]' in PREDICT_SOURCE


def test_mlb_h2h_never_changes_posterior():
    assert "h2h_weight_val = 0.0" in MLB_SOURCE
    assert "h2h_mean_val" in MLB_SOURCE  # still emitted as display evidence
    assert "posterior_mean = (" not in MLB_SOURCE[
        MLB_SOURCE.index("# ── LAYER 12:"): MLB_SOURCE.index("# ── EFFECTIVE STD")
    ]


def test_cs2_h2h_multipliers_are_neutral():
    layer = CS2_SOURCE[CS2_SOURCE.index("# 4e/4f. H2H"):CS2_SOURCE.index("# ── NEW v4 Layers")]
    assert "h2h_form_mult = 1.0" in layer
    assert "h2h_trend_mult = 1.0" in layer
    assert "projection   *= h2h_form_mult" not in layer
    assert "projection    *= h2h_trend_mult" not in layer


def test_wta_h2h_multiplier_is_neutral_for_strong_history():
    strong_h2h = {"p1Wins": 20, "p2Wins": 1}
    assert _h2h_mult(strong_h2h, True, "match_winner") == 1.0
    assert "return 1.0" in WTA_SOURCE[WTA_SOURCE.index("def _h2h_mult"):WTA_SOURCE.index("def _serve_profile_signals")]


def test_h2h_possession_cannot_change_match_stakes_math():
    assert "_effective_poss = _team_exp_poss" in BAYESIAN_SOURCE
    assert "_h2h_poss_override" not in BAYESIAN_SOURCE