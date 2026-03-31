"""
Test Basketball Advanced Analytics Engine (Iteration 31)
Tests the overhauled basketball prediction engine with:
- Per-minute rates, role classification, line proximity z-scores
- Over-rate computation, streak detection, blowout risk
- Consistency scores, data-driven AI overrides
- Confidence capping for coin-flip scenarios
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test data from agent context
TEST_TEAM_ID = 145  # Lakers
TEST_OPPONENT_ID = 140  # Warriors
TEST_PLAYER_NAME = "LeBron James"
TEST_PROP_TYPES = ["points", "rebounds", "pts_reb_ast"]


class TestHealthEndpoint:
    """Health check endpoint tests"""
    
    def test_health_returns_ok(self):
        """GET /api/health returns ok status"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok", f"Health status not ok: {data}"
        print(f"✓ Health check passed: {data}")


class TestBasketballTeamSearch:
    """Basketball team search endpoint tests"""
    
    def test_search_teams_lakers(self):
        """POST /api/basketball/search-teams finds Lakers"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Lakers"},
            timeout=15
        )
        assert response.status_code == 200, f"Team search failed: {response.status_code}"
        data = response.json()
        assert "teams" in data, f"No teams field in response: {data}"
        teams = data["teams"]
        assert len(teams) > 0, "No teams found for Lakers"
        # Check team structure
        team = teams[0]
        assert "id" in team, "Team missing id field"
        assert "name" in team, "Team missing name field"
        print(f"✓ Team search found {len(teams)} teams: {[t['name'] for t in teams[:3]]}")
    
    def test_search_teams_warriors(self):
        """POST /api/basketball/search-teams finds Warriors"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Warriors"},
            timeout=15
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data.get("teams", [])) > 0, "No teams found for Warriors"
        print(f"✓ Warriors search found: {data['teams'][0]['name']}")
    
    def test_search_teams_empty_query_fails(self):
        """POST /api/basketball/search-teams with empty query returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": ""},
            timeout=10
        )
        assert response.status_code == 400, f"Expected 400 for empty query, got {response.status_code}"
        print("✓ Empty query correctly returns 400")


class TestBasketballPredictionEndpoint:
    """Basketball prediction endpoint tests - core functionality"""
    
    def test_prediction_returns_valid_json(self):
        """POST /api/basketball/predict returns valid JSON with required fields"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60  # Long timeout for AI consensus
        )
        assert response.status_code == 200, f"Prediction failed: {response.status_code} - {response.text[:500]}"
        data = response.json()
        
        # Check required top-level fields
        required_fields = [
            "projectedValue", "recommendation", "confidenceScore", 
            "confidenceLevel", "player", "opponent", "propType", "line"
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
        
        # Validate field types
        assert isinstance(data["projectedValue"], (int, float)), "projectedValue must be numeric"
        assert data["recommendation"] in ["over", "under"], f"Invalid recommendation: {data['recommendation']}"
        assert isinstance(data["confidenceScore"], (int, float)), "confidenceScore must be numeric"
        assert 0 <= data["confidenceScore"] <= 100, f"confidenceScore out of range: {data['confidenceScore']}"
        
        print(f"✓ Prediction returned: proj={data['projectedValue']}, rec={data['recommendation']}, conf={data['confidenceScore']}%")
        return data


class TestAdvancedAnalyticsFields:
    """Tests for the new advanced analytics fields in playerGameLogs"""
    
    @pytest.fixture(scope="class")
    def prediction_response(self):
        """Get a prediction response to test analytics fields"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200, f"Prediction failed: {response.status_code}"
        return response.json()
    
    def test_player_game_logs_exists(self, prediction_response):
        """playerGameLogs field exists in response"""
        assert "playerGameLogs" in prediction_response, "Missing playerGameLogs field"
        logs = prediction_response["playerGameLogs"]
        assert isinstance(logs, dict), f"playerGameLogs should be dict, got {type(logs)}"
        print(f"✓ playerGameLogs present with {len(logs)} fields")
    
    def test_over_rate_field(self, prediction_response):
        """playerGameLogs.overRate is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "overRate" in logs, "Missing overRate field"
        over_rate = logs["overRate"]
        assert isinstance(over_rate, (int, float)), f"overRate must be numeric, got {type(over_rate)}"
        assert 0 <= over_rate <= 100, f"overRate out of range: {over_rate}"
        print(f"✓ overRate = {over_rate}%")
    
    def test_per_min_rate_field(self, prediction_response):
        """playerGameLogs.perMinRate is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "perMinRate" in logs, "Missing perMinRate field"
        per_min = logs["perMinRate"]
        assert isinstance(per_min, (int, float)), f"perMinRate must be numeric, got {type(per_min)}"
        assert per_min >= 0, f"perMinRate should be non-negative: {per_min}"
        print(f"✓ perMinRate = {per_min}/min")
    
    def test_rate_projection_field(self, prediction_response):
        """playerGameLogs.rateProjection is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "rateProjection" in logs, "Missing rateProjection field"
        rate_proj = logs["rateProjection"]
        assert isinstance(rate_proj, (int, float)), f"rateProjection must be numeric, got {type(rate_proj)}"
        assert rate_proj >= 0, f"rateProjection should be non-negative: {rate_proj}"
        print(f"✓ rateProjection = {rate_proj}")
    
    def test_role_field(self, prediction_response):
        """playerGameLogs.role is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "role" in logs, "Missing role field"
        role = logs["role"]
        valid_roles = ["STAR", "STARTER", "ROTATION", "BENCH"]
        assert role in valid_roles, f"Invalid role: {role}, expected one of {valid_roles}"
        print(f"✓ role = {role}")
    
    def test_edge_signal_field(self, prediction_response):
        """playerGameLogs.edgeSignal is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "edgeSignal" in logs, "Missing edgeSignal field"
        edge = logs["edgeSignal"]
        assert isinstance(edge, str), f"edgeSignal must be string, got {type(edge)}"
        assert len(edge) > 0, "edgeSignal should not be empty"
        print(f"✓ edgeSignal = {edge}")
    
    def test_statistical_lean_field(self, prediction_response):
        """playerGameLogs.statisticalLean is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "statisticalLean" in logs, "Missing statisticalLean field"
        lean = logs["statisticalLean"]
        valid_leans = ["OVER", "UNDER", "TOSS-UP"]
        assert lean in valid_leans, f"Invalid statisticalLean: {lean}, expected one of {valid_leans}"
        print(f"✓ statisticalLean = {lean}")
    
    def test_consistency_field(self, prediction_response):
        """playerGameLogs.consistency is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "consistency" in logs, "Missing consistency field"
        consistency = logs["consistency"]
        valid_labels = ["VERY CONSISTENT", "MODERATE", "BOOM-BUST (HIGH VARIANCE)"]
        assert consistency in valid_labels, f"Invalid consistency: {consistency}"
        print(f"✓ consistency = {consistency}")
    
    def test_streak_field(self, prediction_response):
        """playerGameLogs.streak is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "streak" in logs, "Missing streak field"
        streak = logs["streak"]
        assert isinstance(streak, str), f"streak must be string, got {type(streak)}"
        print(f"✓ streak = {streak}")
    
    def test_avg_minutes_field(self, prediction_response):
        """playerGameLogs.avgMinutes is present and valid"""
        logs = prediction_response.get("playerGameLogs", {})
        assert "avgMinutes" in logs, "Missing avgMinutes field"
        avg_min = logs["avgMinutes"]
        assert isinstance(avg_min, (int, float)), f"avgMinutes must be numeric, got {type(avg_min)}"
        assert avg_min >= 0, f"avgMinutes should be non-negative: {avg_min}"
        print(f"✓ avgMinutes = {avg_min}")


class TestProjectionConstraints:
    """Tests for projection value constraints"""
    
    def test_projection_within_30_percent_of_rate(self):
        """projectedValue is within 30% of rateProjection"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        logs = data.get("playerGameLogs", {})
        rate_proj = logs.get("rateProjection", 0)
        projected = data.get("projectedValue", 0)
        
        if rate_proj > 0:
            lower_bound = rate_proj * 0.7
            upper_bound = rate_proj * 1.3
            assert lower_bound <= projected <= upper_bound, \
                f"projectedValue {projected} not within 30% of rateProjection {rate_proj} (bounds: {lower_bound:.1f}-{upper_bound:.1f})"
            print(f"✓ projectedValue {projected} within 30% of rateProjection {rate_proj}")
        else:
            print(f"⚠ rateProjection is 0, skipping constraint check")


class TestConfidenceCapping:
    """Tests for confidence capping on coin-flip scenarios"""
    
    def test_coin_flip_confidence_capped(self):
        """Confidence is capped at 52 when edgeSignal contains COIN FLIP"""
        # We need to find a scenario that triggers COIN FLIP
        # This happens when z-score < 0.3 (line very close to average)
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,  # May or may not trigger coin flip depending on actual stats
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        logs = data.get("playerGameLogs", {})
        edge_signal = logs.get("edgeSignal", "")
        confidence = data.get("confidenceScore", 50)
        
        if "COIN FLIP" in edge_signal:
            assert confidence <= 52, \
                f"Confidence {confidence} should be capped at 52 for COIN FLIP edge signal"
            print(f"✓ COIN FLIP detected, confidence correctly capped at {confidence}")
        else:
            print(f"⚠ Edge signal is '{edge_signal}', not COIN FLIP - constraint not applicable")


class TestRecentSamples:
    """Tests for recentSamples array population"""
    
    def test_recent_samples_populated(self):
        """recentSamples array is populated with game data"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        samples = data.get("recentSamples", [])
        assert isinstance(samples, list), f"recentSamples should be list, got {type(samples)}"
        assert len(samples) > 0, "recentSamples should not be empty"
        
        # Check first sample has required fields
        sample = samples[0]
        required_fields = ["date", "opponent", "value", "minutesPlayed", "venue", "result"]
        for field in required_fields:
            assert field in sample, f"Sample missing required field: {field}"
        
        print(f"✓ recentSamples has {len(samples)} games, first: {sample['date']} vs {sample['opponent']} = {sample['value']}")
    
    def test_recent_samples_fields_valid(self):
        """recentSamples fields have valid values"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        
        samples = data.get("recentSamples", [])
        if samples:
            sample = samples[0]
            # Validate field types
            assert isinstance(sample.get("value"), (int, float)), "value should be numeric"
            assert sample.get("venue") in ["home", "away", ""], f"Invalid venue: {sample.get('venue')}"
            assert sample.get("result") in ["W", "L", "D", ""], f"Invalid result: {sample.get('result')}"
            print(f"✓ Sample fields validated: value={sample['value']}, venue={sample['venue']}, result={sample['result']}")


class TestResponseTime:
    """Tests for response time constraints"""
    
    def test_response_within_55_seconds(self):
        """Response completes within 55 seconds (K8s proxy timeout limit)"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        elapsed = time.time() - start_time
        
        assert response.status_code == 200, f"Prediction failed: {response.status_code}"
        assert elapsed < 55, f"Response took {elapsed:.1f}s, exceeds 55s limit"
        print(f"✓ Response completed in {elapsed:.1f}s (under 55s limit)")


class TestMultiplePropTypes:
    """Tests for different prop types"""
    
    def test_pts_reb_ast_prop(self):
        """pts_reb_ast prop type works correctly"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "pts_reb_ast",
            "line": 40.5,
            "venue": "home"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200, f"pts_reb_ast prediction failed: {response.status_code}"
        data = response.json()
        # Response uses display label from BBALL_PROP_LABELS
        assert data.get("propType") in ["pts_reb_ast", "Pts+Reb+Ast"], f"Unexpected propType: {data.get('propType')}"
        print(f"✓ pts_reb_ast prediction: proj={data['projectedValue']}, rec={data['recommendation']}")
    
    def test_rebounds_prop(self):
        """rebounds prop type works correctly"""
        payload = {
            "playerName": TEST_PLAYER_NAME,
            "teamId": TEST_TEAM_ID,
            "teamName": "Los Angeles Lakers",
            "opponentId": TEST_OPPONENT_ID,
            "opponentName": "Golden State Warriors",
            "propType": "rebounds",
            "line": 7.5,
            "venue": "away"
        }
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=60
        )
        assert response.status_code == 200, f"rebounds prediction failed: {response.status_code}"
        data = response.json()
        # Response uses display label from BBALL_PROP_LABELS
        assert data.get("propType") in ["rebounds", "Rebounds"], f"Unexpected propType: {data.get('propType')}"
        print(f"✓ rebounds prediction: proj={data['projectedValue']}, rec={data['recommendation']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
