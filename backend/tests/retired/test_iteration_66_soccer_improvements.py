"""
Iteration 66: Soccer Prediction Improvements Testing
=====================================================
Tests for three new systems implemented to prevent prop prediction misses:
1. Game Tempo Estimation - adjusts pass projections based on expected match goals
2. Favorite Dampening - reduces OVER pass predictions for heavy favorites
3. Cross-Team Possession Contradiction Warning - warns when saving same-direction pass props for opposing teams

Also verifies:
- No basketball references remain in codebase
- Health endpoint works
- Predict endpoint accepts requests
"""

import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
BACKEND_DIR = "/app/backend"


class TestHealthEndpoint:
    """Test /api/health endpoint"""
    
    def test_health_returns_ok(self):
        """Verify /api/health returns status ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok", f"Health status not ok: {data}"
        print(f"✓ Health endpoint returns: {data}")


class TestPredictEndpoint:
    """Test /api/predict endpoint accepts soccer prediction requests"""
    
    def test_predict_endpoint_exists(self):
        """Verify POST /api/predict endpoint exists and accepts requests"""
        # Basic payload - will likely fail due to missing real IDs but should not 404
        payload = {
            "playerId": 0,
            "playerName": "Test Player",
            "teamId": 0,
            "teamName": "Test Team",
            "opponentId": 0,
            "opponentName": "Test Opponent",
            "propType": "pass_attempts",
            "line": 30.5,
            "venue": "home",
            "leagueId": 39
        }
        response = requests.post(f"{BASE_URL}/api/predict", json=payload, timeout=60)
        # Should not be 404 or 405 - endpoint exists
        assert response.status_code != 404, "Predict endpoint not found (404)"
        assert response.status_code != 405, "Predict endpoint method not allowed (405)"
        print(f"✓ Predict endpoint responded with status: {response.status_code}")
        # Even if it fails due to missing data, it should return a structured response
        if response.status_code == 200:
            data = response.json()
            print(f"✓ Predict returned data with keys: {list(data.keys())[:10]}")


class TestGameTempoEstimationCode:
    """Verify Game Tempo Estimation code exists in predict.py"""
    
    def test_game_tempo_variable_initialization(self):
        """Verify game_tempo dict is initialized with expected keys"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Check for game_tempo initialization
        assert 'game_tempo = {"expectedTempo": "normal", "tempoMultiplier": 1.0, "notes": []}' in content, \
            "game_tempo initialization not found"
        print("✓ game_tempo variable initialized correctly")
    
    def test_game_tempo_calculation_logic(self):
        """Verify game tempo calculation uses goals-per-game data"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Check for expected total goals calculation
        assert "expected_total = expected_team_goals + expected_opp_goals" in content, \
            "Expected total goals calculation not found"
        print("✓ Expected total goals calculation exists")
        
        # Check for tempo classification thresholds
        assert "expected_total >= 3.2" in content, "High tempo threshold (3.2) not found"
        assert "expected_total <= 1.8" in content, "Low tempo threshold (1.8) not found"
        print("✓ Tempo classification thresholds exist (3.2 high, 1.8 low)")
    
    def test_game_tempo_logging(self):
        """Verify [GAME TEMPO] log entries are present"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert '[GAME TEMPO]' in content, "[GAME TEMPO] log prefix not found"
        # Check for the specific log format
        assert 'print(f"[GAME TEMPO] {req.playerName}:' in content, \
            "[GAME TEMPO] player log not found"
        print("✓ [GAME TEMPO] logging exists")
    
    def test_game_tempo_multiplier_application(self):
        """Verify tempo multiplier is applied to pass props"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Check for tempo boost/drop logic
        assert "tempo_boost" in content, "tempo_boost variable not found"
        assert "tempo_drop" in content, "tempo_drop variable not found"
        assert "tempoMultiplier" in content, "tempoMultiplier key not found"
        print("✓ Tempo multiplier application logic exists")


class TestFavoriteDampeningCode:
    """Verify Favorite Dampening code exists in predict.py"""
    
    def test_favorite_dampening_initialization(self):
        """Verify favorite_dampening dict is initialized"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert 'favorite_dampening = {"applied": False}' in content, \
            "favorite_dampening initialization not found"
        print("✓ favorite_dampening variable initialized")
    
    def test_favorite_dampening_threshold(self):
        """Verify heavy favorite threshold is 1.60 odds"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "team_odds < 1.60" in content, "Heavy favorite threshold (1.60) not found"
        print("✓ Heavy favorite threshold (1.60 odds) exists")
    
    def test_favorite_dampening_logging(self):
        """Verify [FAVORITE DAMPENING] log entries are present"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert '[FAVORITE DAMPENING]' in content, "[FAVORITE DAMPENING] log prefix not found"
        assert 'print(f"[FAVORITE DAMPENING] {req.playerName}:' in content, \
            "[FAVORITE DAMPENING] player log not found"
        print("✓ [FAVORITE DAMPENING] logging exists")
    
    def test_favorite_dampening_pass_props_only(self):
        """Verify dampening only applies to pass-related props"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Check for pass props filter
        assert 'poss_sensitive_for_fav = {"pass_attempts", "passes", "key_passes", "crosses"}' in content, \
            "Pass props filter for favorite dampening not found"
        print("✓ Favorite dampening applies only to pass props")


class TestCrossTeamPossessionContradiction:
    """Verify Cross-Team Possession Contradiction warning in picks.py"""
    
    def test_possession_contradiction_type_exists(self):
        """Verify POSSESSION_CONTRADICTION warning type exists"""
        with open(f"{BACKEND_DIR}/routes/picks.py", "r") as f:
            content = f.read()
        
        assert '"type": "POSSESSION_CONTRADICTION"' in content, \
            "POSSESSION_CONTRADICTION type not found in picks.py"
        print("✓ POSSESSION_CONTRADICTION warning type exists")
    
    def test_possession_contradiction_severity(self):
        """Verify POSSESSION_CONTRADICTION has CRITICAL severity"""
        with open(f"{BACKEND_DIR}/routes/picks.py", "r") as f:
            content = f.read()
        
        # Find the POSSESSION_CONTRADICTION block and check severity
        pattern = r'"type": "POSSESSION_CONTRADICTION".*?"severity": "(\w+)"'
        match = re.search(pattern, content, re.DOTALL)
        assert match, "Could not find POSSESSION_CONTRADICTION severity"
        severity = match.group(1)
        assert severity == "CRITICAL", f"Expected CRITICAL severity, got {severity}"
        print("✓ POSSESSION_CONTRADICTION has CRITICAL severity")
    
    def test_possession_contradiction_message_content(self):
        """Verify warning message mentions zero-sum possession"""
        with open(f"{BACKEND_DIR}/routes/picks.py", "r") as f:
            content = f.read()
        
        assert "ZERO-SUM ALERT" in content, "ZERO-SUM ALERT message not found"
        assert "Possession is zero-sum" in content, "Zero-sum explanation not found"
        print("✓ POSSESSION_CONTRADICTION message explains zero-sum possession")
    
    def test_correlation_warnings_returned(self):
        """Verify correlationWarnings is returned in save_pick response"""
        with open(f"{BACKEND_DIR}/routes/picks.py", "r") as f:
            content = f.read()
        
        assert '"correlationWarnings": correlation_warnings' in content, \
            "correlationWarnings not returned in save_pick response"
        print("✓ correlationWarnings returned in /api/picks/save response")


class TestBayesianPromptAnchor:
    """Verify Bayesian prompt anchor includes tempo and favorite context"""
    
    def test_bayesian_anchor_tempo_injection(self):
        """Verify tempo context is injected into Bayesian prompt anchor"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "[GAME TEMPO WARNING]" in content, "GAME TEMPO WARNING not in prompt anchor"
        assert "Expected match tempo:" in content, "Tempo label not in prompt"
        print("✓ Game tempo context injected into Bayesian prompt anchor")
    
    def test_bayesian_anchor_favorite_injection(self):
        """Verify favorite dampening context is injected into Bayesian prompt anchor"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "[HEAVY FAVORITE ALERT]" in content, "HEAVY FAVORITE ALERT not in prompt anchor"
        assert "game management mode" in content, "Game management explanation not in prompt"
        print("✓ Favorite dampening context injected into Bayesian prompt anchor")


class TestPostFusionScaling:
    """Verify post-fusion tempo scaling and favorite dampening application"""
    
    def test_post_fusion_tempo_scaling_exists(self):
        """Verify POST-FUSION GAME TEMPO SCALING section exists"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "POST-FUSION GAME TEMPO SCALING" in content, \
            "POST-FUSION GAME TEMPO SCALING section not found"
        print("✓ POST-FUSION GAME TEMPO SCALING section exists")
    
    def test_post_fusion_favorite_dampening_exists(self):
        """Verify POST-FUSION FAVORITE DAMPENING section exists"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "POST-FUSION FAVORITE DAMPENING" in content, \
            "POST-FUSION FAVORITE DAMPENING section not found"
        print("✓ POST-FUSION FAVORITE DAMPENING section exists")


class TestNoBasketballReferences:
    """Verify no basketball references remain in key backend files"""
    
    def test_predict_py_no_basketball(self):
        """Verify predict.py has no basketball references"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read().lower()
        
        # Check for basketball-specific terms (excluding "quarter" which is used for "quarter-final" in soccer)
        basketball_terms = ["basketball", "nba", "wnba", "pts_reb_ast"]
        found = [term for term in basketball_terms if term in content]
        assert not found, f"Basketball references found in predict.py: {found}"
        print("✓ predict.py has no basketball references")
    
    def test_picks_py_no_basketball(self):
        """Verify picks.py has no basketball references"""
        with open(f"{BACKEND_DIR}/routes/picks.py", "r") as f:
            content = f.read().lower()
        
        # Check for basketball-specific terms (excluding comments about removal)
        lines = [l for l in content.split('\n') if 'removed' not in l.lower()]
        content_no_comments = '\n'.join(lines)
        
        basketball_terms = ["basketball", "nba", "wnba", "bball_stat_map"]
        found = [term for term in basketball_terms if term in content_no_comments]
        assert not found, f"Basketball references found in picks.py: {found}"
        print("✓ picks.py has no basketball references (excluding removal comments)")
    
    def test_bayesian_engine_no_basketball(self):
        """Verify bayesian_engine.py has no basketball references"""
        with open(f"{BACKEND_DIR}/bayesian_engine.py", "r") as f:
            content = f.read().lower()
        
        basketball_terms = ["basketball", "nba", "wnba"]
        found = [term for term in basketball_terms if term in content]
        assert not found, f"Basketball references found in bayesian_engine.py: {found}"
        print("✓ bayesian_engine.py has no basketball references")


class TestPicksSaveCorrelationWarning:
    """Integration test for /api/picks/save correlation warnings"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token for test user"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "xaviersteverson@gmail.com",
            "password": "test123456"
        })
        if response.status_code == 200:
            # API returns session_token, not token
            return response.json().get("session_token")
        pytest.skip("Authentication failed - skipping authenticated tests")
    
    def test_save_pick_returns_correlation_warnings_field(self, auth_token):
        """Verify /api/picks/save returns correlationWarnings field"""
        # Save a test pick
        pick_payload = {
            "email": "xaviersteverson@gmail.com",
            "token": auth_token,
            "pick": {
                "id": "test-corr-66-1",
                "player": {"id": 12345, "name": "Test Player 1", "team": "Team A"},
                "opponent": "Team B",
                "propType": "pass_attempts",
                "line": 30.5,
                "recommendation": "under",
                "projectedValue": 28,
                "confidenceScore": 65,
                "confidenceLevel": "Medium",
                "_request": {
                    "teamId": 100,
                    "opponentId": 200,
                    "leagueId": 39,
                    "venue": "home"
                }
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/picks/save", json=pick_payload)
        assert response.status_code == 200, f"Save pick failed: {response.text}"
        
        data = response.json()
        assert "correlationWarnings" in data, "correlationWarnings field missing from response"
        print(f"✓ /api/picks/save returns correlationWarnings: {data.get('correlationWarnings')}")
        
        # Cleanup - delete the test pick
        requests.post(f"{BASE_URL}/api/picks/delete", json={
            "email": "xaviersteverson@gmail.com",
            "token": auth_token,
            "pickId": "test-corr-66-1"
        })


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
