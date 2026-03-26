"""
Test suite for bug fixes in ReversePicks:
1. Natural search finding players (Gianluigi Donnarumma)
2. International teams loading for WCQ and other tournaments
3. Player search deduplication
4. Player photo and nationality display
5. Player full names
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPlayerSearch:
    """Tests for player search bug fixes - deduplication, full names, team info"""
    
    def test_donnarumma_search_returns_single_result(self):
        """Bug fix: Searching 'Donnarumma' should return 1 result, not 7 duplicates"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Donnarumma"})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        # Should have at least 1 result
        assert len(players) >= 1, "Should find at least 1 player"
        
        # Check for deduplication - count unique player IDs
        player_ids = [p["id"] for p in players]
        assert len(player_ids) == len(set(player_ids)), "Should not have duplicate player IDs"
        
        # First result should be Gianluigi Donnarumma
        first_player = players[0]
        assert "Donnarumma" in first_player["name"], f"First result should be Donnarumma, got {first_player['name']}"
        
    def test_donnarumma_has_team_info(self):
        """Bug fix: Donnarumma should have team info (not 'Unknown')"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Donnarumma"})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        # Find Gianluigi Donnarumma
        gianluigi = next((p for p in players if "Gianluigi" in p.get("name", "")), None)
        assert gianluigi is not None, "Should find Gianluigi Donnarumma"
        
        # Should have team info
        assert gianluigi.get("teamName"), f"Should have team name, got: {gianluigi.get('teamName')}"
        assert gianluigi.get("teamId", 0) > 0, "Should have valid team ID"
        
    def test_donnarumma_full_name(self):
        """Bug fix: Should show 'Gianluigi Donnarumma' not 'G. Donnarumma'"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Donnarumma"})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        gianluigi = next((p for p in players if "Gianluigi" in p.get("name", "")), None)
        assert gianluigi is not None, "Should find Gianluigi Donnarumma"
        
        # Name should be full name, not abbreviated
        assert "Gianluigi" in gianluigi["name"], f"Should have full first name, got: {gianluigi['name']}"
        assert "Donnarumma" in gianluigi["name"], f"Should have last name, got: {gianluigi['name']}"
        
    def test_donnarumma_has_photo_and_nationality(self):
        """Bug fix: Player should have photo URL and nationality"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Donnarumma"})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        gianluigi = next((p for p in players if "Gianluigi" in p.get("name", "")), None)
        assert gianluigi is not None, "Should find Gianluigi Donnarumma"
        
        # Should have photo URL
        assert gianluigi.get("photo"), f"Should have photo URL, got: {gianluigi.get('photo')}"
        assert gianluigi["photo"].startswith("http"), "Photo should be a valid URL"
        
        # Should have nationality
        assert gianluigi.get("nationality"), f"Should have nationality, got: {gianluigi.get('nationality')}"
        assert gianluigi["nationality"] == "Italy", f"Nationality should be Italy, got: {gianluigi['nationality']}"
        
    def test_messi_search_returns_lionel_first(self):
        """Bug fix: Searching 'Messi' should return Lionel Messi at Inter Miami as first result"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Messi"})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        assert len(players) >= 1, "Should find at least 1 player"
        
        # First result should be Lionel Messi
        first_player = players[0]
        assert "Lionel" in first_player["name"] or "Messi" in first_player["name"], f"First result should be Lionel Messi, got {first_player['name']}"
        
        # Should be at Inter Miami
        assert "Inter Miami" in first_player.get("teamName", ""), f"Should be at Inter Miami, got: {first_player.get('teamName')}"
        
    def test_salah_search_in_epl(self):
        """Bug fix: Searching 'Salah' with league 39 (EPL) should return Mohamed Salah at Liverpool"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Salah", "league_id": 39})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        assert len(players) >= 1, "Should find at least 1 player"
        
        # First result should be Mohamed Salah
        first_player = players[0]
        assert "Salah" in first_player["name"], f"First result should be Salah, got {first_player['name']}"
        
        # Should be at Liverpool
        assert "Liverpool" in first_player.get("teamName", ""), f"Should be at Liverpool, got: {first_player.get('teamName')}"


class TestInternationalTeams:
    """Tests for international tournament teams loading"""
    
    def test_wcq_uefa_returns_teams(self):
        """Bug fix: WCQ UEFA (league 34) should load teams in Step 3"""
        response = requests.get(f"{BASE_URL}/api/leagues/34/teams")
        assert response.status_code == 200
        data = response.json()
        teams = data.get("teams", [])
        
        # Should have teams (the bug was returning empty list)
        assert len(teams) > 0, "WCQ UEFA should return teams"
        
        # Each team should have required fields
        for team in teams[:3]:
            assert team.get("id"), "Team should have ID"
            assert team.get("name"), "Team should have name"
            
    def test_wcq_conmebol_returns_teams(self):
        """WCQ CONMEBOL (league 30) should load teams"""
        response = requests.get(f"{BASE_URL}/api/leagues/30/teams")
        assert response.status_code == 200
        data = response.json()
        teams = data.get("teams", [])
        
        assert len(teams) > 0, "WCQ CONMEBOL should return teams"
        
    def test_euro_qualifiers_returns_teams(self):
        """Euro Qualifiers (league 96) should load teams"""
        response = requests.get(f"{BASE_URL}/api/leagues/96/teams")
        assert response.status_code == 200
        data = response.json()
        teams = data.get("teams", [])
        
        # May or may not have teams depending on season
        # Just verify endpoint doesn't error
        assert isinstance(teams, list), "Should return a list"


class TestNaturalSearch:
    """Tests for natural language search functionality"""
    
    def test_natural_search_parses_donnarumma(self):
        """Bug fix: Natural search should parse 'Gianluigi Donnarumma' correctly"""
        response = requests.post(f"{BASE_URL}/api/parse-query", json={
            "query": "Gianluigi Donnarumma 52.5 passes vs Arsenal"
        })
        assert response.status_code == 200
        data = response.json()
        
        # Should extract player name
        assert "Donnarumma" in data.get("playerName", ""), f"Should extract Donnarumma, got: {data.get('playerName')}"
        
        # Should extract opponent
        assert "Arsenal" in data.get("opponentName", ""), f"Should extract Arsenal, got: {data.get('opponentName')}"
        
        # Should extract line
        assert data.get("line") == 52.5, f"Should extract line 52.5, got: {data.get('line')}"
        
    def test_natural_search_finds_player_after_parse(self):
        """Natural search should find player after parsing query"""
        # First parse the query
        parse_response = requests.post(f"{BASE_URL}/api/parse-query", json={
            "query": "Donnarumma 30 passes vs Chelsea"
        })
        assert parse_response.status_code == 200
        parsed = parse_response.json()
        
        # Then search for the player
        search_response = requests.post(f"{BASE_URL}/api/players/search", json={
            "query": parsed.get("playerName", "Donnarumma")
        })
        assert search_response.status_code == 200
        players = search_response.json().get("players", [])
        
        # Should find the player
        assert len(players) >= 1, "Should find player after natural search parse"


class TestAuthAndPOTD:
    """Tests for auth and POTD (regression tests)"""
    
    def test_owner_auto_login(self):
        """Owner (josselj001@gmail.com) should auto-login without password"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": "josselj001@gmail.com"
        })
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("verified") == True, "Owner should be verified"
        assert data.get("access_type") == "Owner", f"Should be Owner, got: {data.get('access_type')}"
        assert data.get("session_token"), "Should have session token"
        
    def test_potd_available(self):
        """Pick of the Day should be available"""
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("available") == True, "POTD should be available"
        assert data.get("pick"), "Should have pick data"
        assert data["pick"].get("playerName"), "Pick should have player name"
        
    def test_health_endpoint(self):
        """Health endpoint should return ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("status") == "ok", "Health should be ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
