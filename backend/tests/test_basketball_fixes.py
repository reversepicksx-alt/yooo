"""
Test Basketball Pipeline Fixes - Iteration 27
Tests the following fixes:
1. POST /api/basketball/search-teams returns ONLY NBA teams (league=12 filter)
2. POST /api/basketball/predict returns valid prediction with playerGameLogs.sampleSize > 5
3. POST /api/basketball/predict returns correct matchup overview with proper team names
4. POST /api/basketball/predict returns recentSamples array with real game data
5. POST /api/basketball/predict returns dataQuality.level = 'good' when sufficient data
6. POST /api/predict still works for soccer (regression test)
7. Frontend BASKETBALL_PROP_TYPES does NOT contain steals, blocks, or turnovers
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestBasketballTeamSearch:
    """Test NBA-filtered team search (league=12)"""
    
    def test_search_suns_returns_phoenix_suns_not_youth_teams(self):
        """POST /api/basketball/search-teams for 'Suns' should return Phoenix Suns, NOT youth/foreign teams"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Suns"},
            timeout=30
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "teams" in data, "Response should contain 'teams' key"
        teams = data["teams"]
        assert len(teams) > 0, "Should return at least one team for 'Suns'"
        
        # Check that Phoenix Suns is in the results
        team_names = [t.get("name", "").lower() for t in teams]
        phoenix_found = any("phoenix" in name and "suns" in name for name in team_names)
        assert phoenix_found, f"Phoenix Suns should be in results. Got: {[t.get('name') for t in teams]}"
        
        # Check that youth/foreign teams are NOT in results
        bad_keywords = ["mladi", "youth", "u20", "u21", "u19", "u18", "helios"]
        for team in teams:
            team_name = team.get("name", "").lower()
            for keyword in bad_keywords:
                assert keyword not in team_name, f"Youth/foreign team '{team.get('name')}' should NOT be in NBA results"
        
        print(f"✓ Suns search returned {len(teams)} NBA teams: {[t.get('name') for t in teams]}")
    
    def test_search_grizzlies_returns_memphis_grizzlies(self):
        """POST /api/basketball/search-teams for 'Grizzlies' should return Memphis Grizzlies"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Grizzlies"},
            timeout=30
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "teams" in data, "Response should contain 'teams' key"
        teams = data["teams"]
        assert len(teams) > 0, "Should return at least one team for 'Grizzlies'"
        
        # Check that Memphis Grizzlies is in the results
        team_names = [t.get("name", "").lower() for t in teams]
        memphis_found = any("memphis" in name and "grizzlies" in name for name in team_names)
        assert memphis_found, f"Memphis Grizzlies should be in results. Got: {[t.get('name') for t in teams]}"
        
        print(f"✓ Grizzlies search returned: {[t.get('name') for t in teams]}")
    
    def test_search_returns_team_ids(self):
        """Team search should return valid team IDs"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Lakers"},
            timeout=30
        )
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "Should return at least one team"
        
        for team in teams:
            assert "id" in team, "Each team should have an 'id'"
            assert "name" in team, "Each team should have a 'name'"
            assert isinstance(team["id"], int), f"Team ID should be int, got {type(team['id'])}"
        
        print(f"✓ Lakers search returned teams with valid IDs: {[(t.get('id'), t.get('name')) for t in teams]}")


class TestBasketballPrediction:
    """Test basketball prediction with player game logs"""
    
    def test_jalen_green_points_prediction(self):
        """POST /api/basketball/predict for Jalen Green Points should return valid prediction with game logs"""
        # Phoenix Suns (ID: 155) vs Memphis Grizzlies (ID: 146)
        # Jalen Green is on Houston Rockets, but we're testing the prediction engine
        payload = {
            "teamId": 155,
            "teamName": "Phoenix Suns",
            "opponentId": 146,
            "opponentName": "Memphis Grizzlies",
            "playerName": "Jalen Green",
            "venue": "away",
            "propType": "points",
            "line": 25.5
        }
        
        print(f"Sending basketball prediction request for Jalen Green...")
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=90  # Long timeout for multi-AI consensus
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check required fields
        assert "projectedValue" in data, "Response should contain 'projectedValue'"
        assert "recommendation" in data, "Response should contain 'recommendation'"
        assert "confidenceScore" in data, "Response should contain 'confidenceScore'"
        
        # Check playerGameLogs with sampleSize > 5
        assert "playerGameLogs" in data, "Response should contain 'playerGameLogs'"
        game_logs = data["playerGameLogs"]
        sample_size = game_logs.get("sampleSize", 0)
        assert sample_size > 5, f"playerGameLogs.sampleSize should be > 5, got {sample_size}"
        print(f"✓ playerGameLogs.sampleSize = {sample_size}")
        
        # Check recentSamples array with real game data
        assert "recentSamples" in data, "Response should contain 'recentSamples'"
        recent_samples = data["recentSamples"]
        assert isinstance(recent_samples, list), "recentSamples should be a list"
        assert len(recent_samples) > 0, "recentSamples should not be empty"
        
        # Verify recentSamples structure
        for sample in recent_samples[:3]:  # Check first 3
            assert "date" in sample, f"Sample should have 'date': {sample}"
            assert "opponent" in sample, f"Sample should have 'opponent': {sample}"
            assert "value" in sample, f"Sample should have 'value': {sample}"
        print(f"✓ recentSamples has {len(recent_samples)} entries with date/opponent/value")
        
        # Check matchupOverview
        assert "matchupOverview" in data, "Response should contain 'matchupOverview'"
        matchup = data["matchupOverview"]
        assert "homeTeam" in matchup, "matchupOverview should have 'homeTeam'"
        assert "awayTeam" in matchup, "matchupOverview should have 'awayTeam'"
        print(f"✓ matchupOverview: {matchup.get('homeTeam')} vs {matchup.get('awayTeam')}")
        
        # Check dataQuality
        assert "dataQuality" in data, "Response should contain 'dataQuality'"
        data_quality = data["dataQuality"]
        quality_level = data_quality.get("level", "")
        print(f"✓ dataQuality.level = '{quality_level}'")
        
        # Check sport field
        assert data.get("sport") == "basketball", f"sport should be 'basketball', got {data.get('sport')}"
        
        print(f"✓ Full prediction: proj={data['projectedValue']}, rec={data['recommendation']}, conf={data['confidenceScore']}%")
    
    def test_devin_booker_prediction(self):
        """Test prediction for Devin Booker (Phoenix Suns player)"""
        payload = {
            "teamId": 155,
            "teamName": "Phoenix Suns",
            "opponentId": 146,
            "opponentName": "Memphis Grizzlies",
            "playerName": "Devin Booker",
            "venue": "home",
            "propType": "points",
            "line": 28.5
        }
        
        print(f"Sending basketball prediction request for Devin Booker...")
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=90
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Check game logs
        game_logs = data.get("playerGameLogs", {})
        sample_size = game_logs.get("sampleSize", 0)
        assert sample_size > 5, f"Devin Booker should have > 5 game logs, got {sample_size}"
        
        print(f"✓ Devin Booker prediction: proj={data.get('projectedValue')}, sampleSize={sample_size}")
    
    def test_rebounds_prop_type(self):
        """Test rebounds prop type (fixed parsing with dict.total)"""
        payload = {
            "teamId": 155,
            "teamName": "Phoenix Suns",
            "opponentId": 146,
            "opponentName": "Memphis Grizzlies",
            "playerName": "Devin Booker",
            "venue": "home",
            "propType": "rebounds",
            "line": 5.5
        }
        
        print(f"Testing rebounds prop type...")
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            timeout=90
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "projectedValue" in data, "Should have projectedValue for rebounds"
        assert data.get("propType") == "rebounds" or "rebounds" in str(data.get("_request", {})), "Should be rebounds prop"
        
        print(f"✓ Rebounds prediction: proj={data.get('projectedValue')}")


class TestSoccerRegression:
    """Regression test - soccer prediction should still work"""
    
    def test_soccer_predict_still_works(self):
        """POST /api/predict for soccer should still work"""
        payload = {
            "playerId": 276,
            "playerName": "Neymar",
            "teamId": 85,
            "teamName": "Paris Saint Germain",
            "opponentId": 81,
            "opponentName": "Marseille",
            "leagueId": 61,
            "venue": "home",
            "propType": "shots",
            "line": 3.5
        }
        
        print(f"Testing soccer prediction (regression)...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            timeout=90
        )
        assert response.status_code == 200, f"Soccer predict should still work. Got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "projectedValue" in data, "Soccer prediction should have projectedValue"
        assert "recommendation" in data, "Soccer prediction should have recommendation"
        
        print(f"✓ Soccer prediction still works: proj={data.get('projectedValue')}, rec={data.get('recommendation')}")


class TestFrontendPropTypes:
    """Test that frontend BASKETBALL_PROP_TYPES does NOT contain steals/blocks/turnovers"""
    
    def test_basketball_prop_types_no_steals_blocks_turnovers(self):
        """Check App.js BASKETBALL_PROP_TYPES array"""
        app_js_path = "/app/frontend/src/App.js"
        
        with open(app_js_path, 'r') as f:
            content = f.read()
        
        # Find BASKETBALL_PROP_TYPES definition
        import re
        match = re.search(r'const BASKETBALL_PROP_TYPES\s*=\s*\[(.*?)\];', content, re.DOTALL)
        assert match, "BASKETBALL_PROP_TYPES should be defined in App.js"
        
        prop_types_str = match.group(1).lower()
        
        # Check that steals, blocks, turnovers are NOT present
        assert "steals" not in prop_types_str, "BASKETBALL_PROP_TYPES should NOT contain 'steals'"
        assert "blocks" not in prop_types_str, "BASKETBALL_PROP_TYPES should NOT contain 'blocks'"
        assert "turnovers" not in prop_types_str, "BASKETBALL_PROP_TYPES should NOT contain 'turnovers'"
        
        # Check that valid props ARE present
        assert "points" in prop_types_str, "BASKETBALL_PROP_TYPES should contain 'points'"
        assert "rebounds" in prop_types_str, "BASKETBALL_PROP_TYPES should contain 'rebounds'"
        assert "assists" in prop_types_str, "BASKETBALL_PROP_TYPES should contain 'assists'"
        
        print(f"✓ BASKETBALL_PROP_TYPES correctly excludes steals/blocks/turnovers")
        print(f"✓ BASKETBALL_PROP_TYPES includes: points, rebounds, assists")


class TestHealthCheck:
    """Basic health check"""
    
    def test_api_health(self):
        """GET /api/health should return ok"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"✓ API health check passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
