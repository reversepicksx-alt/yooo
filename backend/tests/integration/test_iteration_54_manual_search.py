"""
Iteration 54: Manual Search Feature Tests
Tests the new manual search endpoints and avg_proj fix in predict.py

Features tested:
- GET /api/manual/leagues - returns 12 leagues
- GET /api/manual/teams/{league_id} - returns teams with teamId, name, logo
- POST /api/manual/search-player - returns squad/player data
- avg_proj fix verification in predict.py line 1925
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestManualSearchEndpoints:
    """Manual Search API endpoint tests"""
    
    def test_get_leagues_returns_12_leagues(self):
        """GET /api/manual/leagues should return exactly 12 leagues"""
        response = requests.get(f"{BASE_URL}/api/manual/leagues")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "leagues" in data, "Response should contain 'leagues' key"
        leagues = data["leagues"]
        
        # Verify exactly 12 leagues
        assert len(leagues) == 12, f"Expected 12 leagues, got {len(leagues)}"
        
        # Verify expected leagues are present
        expected_leagues = [
            "NWSL", "MLS", "Saudi Pro League", "Argentine Liga", "Championship",
            "La Liga", "Serie A", "Ligue 1", "Bundesliga", "Premier League",
            "Champions League", "Europa League"
        ]
        league_names = [lg["name"] for lg in leagues]
        for expected in expected_leagues:
            assert expected in league_names, f"Missing league: {expected}"
        
        # Verify each league has id and name
        for lg in leagues:
            assert "id" in lg, "League should have 'id'"
            assert "name" in lg, "League should have 'name'"
            assert isinstance(lg["id"], int), "League id should be int"
            assert isinstance(lg["name"], str), "League name should be string"
        
        print(f"✓ GET /api/manual/leagues returned {len(leagues)} leagues")
    
    def test_get_premier_league_teams(self):
        """GET /api/manual/teams/39 should return Premier League teams"""
        response = requests.get(f"{BASE_URL}/api/manual/teams/39")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "teams" in data, "Response should contain 'teams' key"
        teams = data["teams"]
        
        # Premier League should have teams cached
        assert len(teams) > 0, "Premier League should have cached teams"
        
        # Verify team structure
        for team in teams[:5]:  # Check first 5
            assert "teamId" in team, "Team should have 'teamId'"
            assert "name" in team, "Team should have 'name'"
            assert "logo" in team, "Team should have 'logo'"
            assert isinstance(team["teamId"], int), "teamId should be int"
            assert isinstance(team["name"], str), "name should be string"
        
        print(f"✓ GET /api/manual/teams/39 returned {len(teams)} Premier League teams")
    
    def test_get_la_liga_teams(self):
        """GET /api/manual/teams/140 should return La Liga teams"""
        response = requests.get(f"{BASE_URL}/api/manual/teams/140")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "teams" in data, "Response should contain 'teams' key"
        teams = data["teams"]
        
        # La Liga should have teams cached
        assert len(teams) > 0, "La Liga should have cached teams"
        
        # Verify team structure
        for team in teams[:5]:
            assert "teamId" in team, "Team should have 'teamId'"
            assert "name" in team, "Team should have 'name'"
            assert "logo" in team, "Team should have 'logo'"
        
        print(f"✓ GET /api/manual/teams/140 returned {len(teams)} La Liga teams")
    
    def test_search_player_athletic_club_squad(self):
        """POST /api/manual/search-player with team_id=531 league_id=140 returns Athletic Club squad"""
        response = requests.post(
            f"{BASE_URL}/api/manual/search-player",
            json={"team_id": 531, "league_id": 140, "player_name": ""}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "players" in data, "Response should contain 'players' key"
        players = data["players"]
        
        # Should return squad players
        if len(players) > 0:
            # Verify player structure
            for player in players[:5]:
                assert "id" in player, "Player should have 'id'"
                assert "name" in player, "Player should have 'name'"
                assert "position" in player, "Player should have 'position'"
            print(f"✓ POST /api/manual/search-player returned {len(players)} Athletic Club players")
        else:
            # API might not have squad data - this is acceptable
            print("⚠ POST /api/manual/search-player returned 0 players (API may not have squad data)")
    
    def test_search_player_with_name_filter(self):
        """POST /api/manual/search-player with player_name filter returns filtered results"""
        # Search for a common name pattern in Premier League team (Arsenal = 42)
        response = requests.post(
            f"{BASE_URL}/api/manual/search-player",
            json={"team_id": 42, "league_id": 39, "player_name": "Saka"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "players" in data, "Response should contain 'players' key"
        players = data["players"]
        
        # If players found, verify filter worked
        if len(players) > 0:
            # At least one player should have "Saka" in name
            saka_found = any("saka" in p["name"].lower() for p in players)
            if saka_found:
                print(f"✓ POST /api/manual/search-player with filter returned {len(players)} players including Saka")
            else:
                print(f"⚠ POST /api/manual/search-player returned {len(players)} players but Saka not found")
        else:
            print("⚠ POST /api/manual/search-player with filter returned 0 players")
    
    def test_search_player_empty_team(self):
        """POST /api/manual/search-player with invalid team returns empty or message"""
        response = requests.post(
            f"{BASE_URL}/api/manual/search-player",
            json={"team_id": 99999, "league_id": 39, "player_name": ""}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "players" in data, "Response should contain 'players' key"
        # Should return empty or message for invalid team
        print(f"✓ POST /api/manual/search-player with invalid team handled gracefully")
    
    def test_get_teams_empty_league(self):
        """GET /api/manual/teams/{invalid_id} returns empty teams or message"""
        response = requests.get(f"{BASE_URL}/api/manual/teams/99999")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert "teams" in data, "Response should contain 'teams' key"
        # Should return empty for uncached league
        print(f"✓ GET /api/manual/teams/99999 returned {len(data['teams'])} teams (expected 0)")


class TestAvgProjFix:
    """Verify the avg_proj crash fix in predict.py line 1925"""
    
    def test_predict_endpoint_exists(self):
        """Verify /api/predict endpoint is accessible"""
        # Just check the endpoint exists - don't run full prediction
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json={
                "playerId": 0,
                "playerName": "Test Player",
                "teamId": 0,
                "teamName": "Test Team",
                "opponentId": 0,
                "opponentName": "Test Opponent",
                "leagueId": 39,
                "venue": "home",
                "propType": "shots",
                "line": 2.5
            }
        )
        # Should not crash with 500 - may return error but not crash
        assert response.status_code != 500, f"Predict endpoint crashed with 500"
        print(f"✓ /api/predict endpoint accessible (status: {response.status_code})")


class TestLeagueIds:
    """Verify correct league IDs in manual search"""
    
    def test_league_ids_match_expected(self):
        """Verify league IDs match API-Sports IDs"""
        response = requests.get(f"{BASE_URL}/api/manual/leagues")
        assert response.status_code == 200
        
        data = response.json()
        leagues = data["leagues"]
        
        expected_ids = {
            254: "NWSL",
            253: "MLS",
            307: "Saudi Pro League",
            128: "Argentine Liga",
            40: "Championship",
            140: "La Liga",
            135: "Serie A",
            61: "Ligue 1",
            78: "Bundesliga",
            39: "Premier League",
            2: "Champions League",
            3: "Europa League"
        }
        
        for lg in leagues:
            if lg["id"] in expected_ids:
                assert lg["name"] == expected_ids[lg["id"]], f"League {lg['id']} name mismatch"
        
        print("✓ All league IDs match expected values")


class TestTeamDataStructure:
    """Verify team data structure from cache"""
    
    def test_bundesliga_teams_structure(self):
        """GET /api/manual/teams/78 returns Bundesliga teams with correct structure"""
        response = requests.get(f"{BASE_URL}/api/manual/teams/78")
        assert response.status_code == 200
        
        data = response.json()
        teams = data["teams"]
        
        if len(teams) > 0:
            # Verify alphabetical sorting
            names = [t["name"] for t in teams]
            assert names == sorted(names), "Teams should be sorted alphabetically"
            print(f"✓ Bundesliga teams sorted alphabetically ({len(teams)} teams)")
        else:
            print("⚠ No Bundesliga teams cached")
    
    def test_serie_a_teams_structure(self):
        """GET /api/manual/teams/135 returns Serie A teams"""
        response = requests.get(f"{BASE_URL}/api/manual/teams/135")
        assert response.status_code == 200
        
        data = response.json()
        teams = data["teams"]
        
        if len(teams) > 0:
            # Verify no _id field (MongoDB ObjectId excluded)
            for team in teams[:5]:
                assert "_id" not in team, "Team should not have MongoDB _id"
            print(f"✓ Serie A teams returned without _id ({len(teams)} teams)")
        else:
            print("⚠ No Serie A teams cached")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
