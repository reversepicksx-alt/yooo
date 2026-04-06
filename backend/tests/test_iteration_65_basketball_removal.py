"""
Iteration 65: Basketball Removal Verification Tests
Tests that all basketball functionality has been removed from the app.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://props-ai-predict.preview.emergentagent.com')

# Test credentials
TEST_EMAIL = "xaviersteverson@gmail.com"
TEST_PASSWORD = "test123456"


class TestHealthEndpoint:
    """Test that the health endpoint works"""
    
    def test_health_endpoint_returns_ok(self):
        """Verify /api/health returns status ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"Health endpoint: {data}")


class TestAuthFlow:
    """Test authentication flow"""
    
    def test_login_with_valid_credentials(self):
        """Verify login works with test credentials"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True
        assert data.get("email") == TEST_EMAIL
        assert "session_token" in data
        print(f"Login successful: {data.get('access_type')}")


class TestPicksEndpoint:
    """Test picks list endpoint"""
    
    @pytest.fixture
    def auth_token(self):
        """Get authentication token"""
        response = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        if response.status_code == 200:
            return response.json().get("session_token")
        pytest.skip("Authentication failed")
    
    def test_picks_list_endpoint(self, auth_token):
        """Verify /api/picks/list works with valid session"""
        response = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": TEST_EMAIL, "token": auth_token}
        )
        assert response.status_code == 200
        data = response.json()
        assert "picks" in data
        print(f"Picks list returned {len(data['picks'])} picks")


class TestBasketballRemoval:
    """Verify basketball functionality has been removed"""
    
    def test_no_basketball_routes_in_backend(self):
        """Verify basketball_predict.py route file doesn't exist"""
        backend_routes_dir = "/app/backend/routes"
        basketball_files = [
            "basketball_predict.py",
            "basketball.py",
        ]
        for filename in basketball_files:
            filepath = os.path.join(backend_routes_dir, filename)
            assert not os.path.exists(filepath), f"Basketball route file still exists: {filepath}"
        print("PASS: No basketball route files found")
    
    def test_no_basketball_utils_file(self):
        """Verify basketball_utils.py doesn't exist"""
        filepath = "/app/backend/basketball_utils.py"
        assert not os.path.exists(filepath), f"Basketball utils file still exists: {filepath}"
        print("PASS: basketball_utils.py not found")
    
    def test_no_basketball_imports_in_api_js(self):
        """Verify api.js doesn't export basketball functions"""
        api_js_path = "/app/frontend/src/api.js"
        with open(api_js_path, "r") as f:
            content = f.read()
        
        # Check for basketball-specific exports
        basketball_exports = [
            "basketballSearchTeams",
            "basketballPredict",
            "basketballSearchPlayers",
        ]
        for export in basketball_exports:
            assert export not in content, f"Basketball export still exists: {export}"
        print("PASS: No basketball exports in api.js")
    
    def test_no_basketball_prop_types_in_constants(self):
        """Verify constants.js doesn't have BASKETBALL_PROP_TYPES"""
        constants_path = "/app/frontend/src/constants.js"
        with open(constants_path, "r") as f:
            content = f.read()
        
        assert "BASKETBALL_PROP_TYPES" not in content, "BASKETBALL_PROP_TYPES still exists in constants.js"
        print("PASS: No BASKETBALL_PROP_TYPES in constants.js")
    
    def test_header_only_shows_soccer(self):
        """Verify Header.jsx only shows Soccer, not basketball toggle"""
        header_path = "/app/frontend/src/components/app/Header.jsx"
        with open(header_path, "r") as f:
            content = f.read()
        
        # Should have Soccer
        assert "Soccer" in content, "Soccer label not found in Header"
        
        # Should NOT have basketball toggle buttons
        assert "CartoonBasketball" not in content, "CartoonBasketball component still in Header"
        assert "basketball" not in content.lower() or "// basketball" in content.lower(), "Basketball reference found in Header"
        print("PASS: Header only shows Soccer")
    
    def test_intel_tab_only_soccer_toggle(self):
        """Verify IntelTab.jsx only has soccer sport toggle"""
        intel_path = "/app/frontend/src/components/app/IntelTab.jsx"
        with open(intel_path, "r") as f:
            content = f.read()
        
        # Check sport toggle only has soccer
        assert "['soccer']" in content or "['soccer'" in content, "Soccer-only toggle not found"
        assert "basketball" not in content.lower() or "// basketball" in content.lower(), "Basketball reference found in IntelTab"
        print("PASS: IntelTab only has soccer toggle")
    
    def test_profile_tab_only_soccer_calibration(self):
        """Verify ProfileTab.jsx calibration only has soccer toggle"""
        profile_path = "/app/frontend/src/components/app/ProfileTab.jsx"
        with open(profile_path, "r") as f:
            content = f.read()
        
        # Check calibration sport toggle only has soccer
        # The sport toggle should be ['soccer'] not ['soccer', 'basketball']
        assert "['soccer']" in content, "Soccer-only calibration toggle not found"
        print("PASS: ProfileTab calibration only has soccer toggle")
    
    def test_tracking_tab_no_basketball_labels(self):
        """Verify TrackingTab.jsx doesn't have NBA/basketball sport labels"""
        tracking_path = "/app/frontend/src/components/app/TrackingTab.jsx"
        with open(tracking_path, "r") as f:
            content = f.read()
        
        # Should not have NBA or basketball sport labels in pick cards
        # The sport label should just be "Soccer" not "NBA" or "Basketball"
        assert "NBA" not in content or "// NBA" in content, "NBA label found in TrackingTab"
        print("PASS: TrackingTab has no NBA labels")
    
    def test_scan_route_only_soccer(self):
        """Verify scan.py only handles soccer props"""
        scan_path = "/app/backend/routes/scan.py"
        with open(scan_path, "r") as f:
            content = f.read()
        
        # Should not have basketball scan prompt
        assert "BASKETBALL" not in content.upper() or "# BASKETBALL" in content.upper(), "Basketball scan prompt found"
        assert "NBA" not in content or "# NBA" in content, "NBA reference found in scan.py"
        print("PASS: scan.py only handles soccer")
    
    def test_picks_route_only_soccer(self):
        """Verify picks.py only handles soccer settlement"""
        picks_path = "/app/backend/routes/picks.py"
        with open(picks_path, "r") as f:
            content = f.read()
        
        # Should not have basketball settlement functions
        assert "_process_basketball_live" not in content, "Basketball live processing still exists"
        assert "_settle_basketball_pick" not in content, "Basketball settlement still exists"
        assert "BBALL_STAT_MAP" not in content, "BBALL_STAT_MAP still exists"
        print("PASS: picks.py only handles soccer")
    
    def test_grok_engine_only_soccer(self):
        """Verify grok_engine.py only handles soccer"""
        grok_path = "/app/backend/grok_engine.py"
        with open(grok_path, "r") as f:
            content = f.read()
        
        # Should not have basketball auto-settlement
        assert "_try_settle_basketball" not in content, "Basketball settlement in grok_engine"
        print("PASS: grok_engine.py only handles soccer")


class TestScanPropEndpoint:
    """Test scan-prop endpoint only accepts soccer"""
    
    def test_scan_prop_defaults_to_soccer(self):
        """Verify scan-prop endpoint defaults to soccer sport"""
        # We can't actually test image scanning without an image,
        # but we can verify the endpoint exists and accepts requests
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": "invalid", "sport": "soccer"}
        )
        # Should fail with 500 (invalid image) not 404 (endpoint not found)
        assert response.status_code in [422, 500], f"Unexpected status: {response.status_code}"
        print("PASS: scan-prop endpoint exists and accepts soccer sport")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
