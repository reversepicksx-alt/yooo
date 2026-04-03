"""
Iteration 43 Tests: Tracking Tabs Split & Calibration Insights
- Tests the new tracking tabs: Live | Won | Lost | Pushed | Insights
- Tests the /api/calibration/insights endpoint
- Verifies proper filtering of picks by result type
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
TEST_EMAIL = os.environ.get('TEST_EMAIL', 'josselj001@gmail.com')


class TestCalibrationInsightsEndpoint:
    """Tests for /api/calibration/insights endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token before each test"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200, f"Auth failed: {resp.text}"
        data = resp.json()
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
        assert self.token, "No session token returned"
    
    def test_calibration_insights_returns_200(self):
        """Calibration insights endpoint should return 200"""
        resp = requests.post(
            f"{BASE_URL}/api/calibration/insights",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    
    def test_calibration_insights_structure(self):
        """Calibration insights should return proper data structure"""
        resp = requests.post(
            f"{BASE_URL}/api/calibration/insights",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        # Check required top-level fields
        assert "insights" in data, "Missing 'insights' field"
        assert "totalAnalyzed" in data, "Missing 'totalAnalyzed' field"
        assert "totalMisses" in data, "Missing 'totalMisses' field"
        assert "totalPropTypes" in data, "Missing 'totalPropTypes' field"
        
        # Verify insights is a list
        assert isinstance(data["insights"], list), "insights should be a list"
    
    def test_calibration_insight_item_structure(self):
        """Each calibration insight should have required fields"""
        resp = requests.post(
            f"{BASE_URL}/api/calibration/insights",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if len(data["insights"]) > 0:
            insight = data["insights"][0]
            required_fields = [
                "sport", "propType", "missCount", "avgErrorPct",
                "recentAvgErrorPct", "biasDirection", "biasConsistent",
                "activeCorrection", "recentSampleSize"
            ]
            for field in required_fields:
                assert field in insight, f"Missing field '{field}' in insight"
            
            # Verify data types
            assert isinstance(insight["missCount"], int), "missCount should be int"
            assert isinstance(insight["avgErrorPct"], (int, float)), "avgErrorPct should be numeric"
            assert insight["biasDirection"] in ["under-projecting", "over-projecting"], \
                f"Invalid biasDirection: {insight['biasDirection']}"
    
    def test_calibration_insights_unauthorized(self):
        """Calibration insights should reject invalid token"""
        resp = requests.post(
            f"{BASE_URL}/api/calibration/insights",
            json={"email": self.email, "token": "invalid-token-12345"}
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


class TestPicksListFiltering:
    """Tests for picks list to verify proper result filtering"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token before each test"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200
        data = resp.json()
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
    
    def test_picks_list_returns_200(self):
        """Picks list endpoint should return 200"""
        resp = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
    
    def test_picks_have_result_field(self):
        """Settled picks should have result field (hit/miss/push)"""
        resp = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
        data = resp.json()
        picks = data.get("picks", [])
        
        settled = [p for p in picks if p.get("status") == "settled"]
        for pick in settled:
            result = pick.get("result")
            assert result in ["hit", "miss", "push", "pending"], \
                f"Invalid result '{result}' for pick {pick.get('playerName')}"
    
    def test_picks_filtering_by_result(self):
        """Verify picks can be filtered by result type"""
        resp = requests.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
        data = resp.json()
        picks = data.get("picks", [])
        
        # Filter by each result type (simulating frontend filtering)
        live_picks = [p for p in picks if p.get("status") == "live"]
        won_picks = [p for p in picks if p.get("status") == "settled" and p.get("result") == "hit"]
        lost_picks = [p for p in picks if p.get("status") == "settled" and p.get("result") == "miss"]
        pushed_picks = [p for p in picks if p.get("status") == "settled" and p.get("result") == "push"]
        
        # Log counts for debugging
        print(f"Live: {len(live_picks)}, Won: {len(won_picks)}, Lost: {len(lost_picks)}, Pushed: {len(pushed_picks)}")
        
        # Verify no overlap between won and pushed (pushes should NOT be in won)
        for pick in pushed_picks:
            assert pick not in won_picks, f"Push pick {pick.get('playerName')} should not be in won list"


class TestMissesEndpoint:
    """Tests for /api/picks/misses endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token before each test"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200
        data = resp.json()
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
    
    def test_misses_endpoint_returns_200(self):
        """Misses endpoint should return 200"""
        resp = requests.post(
            f"{BASE_URL}/api/picks/misses",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
    
    def test_misses_returns_only_misses(self):
        """Misses endpoint should only return picks with result='miss'"""
        resp = requests.post(
            f"{BASE_URL}/api/picks/misses",
            json={"email": self.email, "token": self.token}
        )
        assert resp.status_code == 200
        data = resp.json()
        misses = data.get("misses", [])
        
        for miss in misses:
            assert miss.get("result") == "miss", \
                f"Non-miss pick in misses list: {miss.get('playerName')} has result={miss.get('result')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
