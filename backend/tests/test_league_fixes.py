"""
Test suite for ReversePicks league ID fixes and player search updates.
Tests: International team categories, player photo removal, POTD loading, nationality display.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestInternationalLeagues:
    """Tests for corrected international league IDs"""
    
    def test_wcq_uefa_returns_european_teams(self):
        """WCQ UEFA (league 32) should return European teams like Belgium, France, Spain"""
        response = requests.get(f"{BASE_URL}/api/leagues/32/teams")
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "WCQ UEFA should return teams"
        
        team_names = [t["name"] for t in teams]
        european_teams = ["Belgium", "France", "Croatia", "Sweden", "Spain", "England", "Germany", "Italy", "Portugal", "Netherlands"]
        south_american = ["Brazil", "Argentina", "Uruguay", "Colombia", "Chile"]
        
        # Should have European teams
        has_european = any(team in team_names for team in european_teams)
        assert has_european, f"WCQ UEFA should have European teams, got: {team_names[:5]}"
        
        # Should NOT have South American teams
        has_south_american = any(team in team_names for team in south_american)
        assert not has_south_american, f"WCQ UEFA should NOT have South American teams, got: {team_names[:5]}"
    
    def test_wcq_conmebol_returns_south_american_teams(self):
        """WCQ CONMEBOL (league 34) should return South American teams like Brazil, Argentina"""
        response = requests.get(f"{BASE_URL}/api/leagues/34/teams")
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "WCQ CONMEBOL should return teams"
        
        team_names = [t["name"] for t in teams]
        south_american = ["Brazil", "Argentina", "Uruguay", "Colombia", "Peru", "Chile", "Ecuador", "Paraguay", "Bolivia", "Venezuela"]
        
        has_south_american = any(team in team_names for team in south_american)
        assert has_south_american, f"WCQ CONMEBOL should have South American teams, got: {team_names[:5]}"
    
    def test_wcq_afc_returns_asian_teams(self):
        """WCQ AFC (league 30) should return Asian teams like Japan, South Korea, Australia"""
        response = requests.get(f"{BASE_URL}/api/leagues/30/teams")
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "WCQ AFC should return teams"
        
        team_names = [t["name"] for t in teams]
        asian_teams = ["Japan", "South Korea", "Australia", "Iran", "Saudi Arabia", "Qatar", "China", "Iraq", "UAE"]
        
        has_asian = any(team in team_names for team in asian_teams)
        assert has_asian, f"WCQ AFC should have Asian teams, got: {team_names[:5]}"
    
    def test_wcq_caf_returns_african_teams(self):
        """WCQ CAF (league 29) should return African teams like Nigeria, Senegal, Morocco"""
        response = requests.get(f"{BASE_URL}/api/leagues/29/teams")
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "WCQ CAF should return teams"
        
        team_names = [t["name"] for t in teams]
        african_teams = ["Nigeria", "Senegal", "Morocco", "Egypt", "Tunisia", "Algeria", "Cameroon", "Ghana", "Ivory Coast"]
        
        has_african = any(team in team_names for team in african_teams)
        assert has_african, f"WCQ CAF should have African teams, got: {team_names[:5]}"
    
    def test_euro_qualifiers_returns_european_teams(self):
        """Euro Qualifiers (league 960) should return European teams"""
        response = requests.get(f"{BASE_URL}/api/leagues/960/teams")
        assert response.status_code == 200
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "Euro Qualifiers should return teams"
        
        team_names = [t["name"] for t in teams]
        european_teams = ["Belgium", "France", "Croatia", "Sweden", "Spain", "England", "Germany", "Italy", "Portugal", "Netherlands"]
        
        has_european = any(team in team_names for team in european_teams)
        assert has_european, f"Euro Qualifiers should have European teams, got: {team_names[:5]}"


class TestPlayerSearch:
    """Tests for player search with nationality and team info"""
    
    def test_donnarumma_search_returns_full_name(self):
        """Search 'Donnarumma' should return Gianluigi Donnarumma with full name"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Donnarumma"})
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        assert len(players) > 0, "Should find Donnarumma"
        
        first_player = players[0]
        assert "Gianluigi" in first_player["name"], f"Should have full name, got: {first_player['name']}"
        assert first_player["nationality"] == "Italy", f"Should be Italian, got: {first_player.get('nationality')}"
    
    def test_messi_search_returns_inter_miami(self):
        """Search 'Messi' should return Lionel Messi at Inter Miami"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Messi"})
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        assert len(players) > 0, "Should find Messi"
        
        # Find Lionel Messi (full name includes "Lionel Andrés")
        lionel = next((p for p in players if "Lionel" in p["name"] or p["id"] == 154), None)
        assert lionel is not None, f"Should find Lionel Messi, got: {[p['name'] for p in players[:3]]}"
        assert lionel["teamName"] == "Inter Miami", f"Should be at Inter Miami, got: {lionel.get('teamName')}"
        assert lionel["nationality"] == "Argentina", f"Should be Argentine, got: {lionel.get('nationality')}"
    
    def test_salah_search_with_epl_league(self):
        """Search 'Salah' in EPL (league 39) should return Mohamed Salah at Liverpool"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Salah", "league_id": 39})
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        assert len(players) > 0, "Should find Salah"
        
        first_player = players[0]
        assert "Mohamed" in first_player["name"] or "Salah" in first_player["name"]
        assert first_player["teamName"] == "Liverpool", f"Should be at Liverpool, got: {first_player.get('teamName')}"
        assert first_player["nationality"] == "Egypt", f"Should be Egyptian, got: {first_player.get('nationality')}"
    
    def test_player_search_returns_photo_url(self):
        """Player search should return photo URL (backend still provides it, frontend doesn't display)"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "Salah"})
        assert response.status_code == 200
        
        data = response.json()
        players = data.get("players", [])
        assert len(players) > 0
        
        first_player = players[0]
        assert "photo" in first_player, "Should have photo field"
        assert first_player["photo"].startswith("http"), f"Photo should be URL, got: {first_player.get('photo')}"


class TestOwnerAuth:
    """Tests for owner auto-login"""
    
    def test_owner_auto_login_bypasses_password(self):
        """Owner email josselj001@gmail.com should auto-login without password"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": "josselj001@gmail.com"})
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("verified") == True, "Owner should be verified"
        assert data.get("access_type") == "Owner", f"Should be Owner, got: {data.get('access_type')}"
        assert "session_token" in data, "Should have session token"


class TestPOTD:
    """Tests for Pick of the Day"""
    
    def test_potd_endpoint_returns_data(self):
        """POTD endpoint should return available pick"""
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day")
        assert response.status_code == 200
        
        data = response.json()
        assert "date" in data, "Should have date"
        
        if data.get("available"):
            pick = data.get("pick", {})
            assert "playerName" in pick, "Should have playerName"
            assert "propType" in pick, "Should have propType"
            assert "recommendation" in pick, "Should have recommendation"


class TestHealthAndLeagues:
    """Basic health and leagues tests"""
    
    def test_health_endpoint(self):
        """Health endpoint should return ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("status") == "ok"
    
    def test_leagues_endpoint(self):
        """Leagues endpoint should return supported leagues with correct IDs"""
        response = requests.get(f"{BASE_URL}/api/leagues")
        assert response.status_code == 200
        
        data = response.json()
        leagues = data.get("leagues", [])
        assert len(leagues) > 20, "Should have 20+ leagues"
        
        # Check for key leagues with CORRECT IDs
        league_ids = [l["id"] for l in leagues]
        assert 39 in league_ids, "Should have Premier League (39)"
        assert 32 in league_ids, "Should have WCQ UEFA (32)"
        assert 34 in league_ids, "Should have WCQ CONMEBOL (34)"
        assert 30 in league_ids, "Should have WCQ AFC (30)"
        assert 29 in league_ids, "Should have WCQ CAF (29)"
        assert 960 in league_ids, "Should have Euro Qualifiers (960)"
        
        # Verify league names match IDs
        league_map = {l["id"]: l["name"] for l in leagues}
        assert "UEFA" in league_map.get(32, ""), f"League 32 should be WCQ UEFA, got: {league_map.get(32)}"
        assert "CONMEBOL" in league_map.get(34, ""), f"League 34 should be WCQ CONMEBOL, got: {league_map.get(34)}"
        assert "AFC" in league_map.get(30, ""), f"League 30 should be WCQ AFC, got: {league_map.get(30)}"
        assert "CAF" in league_map.get(29, ""), f"League 29 should be WCQ CAF, got: {league_map.get(29)}"
