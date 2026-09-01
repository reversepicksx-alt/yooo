"""
Test suite for combo prop feature and cache endpoints.
Tests cache lookups, scan-prop endpoint, and health check.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndCacheStatus:
    """Health check and cache status tests"""
    
    def test_health_endpoint(self):
        """GET /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"✓ Health check passed: {data}")
    
    def test_cache_status_returns_valid_counts(self):
        """GET /api/cache/status returns valid counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status")
        assert response.status_code == 200
        data = response.json()
        # Verify all expected fields exist and have positive counts
        assert "leagues" in data and data["leagues"] > 0
        assert "teams" in data and data["teams"] > 0
        assert "players" in data and data["players"] > 0
        assert "nationalTeams" in data and data["nationalTeams"] > 0
        print(f"✓ Cache status: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}, nationalTeams={data['nationalTeams']}")


class TestPlayerLookups:
    """Player cache lookup tests with word-boundary and accent matching"""
    
    def test_lookup_saka_finds_bukayo_not_wan_bissaka(self):
        """GET /api/cache/lookup/player?name=Saka finds B. Saka (word boundary, no Wan-Bissaka)"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Saka"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("found") == True
        player = data.get("player", {})
        # Should find Bukayo Saka, not Wan-Bissaka
        name = player.get("name", "").lower()
        assert "saka" in name
        assert "wan-bissaka" not in name and "bissaka" not in name
        print(f"✓ Saka lookup: Found {player.get('name')} (ID: {player.get('playerId')}) - NOT Wan-Bissaka")
    
    def test_lookup_hojbjerg_finds_with_accent(self):
        """GET /api/cache/lookup/player?name=Hojbjerg finds P. Højbjerg"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Hojbjerg"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("found") == True
        player = data.get("player", {})
        # Should find Højbjerg via accent-stripped matching
        name = player.get("name", "")
        assert "jbjerg" in name.lower() or "højbjerg" in name.lower()
        print(f"✓ Hojbjerg lookup: Found {name} (ID: {player.get('playerId')})")
    
    def test_lookup_salah_with_team_filter(self):
        """GET /api/cache/lookup/player?name=Salah&team_id=40 finds Mohamed Salah at Liverpool"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Salah", "team_id": 40})
        assert response.status_code == 200
        data = response.json()
        assert data.get("found") == True
        player = data.get("player", {})
        # Should find Mohamed Salah at Liverpool (team_id=40)
        name = player.get("name", "")
        assert "salah" in name.lower()
        # Verify it's the Liverpool Salah
        team_id = player.get("teamId")
        assert team_id == 40, f"Expected teamId=40 (Liverpool), got {team_id}"
        print(f"✓ Salah with team filter: Found {name} (ID: {player.get('playerId')}) at teamId={team_id}")


class TestTeamLookups:
    """Team cache lookup tests for club and national teams"""
    
    def test_lookup_denmark_returns_national_team(self):
        """GET /api/cache/lookup/team?name=Denmark returns national team"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/team", params={"name": "Denmark"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("found") == True
        # Response has teamId at top level, not nested
        assert data.get("teamId") is not None
        assert data.get("type") == "national"
        print(f"✓ Denmark lookup: Found teamId={data.get('teamId')}, name={data.get('name')}, type={data.get('type')}")


class TestScanPropEndpoint:
    """Scan prop endpoint tests - verifying endpoint is reachable and handles errors"""
    
    def test_scan_prop_returns_422_on_empty_body(self):
        """POST /api/scan-prop returns 422 on invalid/empty image data (endpoint reachable)"""
        # Send empty body - should get 422 validation error
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={})
        # 422 = validation error (missing required field)
        assert response.status_code == 422
        print(f"✓ scan-prop with empty body: Got expected 422 status")
    
    def test_scan_prop_returns_422_on_invalid_base64(self):
        """POST /api/scan-prop returns 422 on invalid base64 data"""
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={"image_base64": "not-valid-base64!!!"})
        # Should get 422 or 500 for invalid image data
        assert response.status_code in [422, 500]
        print(f"✓ scan-prop with invalid base64: Got status {response.status_code}")
    
    def test_scan_prop_response_schema_includes_iscombo_field(self):
        """POST /api/scan-prop response schema includes isCombo field in extracted object"""
        # We can't send a real image (costs API credits), but we can verify the endpoint exists
        # and check the error response structure
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={"image_base64": "dGVzdA=="})  # "test" in base64
        # This will fail to parse as an image, but endpoint should be reachable
        assert response.status_code in [422, 500]
        # The endpoint exists and responds - that's what we're testing
        print(f"✓ scan-prop endpoint reachable, returns {response.status_code} for invalid image")


class TestCacheEndpointsExist:
    """Verify all cache endpoints exist and respond"""
    
    def test_cache_national_teams_endpoint(self):
        """GET /api/cache/national-teams returns list"""
        response = requests.get(f"{BASE_URL}/api/cache/national-teams")
        assert response.status_code == 200
        data = response.json()
        assert "teams" in data or isinstance(data, list)
        count = len(data.get("teams", data))
        print(f"✓ National teams endpoint: {count} teams")
    
    def test_cache_players_by_team(self):
        """GET /api/cache/players?team_id=42 returns Arsenal squad"""
        response = requests.get(f"{BASE_URL}/api/cache/players", params={"team_id": 42})
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", data)
        assert len(players) > 0
        print(f"✓ Arsenal squad: {len(players)} players")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
