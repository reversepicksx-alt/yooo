"""
Unit tests for the 3-Layer Bayesian Engine v2.
Tests the mathematical correctness, weight capping, edge cases,
and the new intelligence features (streak detection, volatility, decay weighting).
"""
import sys
sys.path.insert(0, '/app/backend')

import pytest
from bayesian_engine import (
    compute_bayesian_projection,
    compute_press_intensity_score,
    compute_live_gaussian_update,
    gaussian_likelihood_update,
)


class TestThreeLayerDistribution:
    """The elite model contract: baseline, matchup likelihood, live update."""

    def test_prediction_exposes_60_and_80_percent_bands(self):
        result = compute_bayesian_projection(
            [{'targetStat': v, 'venue': 'home'} for v in [18, 21, 24, 27, 30, 22, 25, 29, 20, 26]],
            'pass_attempts',
            24.5,
            'home',
        )
        assert result['range60'][0] <= result['range60'][1]
        assert result['range80'][0] <= result['range80'][1]
        assert result['range80'][0] <= result['range60'][0]
        assert result['range60'][1] <= result['range80'][1]
        assert result['confidenceInterval'] == result['range80']
        assert result['distribution']['distributionType'] == 'gaussian'

    def test_count_props_use_discrete_distribution_metadata(self):
        result = compute_bayesian_projection(
            [{'targetStat': v, 'venue': 'home'} for v in [0, 1, 2, 1, 0, 2, 1, 1, 3, 0]],
            'shots',
            1.5,
            'home',
        )
        assert result['distribution']['distributionType'] == 'negative_binomial'
        assert isinstance(result['mostLikelyValue'], (int, float))
        assert len(result['range60']) == 2
        assert len(result['range80']) == 2

    def test_opponent_likelihood_is_sample_shrunk(self):
        result = gaussian_likelihood_update(60, 12, 40, 24)
        assert result['available'] is True
        assert result['posteriorMean'] < 60
        assert result['posteriorMean'] > 40
        assert result['priorWeight'] > result['likelihoodWeight']
        assert result['method'] == 'gaussian_precision_update'

    def test_live_update_keeps_prematch_mean_and_adds_remaining_total(self):
        result = compute_live_gaussian_update(60, 12, 55.5, 'over', 35, 45)
        assert result['available'] is True
        assert result['model'] == 'live_gaussian_remaining_total_v1'
        assert result['preMatchMean'] == 60
        assert result['currentValue'] == 35
        assert result['remainingMinutes'] == 45
        assert result['projectedValue'] > result['currentValue']
        assert 0 <= result['recommendationProbability'] <= 100
        assert result['range60'][0] <= result['range60'][1]
        assert result['range80'][0] <= result['range80'][1]

    def test_live_update_missing_or_zero_elapsed_is_unavailable(self):
        assert compute_live_gaussian_update(60, 12, 55.5, 'under', 0, 0)['available'] is False


class TestBayesianWeightCapping:
    """Verify the Covariate layer never exceeds 25% of total weight."""

    def test_high_variance_player_covariate_capped(self):
        """Previously Covariate would dominate at ~95%. Now capped at 25%."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [18, 32, 15, 28, 22, 35, 19, 30, 14, 27, 21, 33, 16, 29, 20]]
        result = compute_bayesian_projection(logs, 'pass_attempts', 24.5, 'home')
        assert result['covariateWeight'] <= 26, f"Covariate weight {result['covariateWeight']}% exceeds cap"
        assert result['priorWeight'] >= 30, f"Prior weight {result['priorWeight']}% too low"

    def test_covariate_cap_with_dominance(self):
        """Match dominance shouldn't push Covariate beyond 25%."""
        logs = [{'targetStat': 30 + i, 'venue': 'home'} for i in range(15)]
        dom = {'multiplier': 1.20, 'expectedPoss': 60, 'oppExpectedPoss': 40}
        result = compute_bayesian_projection(logs, 'pass_attempts', 35.5, 'home', match_dominance=dom)
        assert result['covariateWeight'] <= 26

    def test_covariate_cap_random_stress(self):
        """Stress test: 100 random players, Covariate never exceeds cap."""
        import random
        random.seed(99)
        for _ in range(100):
            n = random.randint(3, 30)
            vals = [random.uniform(1, 50) for _ in range(n)]
            logs = [{'targetStat': v, 'venue': random.choice(['home', 'away'])} for v in vals]
            result = compute_bayesian_projection(logs, 'pass_attempts', random.uniform(5, 40), 'home')
            assert result['covariateWeight'] <= 26, f"Cap violated: {result['covariateWeight']}%"


class TestMomentumLayer:
    """Verify Momentum correctly detects and weights recent form."""

    def test_cold_streak_detection(self):
        """Cold streak: recent 5 games well below season average."""
        logs = [{'targetStat': v, 'venue': 'away'} for v in [12, 14, 10, 15, 11, 28, 30, 25, 27, 32, 29, 26]]
        result = compute_bayesian_projection(logs, 'shots', 22.5, 'away')
        assert result['momentumLabel'] == 'COLD'
        assert result['momentumEffect'] < -5
        assert result['posteriorMean'] < result['priorMean'], "Cold streak should pull posterior below prior"

    def test_hot_streak_detection(self):
        """Hot streak: recent 5 games well above season average."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [8, 7, 9, 6, 8, 4, 5, 3, 5, 4]]
        result = compute_bayesian_projection(logs, 'shots', 5.5, 'home')
        assert result['momentumLabel'] in ('HOT', 'WARMING')
        assert result['momentumEffect'] > 0
        assert result['posteriorMean'] > result['priorMean'], "Hot streak should push posterior above prior"

    def test_stable_momentum(self):
        """Consistent player: momentum should be STABLE with small effect."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [25, 24, 26, 25, 23, 24, 25, 26, 24, 25]]
        result = compute_bayesian_projection(logs, 'passes', 24.5, 'home')
        assert result['momentumLabel'] == 'STABLE'
        assert abs(result['momentumEffect']) < 1.0


class TestStreakDetection:
    """Verify the new streak detection feature."""

    def test_over_streak_5(self):
        """5 consecutive games over the line."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [28, 30, 27, 26, 29, 20, 18, 22]]
        result = compute_bayesian_projection(logs, 'passes', 25.5, 'home')
        assert result['streakFlag'] == 'OVER_5'

    def test_under_streak_3(self):
        """3 consecutive games under the line."""
        logs = [{'targetStat': v, 'venue': 'away'} for v in [3, 4, 2, 8, 7, 6, 9]]
        result = compute_bayesian_projection(logs, 'goals', 4.5, 'away')
        assert 'UNDER' in result['streakFlag']

    def test_no_streak(self):
        """Mixed results — no streak."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [28, 20, 30, 18, 25, 22, 27]]
        result = compute_bayesian_projection(logs, 'passes', 24.5, 'home')
        assert result['streakFlag'] == 'NONE'


class TestVolatility:
    """Verify the volatility classification."""

    def test_low_volatility(self):
        """Consistent player should be LOW volatility."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [25, 24, 26, 25, 23, 24, 25]]
        result = compute_bayesian_projection(logs, 'passes', 24.5, 'home')
        assert result['volatility'] == 'LOW'
        assert result['cv'] < 0.15

    def test_high_volatility(self):
        """Erratic player should be HIGH or EXTREME volatility."""
        logs = [{'targetStat': v, 'venue': 'home'} for v in [5, 30, 8, 35, 12, 28, 6, 32]]
        result = compute_bayesian_projection(logs, 'passes', 20.5, 'home')
        assert result['volatility'] in ('HIGH', 'EXTREME')
        assert result['cv'] > 0.30


class TestEdgeCases:
    """Edge cases that previously crashed or gave bad results."""

    def test_empty_logs(self):
        result = compute_bayesian_projection([], 'passes', 25.5, 'home')
        assert result['posteriorMean'] == 25.5
        assert result['momentumLabel'] == 'NO DATA'
        assert result['streakFlag'] == 'NONE'
        assert result['volatility'] == 'UNKNOWN'

    def test_single_game(self):
        result = compute_bayesian_projection([{'targetStat': 28, 'venue': 'home'}], 'passes', 25.5, 'home')
        assert result['priorSamples'] == 1
        assert result['posteriorMean'] > 0

    def test_two_games(self):
        result = compute_bayesian_projection(
            [{'targetStat': 28, 'venue': 'home'}, {'targetStat': 12, 'venue': 'away'}],
            'passes', 25.5, 'home'
        )
        assert result['priorSamples'] == 2
        assert 0 < result['priorWeight'] <= 100
        assert 0 < result['momentumWeight'] <= 100
        assert result['covariateWeight'] <= 26

    def test_zero_variance(self):
        """All identical values — should not crash."""
        logs = [{'targetStat': 25, 'venue': 'home'}] * 10
        result = compute_bayesian_projection(logs, 'passes', 25.5, 'home')
        assert result['posteriorMean'] == 25.0
        assert result['volatility'] == 'LOW'

    def test_very_small_stats(self):
        """Goals (0-3 range) — system should handle small numbers."""
        logs = [{'targetStat': v, 'venue': 'h'} for v in [1, 0, 2, 0, 1, 0, 1, 3, 0, 1]]
        result = compute_bayesian_projection(logs, 'goals', 0.5, 'h')
        assert result['posteriorMean'] > 0
        assert result['recommendation'] in ('over', 'under')


class TestPriorMomentumDominance:
    """The core fix: Prior + Momentum should always dominate over Covariate."""

    def test_player_data_dominates(self):
        """For any player, Prior + Momentum should be >= 74% of total weight."""
        import random
        random.seed(123)
        for _ in range(50):
            n = random.randint(5, 30)
            vals = [random.uniform(2, 40) for _ in range(n)]
            logs = [{'targetStat': v, 'venue': random.choice(['home', 'away'])} for v in vals]
            result = compute_bayesian_projection(logs, 'pass_attempts', random.uniform(10, 35), 'home')
            player_weight = result['priorWeight'] + result['momentumWeight']
            assert player_weight >= 74, f"Player data weight {player_weight}% < 74%"


class TestPressIntensity:
    """Press Intensity uses exact-fixture opponent passes and bounded effects."""

    @staticmethod
    def _stats(**overrides):
        row = {
            # Deliberately make the defending team's passes very different from
            # the opponent's passes. The latter must be the synthetic numerator.
            "totalPasses": 900,
            "opponentTotalPasses": 300,
            "possession": "55%",
            "tackles_total": 20,
            "tackles_interceptions": 10,
            "tackles_blocks": 5,
            "duels_won_agg": 20,
            "fouls_committed_agg": 10,
        }
        row.update(overrides)
        return [dict(row), dict(row)]

    def test_uses_same_fixture_opponent_passes_as_numerator(self):
        packet = compute_press_intensity_score(self._stats())
        assert packet["status"] == "available"
        assert packet["signal_used"] == "synthetic_ppda_and_actions"
        assert packet["avg_opponent_passes"] == 300.0
        assert packet["synthetic_ppda"] < 10
        assert packet["score"] > 0.6

    def test_missing_action_fields_are_explicitly_unavailable(self):
        packet = compute_press_intensity_score([
            {"totalPasses": 450, "opponentTotalPasses": 300, "possession": "51%"},
        ])
        assert packet["status"] == "unavailable"
        assert packet["sampleStatus"] == "unavailable"
        assert packet["projectionApplied"] is False
        assert "defensive-action" in packet["reasoning"]

    def test_every_bayesian_call_exposes_press_contract(self):
        logs = [{"targetStat": 30, "minutes": 90}] * 8
        for prop in ("pass_attempts", "passes", "key_passes", "crosses"):
            result = compute_bayesian_projection(
                logs,
                prop,
                25.5,
                "home",
                opponent_fixture_stats=self._stats(),
                position="LW",
            )
            assert result["pressIntensity"]["status"] == "available"
            assert 0 <= result["pressIntensity"]["score100"] <= 100
        assert compute_bayesian_projection(
            logs,
            "pass_attempts",
            25.5,
            "home",
            opponent_fixture_stats=[],
            position="LW",
        )["pressIntensity"]["status"] == "unavailable"

    def test_multiplier_direction_is_role_aware_and_bounded(self):
        logs = [{"targetStat": 30, "minutes": 90}] * 8
        defender_base = compute_bayesian_projection(
            logs, "passes", 25.5, "home", position="CB"
        )
        defender_pressed = compute_bayesian_projection(
            logs,
            "passes",
            25.5,
            "home",
            opponent_fixture_stats=self._stats(),
            position="CB",
        )
        midfielder_base = compute_bayesian_projection(
            logs, "passes", 25.5, "home", position="CM"
        )
        midfielder_pressed = compute_bayesian_projection(
            logs,
            "passes",
            25.5,
            "home",
            opponent_fixture_stats=self._stats(),
            position="CM",
        )
        defender_factor = defender_pressed["pressIntensity"]["projectionMultiplier"]
        midfielder_factor = midfielder_pressed["pressIntensity"]["projectionMultiplier"]
        assert defender_factor > 1.0
        assert midfielder_factor < 1.0
        assert 0.88 <= defender_factor <= 1.12
        assert 0.88 <= midfielder_factor <= 1.12
        assert defender_pressed["posteriorMean"] > defender_base["posteriorMean"]
        assert midfielder_pressed["posteriorMean"] < midfielder_base["posteriorMean"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
