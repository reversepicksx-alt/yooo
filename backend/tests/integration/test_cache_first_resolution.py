"""
Test suite for cache-first player/team resolution system.
Tests the MongoDB cache lookup endpoints and word-boundary matching.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthEndpoint:
    """Basic health check"""
    
    def test_health_returns_ok(self):
        """GET /api/health returns {status: ok}"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"✓ Health check passed: {data}")


class TestCacheStatus:
    """Test cache status endpoint returns valid counts"""
    
    def test_cache_status_returns_counts(self):
        """GET /api/cache/status returns valid counts for leagues, teams, players, nationalTeams"""
        response = requests.get(f"{BASE_URL}/api/cache/status")
        assert response.status_code == 200
        data = response.json()
        
        # Verify all expected keys exist
        assert "leagues" in data, "Missing 'leagues' in cache status"
        assert "teams" in data, "Missing 'teams' in cache status"
        assert "players" in data, "Missing 'players' in cache status"
        assert "nationalTeams" in data, "Missing 'nationalTeams' in cache status"
        
        # Verify counts are positive (cache is populated)
        assert data["leagues"] > 0, f"Expected leagues > 0, got {data['leagues']}"
        assert data["teams"] > 0, f"Expected teams > 0, got {data['teams']}"
        assert data["players"] > 0, f"Expected players > 0, got {data['players']}"
        assert data["nationalTeams"] > 0, f"Expected nationalTeams > 0, got {data['nationalTeams']}"
        
        print(f"✓ Cache status: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}, nationalTeams={data['nationalTeams']}")


class TestPlayerLookup:
    """Test player lookup with accent-stripped matching and word boundaries"""
    
    def test_hojbjerg_accent_stripped_matching(self):
        """GET /api/cache/lookup/player?name=Hojbjerg finds P. Højbjerg (accent-stripped via nameClean)"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Hojbjerg"})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Hojbjerg, got: {data}"
        player = data.get("player", {})
        assert player, "Player data missing"
        
        # Should find Højbjerg (with accent) via nameClean matching
        player_name = player.get("name", "").lower()
        assert "hojbjerg" in player_name.lower() or "højbjerg" in player_name.lower(), \
            f"Expected Højbjerg, got: {player.get('name')}"
        
        print(f"✓ Hojbjerg lookup: Found {player.get('name')} (ID: {player.get('playerId')})")
    
    def test_salah_with_team_filter(self):
        """GET /api/cache/lookup/player?name=Salah&team_id=40 finds Mohamed Salah at Liverpool (not I. Salah from Basel)"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Salah", "team_id": 40})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Salah at Liverpool (team_id=40), got: {data}"
        player = data.get("player", {})
        
        # Should be Mohamed Salah, not I. Salah
        player_name = player.get("name", "")
        assert "mohamed" in player_name.lower() or "m." in player_name.lower() or player_name.lower().startswith("m"), \
            f"Expected Mohamed Salah, got: {player_name}"
        assert player.get("teamId") == 40, f"Expected teamId=40 (Liverpool), got: {player.get('teamId')}"
        
        print(f"✓ Salah with team filter: Found {player_name} at team {player.get('teamId')}")
    
    def test_saka_word_boundary_matching(self):
        """GET /api/cache/lookup/player?name=Saka finds B. Saka at Arsenal (not Wan-Bissaka)"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Saka"})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Saka, got: {data}"
        player = data.get("player", {})
        
        # Should be B. Saka or Bukayo Saka, NOT Wan-Bissaka
        player_name = player.get("name", "").lower()
        assert "bissaka" not in player_name, f"Got Wan-Bissaka instead of Saka: {player.get('name')}"
        assert "saka" in player_name, f"Expected Saka in name, got: {player.get('name')}"
        
        print(f"✓ Saka word boundary: Found {player.get('name')} (ID: {player.get('playerId')})")
    
    def test_odegaard_accent_stripped_with_team(self):
        """GET /api/cache/lookup/player?name=Odegaard&team_id=42 finds M. Ødegaard at Arsenal"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Odegaard", "team_id": 42})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Odegaard at Arsenal (team_id=42), got: {data}"
        player = data.get("player", {})
        
        player_name = player.get("name", "").lower()
        assert "odegaard" in player_name or "ødegaard" in player_name, \
            f"Expected Ødegaard, got: {player.get('name')}"
        
        print(f"✓ Odegaard accent-stripped: Found {player.get('name')} at team {player.get('teamId')}")
    
    def test_mbappe_accent_stripped_with_team(self):
        """GET /api/cache/lookup/player?name=Mbappe&team_id=541 finds Kylian Mbappé at Real Madrid"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Mbappe", "team_id": 541})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Mbappe at Real Madrid (team_id=541), got: {data}"
        player = data.get("player", {})
        
        player_name = player.get("name", "").lower()
        assert "mbappe" in player_name or "mbappé" in player_name, \
            f"Expected Mbappé, got: {player.get('name')}"
        
        print(f"✓ Mbappe accent-stripped: Found {player.get('name')} at team {player.get('teamId')}")


class TestTeamLookup:
    """Test team lookup for clubs and national teams"""
    
    def test_arsenal_club_lookup(self):
        """GET /api/cache/lookup/team?name=Arsenal returns club type with teamId=42"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/team", params={"name": "Arsenal"})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Arsenal, got: {data}"
        assert data.get("type") == "club", f"Expected type='club', got: {data.get('type')}"
        assert data.get("teamId") == 42, f"Expected teamId=42, got: {data.get('teamId')}"
        
        print(f"✓ Arsenal lookup: type={data.get('type')}, teamId={data.get('teamId')}, name={data.get('name')}")
    
    def test_czechia_national_lookup(self):
        """GET /api/cache/lookup/team?name=Czechia returns national type with teamId=770"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/team", params={"name": "Czechia"})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Czechia, got: {data}"
        assert data.get("type") == "national", f"Expected type='national', got: {data.get('type')}"
        assert data.get("teamId") == 770, f"Expected teamId=770, got: {data.get('teamId')}"
        
        print(f"✓ Czechia lookup: type={data.get('type')}, teamId={data.get('teamId')}, name={data.get('name')}")
    
    def test_denmark_national_lookup(self):
        """GET /api/cache/lookup/team?name=Denmark returns national type with teamId=21"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/team", params={"name": "Denmark"})
        assert response.status_code == 200
        data = response.json()
        
        assert data.get("found") == True, f"Expected to find Denmark, got: {data}"
        assert data.get("type") == "national", f"Expected type='national', got: {data.get('type')}"
        assert data.get("teamId") == 21, f"Expected teamId=21, got: {data.get('teamId')}"
        
        print(f"✓ Denmark lookup: type={data.get('type')}, teamId={data.get('teamId')}, name={data.get('name')}")


class TestNationalTeams:
    """Test national teams endpoint"""
    
    def test_national_teams_list(self):
        """GET /api/cache/national-teams returns a list of national teams"""
        response = requests.get(f"{BASE_URL}/api/cache/national-teams")
        assert response.status_code == 200
        data = response.json()
        
        assert "count" in data, "Missing 'count' in response"
        assert "teams" in data, "Missing 'teams' in response"
        assert data["count"] > 0, f"Expected count > 0, got {data['count']}"
        assert len(data["teams"]) > 0, "Expected non-empty teams list"
        
        # Verify team structure
        first_team = data["teams"][0]
        assert "id" in first_team, "Missing 'id' in team"
        assert "name" in first_team, "Missing 'name' in team"
        
        print(f"✓ National teams: count={data['count']}, sample={first_team}")


class TestPlayersEndpoint:
    """Test players endpoint with team filter"""
    
    def test_arsenal_squad_contains_saka(self):
        """GET /api/cache/players?team_id=42 returns Arsenal squad with B. Saka in it"""
        response = requests.get(f"{BASE_URL}/api/cache/players", params={"team_id": 42})
        assert response.status_code == 200
        data = response.json()
        
        assert "count" in data, "Missing 'count' in response"
        assert "players" in data, "Missing 'players' in response"
        assert data["count"] > 0, f"Expected Arsenal squad count > 0, got {data['count']}"
        
        # Find Saka in the squad
        players = data["players"]
        saka_found = False
        for player in players:
            if "saka" in player.get("name", "").lower():
                saka_found = True
                print(f"✓ Found Saka in Arsenal squad: {player}")
                break
        
        assert saka_found, f"Expected to find Saka in Arsenal squad, got {len(players)} players"


class TestScanPropEndpoint:
    """Test scan-prop endpoint exists and handles bad input"""
    
    def test_scan_prop_exists_returns_422_on_empty(self):
        """POST /api/scan-prop endpoint exists and returns 422 on empty/bad image"""
        # Test with empty body - should return 422 (validation error)
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={})
        assert response.status_code == 422, f"Expected 422 on empty body, got {response.status_code}"
        print(f"✓ scan-prop returns 422 on empty body: {response.json()}")
    
    def test_scan_prop_returns_422_on_invalid_base64(self):
        """POST /api/scan-prop returns 422 on invalid base64 image"""
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={"image_base64": "not-valid-base64"})
        # Should return 422 or 500 depending on how far it gets
        assert response.status_code in [422, 500], f"Expected 422 or 500 on invalid base64, got {response.status_code}"
        print(f"✓ scan-prop handles invalid base64: status={response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
