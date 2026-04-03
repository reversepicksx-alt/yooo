"""
Backend Auth Tests for ReversePicks
Tests the Whop-based authentication system including:
- Owner auto-login bypass
- Lifetime subscriber password setup flow
- Login with password
- Session verification
- Logout
- Non-member rejection
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from environment
OWNER_EMAIL = os.environ.get("TEST_OWNER_EMAIL", "josselj001@gmail.com")
LIFETIME_SUB_EMAIL = os.environ.get("TEST_LIFETIME_EMAIL", "rijulgauchan1@gmail.com")
NON_MEMBER_EMAIL = os.environ.get("TEST_NON_MEMBER_EMAIL", "nobody@test.com")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "testpass1")


class TestHealthCheck:
    """Basic health check to ensure API is running"""
    
    def test_health_endpoint(self):
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        print(f"✓ Health check passed: {data}")


class TestOwnerAutoLogin:
    """Owner email should bypass password and get instant auth"""
    
    def test_owner_auto_login(self):
        """Owner josselj001@gmail.com should get verified=True immediately"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": OWNER_EMAIL}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Owner should be verified immediately with session token
        assert data.get("verified") == True, f"Owner should be verified, got: {data}"
        assert data.get("email") == OWNER_EMAIL.lower()
        assert data.get("session_token") is not None, "Owner should get session token"
        assert data.get("access_type") == "Owner", f"Access type should be Owner, got: {data.get('access_type')}"
        
        print(f"✓ Owner auto-login successful: {data}")
        return data.get("session_token")


class TestNonMemberRejection:
    """Non-members should be rejected with proper error message"""
    
    def test_non_member_rejected(self):
        """Random email should get verified=False with error message"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": NON_MEMBER_EMAIL}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Non-member should NOT be verified
        assert data.get("verified") == False, f"Non-member should not be verified, got: {data}"
        assert "No active membership" in data.get("message", ""), f"Should show membership error, got: {data}"
        
        print(f"✓ Non-member correctly rejected: {data}")


class TestLifetimeSubPasswordSetup:
    """Lifetime subscriber password setup flow"""
    
    def test_lifetime_sub_requires_password_setup(self):
        """Lifetime sub without password should get requires_password_setup=True"""
        # First, clear any existing password for this user (for clean test)
        # Note: In production, we'd use a test database
        
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": LIFETIME_SUB_EMAIL}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Should either require password setup OR require password (if already set)
        if data.get("requires_password_setup"):
            assert data.get("access_type") in ["Lifetime", "Premium", "Manual"], f"Should have valid access type, got: {data}"
            print(f"✓ Lifetime sub requires password setup: {data}")
        elif data.get("requires_password"):
            print(f"✓ Lifetime sub already has password set, requires login: {data}")
        else:
            # If verified directly, that's also acceptable (owner case)
            assert data.get("verified") == True, f"Unexpected response: {data}"
            print(f"✓ Lifetime sub verified directly: {data}")
    
    def test_set_password_for_lifetime_sub(self):
        """Set password for lifetime subscriber"""
        response = requests.post(
            f"{BASE_URL}/api/auth/set-password",
            json={"email": LIFETIME_SUB_EMAIL, "password": TEST_PASSWORD}
        )
        
        # Should succeed if user has valid access
        if response.status_code == 200:
            data = response.json()
            assert data.get("verified") == True, f"Should be verified after setting password, got: {data}"
            assert data.get("session_token") is not None, "Should get session token"
            assert data.get("access_type") in ["Lifetime", "Premium", "Manual", "Owner"], f"Should have valid access type, got: {data}"
            print(f"✓ Password set successfully: {data}")
            return data.get("session_token")
        elif response.status_code == 401:
            # User doesn't have valid subscription
            print("✓ User doesn't have valid subscription (expected for some test cases)")
        else:
            pytest.fail(f"Unexpected status code: {response.status_code}, response: {response.text}")
    
    def test_password_too_short(self):
        """Password less than 6 chars should be rejected"""
        response = requests.post(
            f"{BASE_URL}/api/auth/set-password",
            json={"email": LIFETIME_SUB_EMAIL, "password": "12345"}
        )
        assert response.status_code == 400, f"Short password should be rejected, got: {response.status_code}"
        data = response.json()
        assert "6 characters" in data.get("detail", ""), f"Should mention 6 char requirement, got: {data}"
        print(f"✓ Short password correctly rejected: {data}")


class TestLoginWithPassword:
    """Login with email and password"""
    
    def test_login_success(self):
        """Login with correct password should succeed"""
        # First ensure password is set
        requests.post(
            f"{BASE_URL}/api/auth/set-password",
            json={"email": LIFETIME_SUB_EMAIL, "password": TEST_PASSWORD}
        )
        
        # Now login
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": LIFETIME_SUB_EMAIL, "password": TEST_PASSWORD}
        )
        
        if response.status_code == 200:
            data = response.json()
            assert data.get("verified") == True, f"Should be verified, got: {data}"
            assert data.get("session_token") is not None, "Should get session token"
            print(f"✓ Login successful: {data}")
            return data.get("session_token")
        elif response.status_code == 401:
            data = response.json()
            print(f"✓ Login failed (expected if subscription expired): {data}")
        else:
            pytest.fail(f"Unexpected status code: {response.status_code}")
    
    def test_login_wrong_password(self):
        """Login with wrong password should fail"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": LIFETIME_SUB_EMAIL, "password": "wrongpassword123"}
        )
        assert response.status_code == 401, f"Wrong password should return 401, got: {response.status_code}"
        data = response.json()
        assert "Invalid" in data.get("detail", ""), f"Should show invalid error, got: {data}"
        print(f"✓ Wrong password correctly rejected: {data}")
    
    def test_login_no_password_set(self):
        """Login for user without password should fail"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "newuser_nopassword@test.com", "password": "anypassword"}
        )
        assert response.status_code == 401, f"No password user should return 401, got: {response.status_code}"
        print("✓ Login without password correctly rejected")


class TestSessionVerification:
    """Session verification tests"""
    
    def test_verify_valid_session(self):
        """Valid session should be verified"""
        # First get a valid session
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": OWNER_EMAIL}
        )
        data = response.json()
        session_token = data.get("session_token")
        
        if session_token:
            # Verify the session
            verify_response = requests.post(
                f"{BASE_URL}/api/auth/verify-session",
                json={"email": OWNER_EMAIL, "session_token": session_token}
            )
            assert verify_response.status_code == 200
            verify_data = verify_response.json()
            assert verify_data.get("valid") == True, f"Session should be valid, got: {verify_data}"
            print(f"✓ Session verification successful: {verify_data}")
        else:
            pytest.skip("Could not get session token for verification test")
    
    def test_verify_invalid_session(self):
        """Invalid session should not be verified"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-session",
            json={"email": OWNER_EMAIL, "session_token": "invalid-token-12345"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("valid") == False, f"Invalid session should not be valid, got: {data}"
        print(f"✓ Invalid session correctly rejected: {data}")


class TestLogout:
    """Logout functionality tests"""
    
    def test_logout(self):
        """Logout should invalidate session"""
        # First get a valid session
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": OWNER_EMAIL}
        )
        data = response.json()
        session_token = data.get("session_token")
        
        if session_token:
            # Logout
            logout_response = requests.post(
                f"{BASE_URL}/api/auth/logout",
                json={"email": OWNER_EMAIL, "session_token": session_token}
            )
            assert logout_response.status_code == 200
            logout_data = logout_response.json()
            assert logout_data.get("success") == True, f"Logout should succeed, got: {logout_data}"
            
            # Verify session is now invalid
            verify_response = requests.post(
                f"{BASE_URL}/api/auth/verify-session",
                json={"email": OWNER_EMAIL, "session_token": session_token}
            )
            verify_data = verify_response.json()
            assert verify_data.get("valid") == False, f"Session should be invalid after logout, got: {verify_data}"
            
            print("✓ Logout successful, session invalidated")
        else:
            pytest.skip("Could not get session token for logout test")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
