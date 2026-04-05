"""
Iteration 53: Model Upgrades & Calibration Dashboard v2 Tests
- Gemini 2.5 Pro upgrade (replaces gemini-2.0-flash)
- Grok 4.1 Fast Reasoning upgrade (replaces grok-4-1-fast-non-reasoning)
- GPT-5.2 model string verification
- Admin calibration endpoint new fields: byPosition, byGameContext, byPropPosition, byPropContext, blowoutDetails
"""
import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndBasics:
    """Basic health check tests"""
    
    def test_health_endpoint(self):
        """Verify /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"Health check passed: {data}")


class TestModelStringsInCode:
    """Verify model strings are correctly upgraded in all prediction files"""
    
    def test_predict_py_has_gemini_25_pro(self):
        """predict.py should have gemini-2.5-pro"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        assert 'gemini-2.5-pro' in content, "predict.py missing gemini-2.5-pro"
        assert 'gemini-2.0-flash' not in content, "predict.py still has old gemini-2.0-flash"
        count = content.count('gemini-2.5-pro')
        print(f"predict.py: Found {count} occurrences of gemini-2.5-pro")
    
    def test_predict_py_has_grok_41_fast_reasoning(self):
        """predict.py should have grok-4-1-fast-reasoning"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        assert 'grok-4-1-fast-reasoning' in content, "predict.py missing grok-4-1-fast-reasoning"
        assert 'grok-4-1-fast-non-reasoning' not in content, "predict.py still has old grok-4-1-fast-non-reasoning"
        count = content.count('grok-4-1-fast-reasoning')
        print(f"predict.py: Found {count} occurrences of grok-4-1-fast-reasoning")
    
    def test_predict_py_has_gpt52(self):
        """predict.py should have gpt-5.2"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        assert 'gpt-5.2' in content, "predict.py missing gpt-5.2"
        assert 'gpt-4.1-mini' not in content, "predict.py still has old gpt-4.1-mini"
        count = content.count('gpt-5.2')
        print(f"predict.py: Found {count} occurrences of gpt-5.2")
    
    def test_basketball_predict_py_has_gemini_25_pro(self):
        """basketball_predict.py should have gemini-2.5-pro"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        assert 'gemini-2.5-pro' in content, "basketball_predict.py missing gemini-2.5-pro"
        assert 'gemini-2.0-flash' not in content, "basketball_predict.py still has old gemini-2.0-flash"
        count = content.count('gemini-2.5-pro')
        print(f"basketball_predict.py: Found {count} occurrences of gemini-2.5-pro")
    
    def test_basketball_predict_py_has_grok_41_fast_reasoning(self):
        """basketball_predict.py should have grok-4-1-fast-reasoning"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        assert 'grok-4-1-fast-reasoning' in content, "basketball_predict.py missing grok-4-1-fast-reasoning"
        assert 'grok-4-1-fast-non-reasoning' not in content, "basketball_predict.py still has old grok-4-1-fast-non-reasoning"
        count = content.count('grok-4-1-fast-reasoning')
        print(f"basketball_predict.py: Found {count} occurrences of grok-4-1-fast-reasoning")
    
    def test_basketball_predict_py_has_gpt52(self):
        """basketball_predict.py should have gpt-5.2"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        assert 'gpt-5.2' in content, "basketball_predict.py missing gpt-5.2"
        assert 'gpt-4.1-mini' not in content, "basketball_predict.py still has old gpt-4.1-mini"
        count = content.count('gpt-5.2')
        print(f"basketball_predict.py: Found {count} occurrences of gpt-5.2")
    
    def test_miss_analysis_py_has_gemini_25_pro(self):
        """miss_analysis.py should have gemini-2.5-pro"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        assert 'gemini-2.5-pro' in content, "miss_analysis.py missing gemini-2.5-pro"
        assert 'gemini-2.0-flash' not in content, "miss_analysis.py still has old gemini-2.0-flash"
        count = content.count('gemini-2.5-pro')
        print(f"miss_analysis.py: Found {count} occurrences of gemini-2.5-pro")
    
    def test_miss_analysis_py_has_grok_41_fast_reasoning(self):
        """miss_analysis.py should have grok-4-1-fast-reasoning"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        assert 'grok-4-1-fast-reasoning' in content, "miss_analysis.py missing grok-4-1-fast-reasoning"
        assert 'grok-4-1-fast-non-reasoning' not in content, "miss_analysis.py still has old grok-4-1-fast-non-reasoning"
        count = content.count('grok-4-1-fast-reasoning')
        print(f"miss_analysis.py: Found {count} occurrences of grok-4-1-fast-reasoning")
    
    def test_miss_analysis_py_has_gpt52(self):
        """miss_analysis.py should have gpt-5.2"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        assert 'gpt-5.2' in content, "miss_analysis.py missing gpt-5.2"
        assert 'gpt-4.1-mini' not in content, "miss_analysis.py still has old gpt-4.1-mini"
        count = content.count('gpt-5.2')
        print(f"miss_analysis.py: Found {count} occurrences of gpt-5.2")


class TestAdminCalibrationEndpoint:
    """Test admin calibration endpoint returns new v2 fields"""
    
    def test_admin_calibration_summarize_has_new_fields(self):
        """Verify admin.py summarize() function includes new fields"""
        with open('/app/backend/routes/admin.py', 'r') as f:
            content = f.read()
        
        # Check for new fields in summarize function
        assert 'byPosition' in content, "admin.py missing byPosition field"
        assert 'byGameContext' in content, "admin.py missing byGameContext field"
        assert 'byPropPosition' in content, "admin.py missing byPropPosition field"
        assert 'byPropContext' in content, "admin.py missing byPropContext field"
        assert 'blowoutDetails' in content, "admin.py missing blowoutDetails field"
        print("admin.py has all new calibration fields: byPosition, byGameContext, byPropPosition, byPropContext, blowoutDetails")


class TestCalibrationEngineV2:
    """Test calibration.py v2 features"""
    
    def test_calibration_has_position_inference(self):
        """calibration.py should have position inference constants"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        assert 'SOCCER_GK_PROPS' in content, "Missing SOCCER_GK_PROPS"
        assert 'SOCCER_DEF_PROPS' in content, "Missing SOCCER_DEF_PROPS"
        assert 'SOCCER_ATK_PROPS' in content, "Missing SOCCER_ATK_PROPS"
        assert 'SOCCER_MID_PROPS' in content, "Missing SOCCER_MID_PROPS"
        assert 'BBALL_BIG_PROPS' in content, "Missing BBALL_BIG_PROPS"
        assert 'BBALL_GUARD_PROPS' in content, "Missing BBALL_GUARD_PROPS"
        print("calibration.py has all position inference constants")
    
    def test_calibration_has_game_context_classification(self):
        """calibration.py should have game context classification"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        assert '_game_context' in content, "Missing _game_context function"
        assert '"blowout"' in content, "Missing blowout context"
        assert '"close"' in content, "Missing close context"
        assert '"normal"' in content, "Missing normal context"
        print("calibration.py has game context classification (blowout/close/normal)")
    
    def test_calibration_has_granular_buckets(self):
        """calibration.py should track granular stats"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        assert 'by_position' in content, "Missing by_position tracking"
        assert 'by_game_context' in content, "Missing by_game_context tracking"
        assert 'by_prop_position' in content, "Missing by_prop_position tracking"
        assert 'by_prop_context' in content, "Missing by_prop_context tracking"
        assert 'blowout_misses' in content, "Missing blowout_misses tracking"
        print("calibration.py has all granular tracking buckets")


class TestFrontendCalibrationDashboard:
    """Test ProfileTab.jsx CalibrationDashboard v2"""
    
    def test_profile_tab_has_calibration_dashboard(self):
        """ProfileTab.jsx should have CalibrationDashboard component"""
        with open('/app/frontend/src/components/app/ProfileTab.jsx', 'r') as f:
            content = f.read()
        
        assert 'CalibrationDashboard' in content, "Missing CalibrationDashboard component"
        assert 'isOwner' in content, "Missing isOwner check for CalibrationDashboard"
        print("ProfileTab.jsx has CalibrationDashboard component with isOwner gate")
    
    def test_calibration_dashboard_has_section_tabs(self):
        """CalibrationDashboard should have 5 section tabs"""
        with open('/app/frontend/src/components/app/ProfileTab.jsx', 'r') as f:
            content = f.read()
        
        # Check for section tabs
        assert "'overview'" in content or '"overview"' in content, "Missing overview tab"
        assert "'position'" in content or '"position"' in content, "Missing position tab"
        assert "'context'" in content or '"context"' in content, "Missing context tab"
        assert "'league'" in content or '"league"' in content, "Missing league tab"
        assert "'details'" in content or '"details"' in content, "Missing details tab"
        print("CalibrationDashboard has all 5 section tabs: overview, position, context, league, details")
    
    def test_calibration_dashboard_has_data_testids(self):
        """CalibrationDashboard should have data-testid attributes"""
        with open('/app/frontend/src/components/app/ProfileTab.jsx', 'r') as f:
            content = f.read()
        
        assert 'data-testid="calibration-dashboard"' in content, "Missing calibration-dashboard testid"
        assert 'data-testid="calibration-toggle"' in content, "Missing calibration-toggle testid"
        # Template literal format: data-testid={`cal-tab-${s.key}`}
        assert 'data-testid={`cal-tab-' in content, "Missing cal-tab testids"
        print("CalibrationDashboard has data-testid attributes for testing")
    
    def test_calibration_dashboard_renders_new_fields(self):
        """CalibrationDashboard should render byPosition, byGameContext, etc."""
        with open('/app/frontend/src/components/app/ProfileTab.jsx', 'r') as f:
            content = f.read()
        
        assert 'byPosition' in content, "Missing byPosition rendering"
        assert 'byGameContext' in content, "Missing byGameContext rendering"
        assert 'byPropPosition' in content, "Missing byPropPosition rendering"
        assert 'byPropContext' in content, "Missing byPropContext rendering"
        assert 'blowoutDetails' in content, "Missing blowoutDetails rendering"
        print("CalibrationDashboard renders all new v2 fields")


class TestOwnerAuthAndCalibrationAPI:
    """Test owner authentication and calibration API access"""
    
    def test_owner_login_endpoint_exists(self):
        """Owner email should be able to access login endpoint"""
        response = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "josselj001@gmail.com",
            "password": "test"  # Will fail auth but endpoint should exist
        })
        # Should return 401 (invalid password) not 404 (endpoint not found)
        assert response.status_code in [200, 401, 400], f"Unexpected status: {response.status_code}"
        print(f"Login endpoint exists, status: {response.status_code}")
    
    def test_calibration_endpoint_requires_auth(self):
        """Calibration endpoint should require valid session"""
        response = requests.get(f"{BASE_URL}/api/admin/calibration", params={
            "email": "josselj001@gmail.com",
            "token": "invalid_token"
        })
        assert response.status_code == 401, f"Expected 401, got {response.status_code}"
        print("Calibration endpoint correctly requires valid session")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
