"""
Unit tests for the 3-Layer Bayesian Engine v2.
Tests the mathematical correctness, weight capping, edge cases,
and the new intelligence features (streak detection, volatility, decay weighting).
"""
import sys
sys.path.insert(0, '/app/backend')

import pytest
from bayesian_engine import compute_bayesian_projection


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
