"""
Test the Bayesian-AI Fusion logic that merges Grok's AI projection
with the Bayesian Engine's mathematical posterior into one unified number.
"""
import sys
sys.path.insert(0, '/app/backend')

import pytest


def _simulate_fusion(ai_proj, ai_rec, bayes_posterior, bayes_prob, bayes_rec, line):
    """Replicate the exact fusion logic from predict.py"""
    if bayes_rec == ai_rec:
        bayes_weight = 0.40
    else:
        if bayes_prob >= 0.70:
            bayes_weight = 0.50
        elif bayes_prob >= 0.55:
            bayes_weight = 0.40
        else:
            bayes_weight = 0.30

    ai_weight = round(1.0 - bayes_weight, 2)
    fused = round(ai_weight * ai_proj + bayes_weight * bayes_posterior, 1)
    fused_rec = "over" if fused > line else "under"
    return fused, fused_rec, ai_weight, bayes_weight


class TestFusionAgreement:
    """When AI and Bayesian agree on direction, standard 60/40 blend."""

    def test_both_over(self):
        fused, rec, aw, bw = _simulate_fusion(40.0, "over", 42.0, 0.75, "over", 35.5)
        assert rec == "over"
        assert bw == 0.40
        assert aw == 0.60
        assert fused == round(0.60 * 40.0 + 0.40 * 42.0, 1)

    def test_both_under(self):
        fused, rec, aw, bw = _simulate_fusion(20.0, "under", 18.0, 0.80, "under", 25.5)
        assert rec == "under"
        assert bw == 0.40


class TestFusionDisagreement:
    """When AI and Bayesian disagree, weights adapt by confidence."""

    def test_strong_bayesian_disagree(self):
        """Bayesian > 70% confidence: gets 50% weight (equal)."""
        fused, rec, aw, bw = _simulate_fusion(
            51.0, "over", 47.4, 0.721, "under", 50.5
        )
        assert bw == 0.50
        assert aw == 0.50
        expected = round(0.50 * 51.0 + 0.50 * 47.4, 1)
        assert fused == expected
        assert rec == "under"  # 49.2 < 50.5 → under

    def test_moderate_bayesian_disagree(self):
        """Bayesian 55-70% confidence: gets 40% weight."""
        fused, rec, aw, bw = _simulate_fusion(
            50.6, "under", 54.4, 0.671, "over", 51.5
        )
        assert bw == 0.40
        assert aw == 0.60

    def test_weak_bayesian_disagree(self):
        """Bayesian < 55% confidence: gets only 30% weight."""
        fused, rec, aw, bw = _simulate_fusion(
            30.0, "under", 31.0, 0.52, "over", 30.5
        )
        assert bw == 0.30
        assert aw == 0.70


class TestFusionRealCases:
    """The 3 real contradictions from the user's screenshots."""

    def test_whiteman_contradiction(self):
        """Whiteman: AI=51 OVER, Bayesian=47.4 UNDER (72.1%). Should fuse to UNDER."""
        fused, rec, _, _ = _simulate_fusion(51.0, "over", 47.4, 0.721, "under", 50.5)
        assert rec == "under", f"Whiteman should fuse to UNDER, got {rec}"
        assert fused < 50.5

    def test_klarer_contradiction(self):
        """Klarer: AI=50.6 UNDER, Bayesian=54.4 OVER (67.1%). Should fuse to OVER."""
        fused, rec, _, _ = _simulate_fusion(50.6, "under", 54.4, 0.671, "over", 51.5)
        assert rec == "over", f"Klarer should fuse to OVER, got {rec}"
        assert fused > 51.5

    def test_randell_agreement(self):
        """Randell: Both OVER. Bayesian 45.3 pulls up Grok's timid 35.8."""
        fused, rec, _, _ = _simulate_fusion(35.8, "over", 45.3, 0.937, "over", 35.5)
        assert rec == "over"
        assert fused > 35.8, "Fusion should pull Randell up from Grok's timid 35.8"
        assert fused > 35.5


class TestFusionEdgeCases:
    """Edge cases to bulletproof the fusion."""

    def test_identical_projections(self):
        """When AI and Bayesian produce the same number."""
        fused, _, _, _ = _simulate_fusion(35.0, "over", 35.0, 0.80, "over", 30.5)
        assert fused == 35.0

    def test_extreme_disagreement(self):
        """Massive divergence — fusion should pull toward reasonable middle."""
        fused, _, _, _ = _simulate_fusion(60.0, "over", 30.0, 0.90, "under", 45.0)
        assert 40 <= fused <= 50, f"Extreme divergence should land in reasonable range, got {fused}"

    def test_coin_flip_bayesian(self):
        """Bayesian is 50/50 — should get minimal weight."""
        _, _, _, bw = _simulate_fusion(40.0, "over", 30.0, 0.50, "under", 35.0)
        assert bw == 0.30, "50/50 Bayesian should get lowest weight"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
