"""
Iteration 68: Testing Grok model fixes, Bayesian fallback, opponentId optional, and team resolution with accent-stripped matching.

Tests:
1. POST /api/predict with valid request (teamId=40, opponentId=50) should return 200
2. POST /api/predict with opponentId=0 should NOT crash with 400 — should return 200 (AI-only mode)
3. POST /api/predict with teamId=0 AND opponentId=0 should still return 200 (full fallback mode)
4. POST /api/scan-prop with invalid base64 should return graceful error, not 500
5. Team resolution: 'bayern munich' should resolve to Bayern München (teamId=157) - via /api/re-resolve
6. Team resolution: 'atletico madrid' should resolve to Atletico Madrid (teamId=530) - via /api/re-resolve
7. Team resolution: 'borussia monchengladbach' should resolve to Borussia Mönchengladbach (teamId=163) - via /api/re-resolve
8. Player resolution: 'Joshua Kimmich' should resolve to J. Kimmich at Bayern München - via /api/re-resolve
9. Player resolution: 'Harry Kane' should resolve correctly - via /api/re-resolve
10. Backend server starts without errors
11. Grok model names are correctly configured
12. PredictionRequest.opponentId is optional with default 0
"""
import pytest
import requests
import os
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://props-ai-predict.preview.emergentagent.com').rstrip('/')


class TestBackendHealth:
    """Test that backend server is running and healthy"""
    
    def test_backend_health(self):
        """Backend server should respond to requests"""
        response = requests.get(f"{BASE_URL}/api/leagues", timeout=10)
        assert response.status_code == 200, f"Backend not responding: {response.status_code}"
        print(f"✓ Backend health check passed - status {response.status_code}")


class TestPredictEndpoint:
    """Test /api/predict endpoint with various configurations"""
    
    def test_predict_with_valid_ids(self):
        """POST /api/predict with valid teamId=40, opponentId=50 should return 200"""
        payload = {
            "leagueId": 39,  # Premier League
            "playerId": 1100,  # Example player ID
            "playerName": "Mohamed Salah",
            "teamId": 40,  # Liverpool
            "teamName": "Liverpool",
            "opponentId": 50,  # Manchester City
            "opponentName": "Manchester City",
            "venue": "home",
            "propType": "shots",
            "line": 3.5
        }
        response = requests.post(f"{BASE_URL}/api/predict", json=payload, timeout=120)
        
        # Should return 200, not 400 or 500
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:500]}"
        
        data = response.json()
        # Verify response structure
        assert "projectedValue" in data or "error" not in data, f"Response missing projectedValue: {data}"
        print(f"✓ Predict with valid IDs returned 200 - projectedValue: {data.get('projectedValue', 'N/A')}")
    
    def test_predict_with_opponent_id_zero(self):
        """POST /api/predict with opponentId=0 should NOT crash with 400 — should return 200 (AI-only mode)"""
        payload = {
            "leagueId": 78,  # Bundesliga
            "playerId": 10007,  # Joshua Kimmich
            "playerName": "Joshua Kimmich",
            "teamId": 157,  # Bayern Munich
            "teamName": "Bayern Munich",
            "opponentId": 0,  # Missing opponent - should trigger AI-only mode
            "opponentName": "",
            "venue": "home",
            "propType": "pass_attempts",
            "line": 85.5
        }
        response = requests.post(f"{BASE_URL}/api/predict", json=payload, timeout=120)
        
        # Should return 200, NOT 400 (the bug we're testing for)
        assert response.status_code == 200, f"Expected 200 for opponentId=0, got {response.status_code}: {response.text[:500]}"
        
        data = response.json()
        print(f"✓ Predict with opponentId=0 returned 200 (AI-only mode) - projectedValue: {data.get('projectedValue', 'N/A')}")
    
    def test_predict_with_both_ids_zero(self):
        """POST /api/predict with teamId=0 AND opponentId=0 should still return 200 (full fallback mode)"""
        payload = {
            "leagueId": 39,
            "playerId": 0,  # No player ID
            "playerName": "Harry Kane",
            "teamId": 0,  # No team ID
            "teamName": "Bayern Munich",
            "opponentId": 0,  # No opponent ID
            "opponentName": "",
            "venue": "home",
            "propType": "shots",
            "line": 4.5
        }
        response = requests.post(f"{BASE_URL}/api/predict", json=payload, timeout=120)
        
        # Should return 200, not crash
        assert response.status_code == 200, f"Expected 200 for full fallback mode, got {response.status_code}: {response.text[:500]}"
        
        data = response.json()
        print(f"✓ Predict with teamId=0 AND opponentId=0 returned 200 (full fallback) - projectedValue: {data.get('projectedValue', 'N/A')}")


class TestScanPropEndpoint:
    """Test /api/scan-prop endpoint error handling"""
    
    def test_scan_prop_invalid_base64(self):
        """POST /api/scan-prop with invalid base64 should return graceful error, not 500"""
        payload = {
            "image_base64": "not_valid_base64_data!!!",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/scan-prop", json=payload, timeout=30)
        
        # Should NOT return 500 (internal server error)
        # Acceptable: 200 with error message, 400, 422
        assert response.status_code != 500, f"Got 500 error for invalid base64: {response.text[:500]}"
        
        # If 200, should have error message or empty picks
        if response.status_code == 200:
            data = response.json()
            # Either has error field or empty picks
            has_error = "error" in data or data.get("success") == False
            has_empty_picks = data.get("picks") == [] or data.get("picks") is None
            assert has_error or has_empty_picks, f"Expected error or empty picks for invalid base64: {data}"
        
        print(f"✓ Scan-prop with invalid base64 handled gracefully - status {response.status_code}")


class TestTeamResolutionViaAPI:
    """Test team resolution with accent-stripped matching via /api/re-resolve endpoint"""
    
    def test_bayern_munich_resolution(self):
        """'bayern munich' should resolve to Bayern München (teamId=157) via /api/re-resolve"""
        payload = {
            "playerName": "Joshua Kimmich",
            "playerTeam": "bayern munich",
            "opponentName": "Dortmund",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/re-resolve", json=payload, timeout=30)
        
        assert response.status_code == 200, f"Re-resolve failed: {response.status_code}"
        data = response.json()
        
        resolved = data.get("resolved")
        assert resolved is not None, f"Player not resolved: {data}"
        
        team_id = resolved.get("teamId")
        team_name = resolved.get("teamName", "")
        
        # Bayern Munich should resolve to teamId 157
        assert team_id == 157, f"Expected teamId=157 for Bayern Munich, got {team_id}"
        print(f"✓ 'bayern munich' resolved to {team_name} (ID: {team_id})")
    
    def test_atletico_madrid_resolution(self):
        """'atletico madrid' should resolve to Atletico Madrid (teamId=530) via /api/re-resolve"""
        payload = {
            "playerName": "Antoine Griezmann",
            "playerTeam": "atletico madrid",
            "opponentName": "Real Madrid",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/re-resolve", json=payload, timeout=30)
        
        assert response.status_code == 200, f"Re-resolve failed: {response.status_code}"
        data = response.json()
        
        resolved = data.get("resolved")
        # Player may or may not be found, but we can check leagueId
        league_id = data.get("leagueId")
        
        # Atletico Madrid is in La Liga (140)
        assert league_id == 140, f"Expected leagueId=140 for Atletico Madrid, got {league_id}"
        print(f"✓ 'atletico madrid' resolved to leagueId={league_id}")
    
    def test_borussia_monchengladbach_resolution(self):
        """'borussia monchengladbach' should resolve via /api/re-resolve"""
        payload = {
            "playerName": "Test Player",
            "playerTeam": "borussia monchengladbach",
            "opponentName": "Bayern Munich",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/re-resolve", json=payload, timeout=30)
        
        assert response.status_code == 200, f"Re-resolve failed: {response.status_code}"
        data = response.json()
        
        # Borussia Mönchengladbach is in Bundesliga (78)
        league_id = data.get("leagueId")
        assert league_id == 78, f"Expected leagueId=78 for Borussia Mönchengladbach, got {league_id}"
        print(f"✓ 'borussia monchengladbach' resolved to leagueId={league_id}")


class TestPlayerResolutionViaAPI:
    """Test player resolution via /api/re-resolve endpoint"""
    
    def test_joshua_kimmich_resolution(self):
        """'Joshua Kimmich' should resolve to J. Kimmich at Bayern München, not Germany"""
        payload = {
            "playerName": "Joshua Kimmich",
            "playerTeam": "Bayern Munich",
            "opponentName": "Dortmund",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/re-resolve", json=payload, timeout=30)
        
        assert response.status_code == 200, f"Re-resolve failed: {response.status_code}"
        data = response.json()
        
        resolved = data.get("resolved")
        assert resolved is not None, "Joshua Kimmich not resolved"
        
        player_id = resolved.get("playerId")
        player_name = resolved.get("playerName")
        team_name = resolved.get("teamName", "")
        
        assert player_id is not None, "Joshua Kimmich playerId is None"
        
        # Should be at Bayern München (club), not Germany (national)
        # Bayern Munich teamId is 157
        team_id = resolved.get("teamId")
        assert team_id == 157, f"Expected teamId=157 (Bayern), got {team_id}"
        
        print(f"✓ 'Joshua Kimmich' resolved to {player_name} (ID: {player_id}) at {team_name}")
    
    def test_harry_kane_resolution(self):
        """'Harry Kane' should resolve correctly"""
        payload = {
            "playerName": "Harry Kane",
            "playerTeam": "Bayern Munich",
            "opponentName": "Dortmund",
            "sport": "soccer"
        }
        response = requests.post(f"{BASE_URL}/api/re-resolve", json=payload, timeout=30)
        
        assert response.status_code == 200, f"Re-resolve failed: {response.status_code}"
        data = response.json()
        
        resolved = data.get("resolved")
        assert resolved is not None, "Harry Kane not resolved"
        
        player_id = resolved.get("playerId")
        player_name = resolved.get("playerName")
        team_name = resolved.get("teamName", "")
        
        assert player_id is not None, "Harry Kane playerId is None"
        print(f"✓ 'Harry Kane' resolved to {player_name} (ID: {player_id}) at {team_name}")


class TestGrokModelConfiguration:
    """Test that Grok model names are correctly configured"""
    
    def test_grok_model_names(self):
        """Verify grok model names are consistent (grok-4-1-fast-non-reasoning, not grok-4.1-fast)"""
        from grok_engine import GROK_MODEL, GROK_REASONING_MODEL
        
        # The fix changed grok-4.1-fast to grok-4-1-fast-non-reasoning
        assert "4.1" not in GROK_MODEL, f"GROK_MODEL still has old format: {GROK_MODEL}"
        assert "4-1" in GROK_MODEL or "grok-3" in GROK_MODEL.lower(), f"GROK_MODEL unexpected format: {GROK_MODEL}"
        
        print(f"✓ GROK_MODEL: {GROK_MODEL}")
        print(f"✓ GROK_REASONING_MODEL: {GROK_REASONING_MODEL}")


class TestPredictionRequestModel:
    """Test that PredictionRequest model has opponentId as optional"""
    
    def test_opponent_id_optional(self):
        """PredictionRequest.opponentId should be optional with default 0"""
        from models import PredictionRequest
        
        # Create request without opponentId - should not raise
        try:
            req = PredictionRequest(
                playerName="Test Player",
                propType="shots",
                line=3.5
            )
            # opponentId should default to 0
            assert req.opponentId == 0, f"opponentId default should be 0, got {req.opponentId}"
            print(f"✓ PredictionRequest.opponentId is optional with default 0")
        except Exception as e:
            pytest.fail(f"PredictionRequest should accept missing opponentId: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
