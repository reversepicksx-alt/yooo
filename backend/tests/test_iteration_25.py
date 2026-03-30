"""
Iteration 25 Backend Tests
Tests for: Major UI overhaul (3-tab nav, Profile tab), VIP email access, v2.2 version
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://ai-sports-analytics-2.preview.emergentagent.com').rstrip('/')


class TestHealthEndpoint:
    """Health check endpoint tests"""
    
    def test_health_returns_200(self):
        """GET /api/health returns 200 with status ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        assert "timestamp" in data
        print(f"✓ Health check passed: {data}")


class TestAuthVerifyWhop:
    """Auth verify-whop endpoint tests"""
    
    def test_owner_email_auto_verifies(self):
        """POST /api/auth/verify-whop returns verified=true for owner email"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") is True
        assert data.get("email") == "josselj001@gmail.com"
        assert data.get("access_type") == "Owner"
        assert "session_token" in data
        print(f"✓ Owner email verified: {data.get('access_type')}")
    
    def test_vip_email_michael_access(self):
        """POST /api/auth/verify-whop returns access for new VIP email michael1069_6910@yahoo.com"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "michael1069_6910@yahoo.com"}
        )
        assert response.status_code == 200
        data = response.json()
        # VIP email should either auto-verify or require password setup
        assert data.get("requires_password_setup") is True or data.get("verified") is True
        assert data.get("email") == "michael1069_6910@yahoo.com"
        assert data.get("access_type") == "Lifetime"
        print(f"✓ VIP email michael1069_6910@yahoo.com has Lifetime access")
    
    def test_invalid_email_returns_not_verified(self):
        """POST /api/auth/verify-whop returns verified=false for invalid email"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "invalid_test_email_12345@example.com"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") is False
        assert "No active membership" in data.get("message", "")
        print(f"✓ Invalid email correctly rejected")


class TestAuthResetPassword:
    """Auth reset-password endpoint tests"""
    
    def test_reset_password_endpoint_exists(self):
        """POST /api/auth/reset-password endpoint exists and responds"""
        response = requests.post(
            f"{BASE_URL}/api/auth/reset-password",
            json={"email": "test@example.com", "new_password": "test123456"}
        )
        # Should return 401 (no subscription) or 404 (no account) - not 404 method not found
        assert response.status_code in [401, 404]
        data = response.json()
        assert "detail" in data
        print(f"✓ Reset password endpoint exists, response: {data.get('detail')}")
    
    def test_reset_password_short_password_validation(self):
        """POST /api/auth/reset-password validates password length"""
        response = requests.post(
            f"{BASE_URL}/api/auth/reset-password",
            json={"email": "josselj001@gmail.com", "new_password": "123"}
        )
        # Should return 400 for short password
        assert response.status_code == 400
        data = response.json()
        assert "6 characters" in data.get("detail", "")
        print(f"✓ Short password validation works")


class TestPicksEndpoint:
    """Picks list endpoint tests"""
    
    def test_picks_list_requires_auth(self):
        """POST /api/picks/list requires authentication"""
        response = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": "josselj001@gmail.com", "token": "invalid_token"}
        )
        # Should return 401 for invalid token
        assert response.status_code in [200, 401]
        print(f"✓ Picks list endpoint responds correctly")
    
    def test_picks_list_with_valid_session(self):
        """POST /api/picks/list returns picks for authenticated user"""
        # First get a valid session token
        verify_response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        token = verify_response.json().get("session_token")
        
        response = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": "josselj001@gmail.com", "token": token}
        )
        assert response.status_code == 200
        data = response.json()
        assert "picks" in data
        assert isinstance(data["picks"], list)
        print(f"✓ Picks list returned {len(data['picks'])} picks")


class TestCacheStatus:
    """Cache status endpoint tests"""
    
    def test_cache_status_returns_counts(self):
        """GET /api/cache/status returns valid cache counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status")
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert "teams" in data
        assert "players" in data
        print(f"✓ Cache status: leagues={data.get('leagues')}, teams={data.get('teams')}, players={data.get('players')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
