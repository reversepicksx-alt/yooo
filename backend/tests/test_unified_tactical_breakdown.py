"""
Test suite for unified tactical breakdown architecture change (iteration 23)
- Tactical tab removed, now only 2 tabs: SCAN | TRACKING
- POST /api/predict now returns 'tacticalBreakdown' field as part of response
- tacticalBreakdown includes international stats when match is international
- Follow-up chat endpoints still work for questions about predictions
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndCache:
    """Basic health and cache status tests"""
    
    def test_health_endpoint(self):
        """GET /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"✓ Health check passed: {data}")
    
    def test_cache_status(self):
        """GET /api/cache/status returns valid counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert "teams" in data
        assert "players" in data
        assert data["leagues"] > 0
        assert data["teams"] > 0
        assert data["players"] > 0
        print(f"✓ Cache status: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}, nationalTeams={data.get('nationalTeams', 0)}")


class TestTacticalFollowUpEndpoints:
    """Test that tactical endpoints still work for follow-up chat"""
    
    def test_tactical_start(self):
        """POST /api/tactical/start returns session_id and welcome message"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            headers={"Content-Type": "application/json"},
            json={},
            timeout=15
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data
        assert len(data["session_id"]) > 0
        assert len(data["message"]) > 0
        print(f"✓ Tactical start: session_id={data['session_id'][:20]}..., message length={len(data['message'])}")
        return data["session_id"]
    
    def test_tactical_message(self):
        """POST /api/tactical/message works for follow-up questions"""
        # First start a session
        start_response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            headers={"Content-Type": "application/json"},
            json={},
            timeout=15
        )
        assert start_response.status_code == 200
        session_id = start_response.json()["session_id"]
        
        # Send a follow-up message
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": "What factors affect pass attempts?"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert len(data["response"]) > 50  # Should have substantial response
        print(f"✓ Tactical message: response length={len(data['response'])}")


class TestPredictWithTacticalBreakdown:
    """Test that /api/predict returns tacticalBreakdown field"""
    
    def test_predict_returns_tactical_breakdown(self):
        """POST /api/predict returns response with 'tacticalBreakdown' field (non-empty string)"""
        # Use a simple domestic match for faster response
        payload = {
            "playerId": 2735,  # Hojbjerg
            "playerName": "Pierre-Emile Højbjerg",
            "teamId": 33,  # Manchester United
            "teamName": "Manchester United",
            "opponentId": 40,  # Liverpool
            "opponentName": "Liverpool",
            "leagueId": 39,  # Premier League
            "venue": "home",
            "propType": "pass_attempts",
            "line": 35.5
        }
        
        print(f"Testing predict endpoint with domestic match (may take 30-60s)...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            timeout=90  # Extended timeout for unified prediction
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:500]}"
        data = response.json()
        
        # Check core prediction fields
        assert "player" in data, "Missing 'player' field"
        assert "projectedValue" in data, "Missing 'projectedValue' field"
        assert "recommendation" in data, "Missing 'recommendation' field"
        assert "confidenceScore" in data, "Missing 'confidenceScore' field"
        
        # KEY TEST: Check tacticalBreakdown field exists and is non-empty
        assert "tacticalBreakdown" in data, "Missing 'tacticalBreakdown' field - this is the key architectural change!"
        tactical = data["tacticalBreakdown"]
        assert isinstance(tactical, str), f"tacticalBreakdown should be string, got {type(tactical)}"
        assert len(tactical) > 100, f"tacticalBreakdown too short ({len(tactical)} chars) - should have substantial analysis"
        
        print(f"✓ Predict with tacticalBreakdown:")
        print(f"  - Player: {data['player'].get('name', '?')}")
        print(f"  - Projected: {data['projectedValue']}")
        print(f"  - Recommendation: {data['recommendation']}")
        print(f"  - Confidence: {data['confidenceScore']}%")
        print(f"  - tacticalBreakdown length: {len(tactical)} chars")
        print(f"  - tacticalBreakdown preview: {tactical[:200]}...")
        
        return data
    
    def test_predict_international_includes_intl_stats(self):
        """POST /api/predict tacticalBreakdown includes international stats when match is international"""
        # International match: Denmark vs Czech Republic
        payload = {
            "playerId": 2735,  # Hojbjerg
            "playerName": "Pierre-Emile Højbjerg",
            "teamId": 21,  # Denmark national team
            "teamName": "Denmark",
            "opponentId": 770,  # Czech Republic
            "opponentName": "Czech Republic",
            "leagueId": 5,  # UEFA Nations League / International
            "venue": "home",
            "propType": "pass_attempts",
            "line": 94.5
        }
        
        print(f"Testing predict endpoint with INTERNATIONAL match (Denmark vs Czech Republic)...")
        print(f"This tests that tacticalBreakdown includes international stats...")
        
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            timeout=90
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text[:500]}"
        data = response.json()
        
        # Check tacticalBreakdown exists
        assert "tacticalBreakdown" in data, "Missing 'tacticalBreakdown' field"
        tactical = data["tacticalBreakdown"]
        assert len(tactical) > 100, f"tacticalBreakdown too short ({len(tactical)} chars)"
        
        # Check for international context indicators
        tactical_lower = tactical.lower()
        intl_indicators = ["international", "national team", "denmark", "czech", "nations league", "friendl"]
        found_indicators = [ind for ind in intl_indicators if ind in tactical_lower]
        
        print(f"✓ International predict with tacticalBreakdown:")
        print(f"  - Player: {data['player'].get('name', '?')}")
        print(f"  - Projected: {data['projectedValue']}")
        print(f"  - Recommendation: {data['recommendation']}")
        print(f"  - tacticalBreakdown length: {len(tactical)} chars")
        print(f"  - International indicators found: {found_indicators}")
        print(f"  - tacticalBreakdown preview: {tactical[:300]}...")
        
        # At least some international context should be present
        assert len(found_indicators) >= 1, f"Expected international context in tacticalBreakdown, found indicators: {found_indicators}"
        
        return data


class TestPredictResponseStructure:
    """Test the full structure of predict response"""
    
    def test_predict_has_all_required_fields(self):
        """Verify predict response has all expected fields including tacticalBreakdown"""
        payload = {
            "playerId": 2735,
            "playerName": "Pierre-Emile Højbjerg",
            "teamId": 33,
            "teamName": "Manchester United",
            "opponentId": 40,
            "opponentName": "Liverpool",
            "leagueId": 39,
            "venue": "home",
            "propType": "tackles",
            "line": 3.5
        }
        
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            timeout=90
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Required fields
        required_fields = [
            "player", "opponent", "propType", "line", "projectedValue",
            "recommendation", "confidenceScore", "confidenceLevel",
            "tacticalBreakdown"  # NEW: This is the key field
        ]
        
        missing = [f for f in required_fields if f not in data]
        assert len(missing) == 0, f"Missing required fields: {missing}"
        
        # Optional but expected fields
        optional_fields = [
            "matchupOverview", "recentSamples", "bayesianMetrics",
            "probabilityCurve", "reasoning", "sharpSummary"
        ]
        
        present_optional = [f for f in optional_fields if f in data]
        print(f"✓ Predict response structure verified:")
        print(f"  - All required fields present: {required_fields}")
        print(f"  - Optional fields present: {present_optional}")
        print(f"  - tacticalBreakdown: {len(data.get('tacticalBreakdown', ''))} chars")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
