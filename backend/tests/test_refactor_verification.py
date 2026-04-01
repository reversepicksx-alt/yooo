"""
Test suite for verifying the backend refactoring.
Tests all routes after splitting server.py into modular route files.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://props-ai-predict.preview.emergentagent.com').rstrip('/')
TEST_EMAIL = "josselj001@gmail.com"  # Owner email - bypasses auth


class TestHealthAndStatus:
    """Health check and status endpoints"""
    
    def test_health_endpoint(self):
        """GET /api/health returns status ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        print(f"✓ Health check passed: {data}")
    
    def test_football_status(self):
        """GET /api/football/status returns status data"""
        response = requests.get(f"{BASE_URL}/api/football/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        print(f"✓ Football status: {data['status']}")


class TestLeagues:
    """Leagues endpoint tests"""
    
    def test_get_leagues_returns_all_supported(self):
        """GET /api/leagues returns all 31 supported leagues"""
        response = requests.get(f"{BASE_URL}/api/leagues")
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        leagues = data["leagues"]
        assert len(leagues) == 31, f"Expected 31 leagues, got {len(leagues)}"
        
        # Verify key leagues are present
        league_ids = [l["id"] for l in leagues]
        assert 39 in league_ids, "Premier League (39) missing"
        assert 140 in league_ids, "La Liga (140) missing"
        assert 135 in league_ids, "Serie A (135) missing"
        assert 78 in league_ids, "Bundesliga (78) missing"
        assert 61 in league_ids, "Ligue 1 (61) missing"
        assert 253 in league_ids, "MLS (253) missing"
        assert 1 in league_ids, "World Cup (1) missing"
        assert 4 in league_ids, "Euro Championship (4) missing"
        assert 960 in league_ids, "Euro Qualifiers (960) missing"
        assert 115 in league_ids, "AFCON Qualifiers (115) missing"
        print(f"✓ All 31 leagues returned correctly")


class TestAuth:
    """Authentication endpoint tests"""
    
    def test_verify_whop_owner_email(self):
        """POST /api/auth/verify-whop with owner email returns verified=true, access_type=Owner"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": TEST_EMAIL}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["verified"] == True
        assert data["access_type"] == "Owner"
        assert data["email"] == TEST_EMAIL.lower()
        assert "session_token" in data
        print(f"✓ Owner auth verified: {data['access_type']}")
        return data["session_token"]


class TestPlayerSearch:
    """Player search endpoint tests"""
    
    def test_search_player_legacy_endpoint(self):
        """GET /api/search-player?query=Salah returns player results"""
        response = requests.get(f"{BASE_URL}/api/search-player?query=Salah")
        assert response.status_code == 200
        data = response.json()
        assert "players" in data
        players = data["players"]
        assert len(players) > 0, "Expected at least one player result"
        
        # Check for Mohamed Salah
        salah_found = any("Salah" in p.get("name", "") for p in players)
        assert salah_found, "Mohamed Salah not found in results"
        print(f"✓ Legacy search returned {len(players)} players")
    
    def test_search_players_post_endpoint(self):
        """POST /api/players/search with query Messi returns results"""
        response = requests.post(
            f"{BASE_URL}/api/players/search",
            json={"query": "Messi"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "players" in data
        players = data["players"]
        assert len(players) > 0, "Expected at least one player result"
        
        # Check for Lionel Messi
        messi_found = any("Messi" in p.get("name", "") for p in players)
        assert messi_found, "Lionel Messi not found in results"
        print(f"✓ Player search returned {len(players)} players")


class TestChat:
    """Chat endpoint tests"""
    
    def test_chat_start_returns_session_id(self):
        """POST /api/chat/start returns a session_id"""
        response = requests.post(
            f"{BASE_URL}/api/chat/start",
            json={}
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0
        assert "message" in data
        print(f"✓ Chat session started: {data['session_id'][:8]}...")


class TestEndpointExistence:
    """Tests that endpoints exist (return 422 for missing body, not 404)"""
    
    def test_scan_prop_endpoint_exists(self):
        """POST /api/scan-prop endpoint exists (returns 422 without body, not 404)"""
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={}
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        # Check it's a validation error for missing image_base64
        errors = data["detail"]
        field_names = [e.get("loc", [])[-1] for e in errors if "loc" in e]
        assert "image_base64" in field_names, "Expected image_base64 validation error"
        print("✓ /api/scan-prop endpoint exists")
    
    def test_predict_endpoint_exists(self):
        """POST /api/predict endpoint exists (returns 422 without body, not 404)"""
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json={}
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print("✓ /api/predict endpoint exists")
    
    def test_picks_save_endpoint_exists(self):
        """POST /api/picks/save endpoint exists (returns 422 without body, not 404)"""
        response = requests.post(
            f"{BASE_URL}/api/picks/save",
            json={}
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print("✓ /api/picks/save endpoint exists")
    
    def test_picks_list_endpoint_exists(self):
        """POST /api/picks/list endpoint exists (returns 422 without body, not 404)"""
        response = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={}
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print("✓ /api/picks/list endpoint exists")
    
    def test_settle_picks_endpoint_exists(self):
        """POST /api/settle-picks endpoint exists (returns 422 without body, not 404)"""
        response = requests.post(
            f"{BASE_URL}/api/settle-picks",
            json={}
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        assert "detail" in data
        print("✓ /api/settle-picks endpoint exists")


class TestPickOfTheDay:
    """Pick of the day endpoint tests"""
    
    def test_pick_of_the_day_endpoint(self):
        """GET /api/pick-of-the-day endpoint exists and returns data"""
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day")
        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert "available" in data
        if data["available"]:
            assert "pick" in data
            pick = data["pick"]
            assert "playerName" in pick
            assert "propType" in pick
            assert "recommendation" in pick
            print(f"✓ Pick of the day: {pick.get('playerName', 'N/A')}")
        else:
            print("✓ Pick of the day endpoint works (no pick available today)")


class TestRouteModularity:
    """Tests to verify routes are properly modularized"""
    
    def test_auth_routes_prefix(self):
        """Auth routes should be under /api/auth/"""
        # verify-whop
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": "test@test.com"})
        assert response.status_code in [200, 401, 422], "verify-whop should be accessible"
        
        # login
        response = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "test@test.com", "password": "test"})
        assert response.status_code in [200, 401, 422], "login should be accessible"
        print("✓ Auth routes properly prefixed under /api/auth/")
    
    def test_players_routes_prefix(self):
        """Player routes should be under /api/"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={"query": "test"})
        assert response.status_code == 200
        print("✓ Player routes properly prefixed under /api/")
    
    def test_picks_routes_prefix(self):
        """Picks routes should be under /api/picks/"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={})
        assert response.status_code == 422  # Missing required fields, but route exists
        print("✓ Picks routes properly prefixed under /api/picks/")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
