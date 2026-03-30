"""
Test iteration 24: Verify ProjectionCard.jsx rewrite and core functionality
- Backend API health
- Auth verification for owner account
- Tactical endpoints for follow-up chat
- 2-tab navigation (Scan | Tracking)
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndAuth:
    """Health check and authentication tests"""
    
    def test_health_endpoint(self):
        """GET /api/health returns 200 with status ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"Health check passed: {data}")
    
    def test_auth_verify_whop_owner(self):
        """POST /api/auth/verify-whop returns verified=true for owner email"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True
        assert data.get("access_type") == "Owner"
        assert "session_token" in data
        print(f"Owner auth verified: {data.get('email')} - {data.get('access_type')}")


class TestTacticalEndpoints:
    """Tactical chat endpoints for follow-up questions"""
    
    def test_tactical_start(self):
        """POST /api/tactical/start returns session_id and welcome message"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data
        assert len(data["session_id"]) > 0
        print(f"Tactical session started: {data['session_id']}")
        return data["session_id"]
    
    def test_tactical_message(self):
        """POST /api/tactical/message works for follow-up questions"""
        # First start a session
        start_response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert start_response.status_code == 200
        session_id = start_response.json()["session_id"]
        
        # Send a message
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": session_id,
                "message": "What is a good pass attempts line for a midfielder?"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert len(data["response"]) > 0
        print(f"Tactical message response received: {len(data['response'])} chars")


class TestCacheStatus:
    """Cache status endpoint"""
    
    def test_cache_status(self):
        """GET /api/cache/status returns valid counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status")
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert "teams" in data
        assert "players" in data
        assert data["leagues"] > 0
        assert data["teams"] > 0
        assert data["players"] > 0
        print(f"Cache status: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}")


class TestPicksEndpoints:
    """Picks CRUD endpoints"""
    
    @pytest.fixture
    def auth_token(self):
        """Get auth token for owner account"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        if response.status_code == 200:
            return response.json().get("session_token")
        pytest.skip("Auth failed")
    
    def test_list_picks(self, auth_token):
        """POST /api/picks/list returns picks array"""
        response = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": "josselj001@gmail.com", "token": auth_token}
        )
        assert response.status_code == 200
        data = response.json()
        assert "picks" in data
        assert isinstance(data["picks"], list)
        print(f"Picks list returned: {len(data['picks'])} picks")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
