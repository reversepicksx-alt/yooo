"""
Basketball Integration Tests - Iteration 26
Tests for:
- Basketball prediction endpoint POST /api/basketball/predict
- Basketball team search endpoint POST /api/basketball/search-teams
- Scan endpoint POST /api/scan-prop with sport='basketball'
- Soccer prediction regression test
- No baseball references in backend routes
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestBasketballIntegration:
    """Basketball (NBA) integration tests"""
    
    def test_health_check(self):
        """Verify API is running"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print("✓ Health check passed")
    
    def test_basketball_search_teams_lakers(self):
        """Test basketball team search returns teams with id and name"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Lakers"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert "teams" in data
        assert len(data["teams"]) > 0
        # Verify team structure
        team = data["teams"][0]
        assert "id" in team
        assert "name" in team
        assert "Lakers" in team["name"]
        print(f"✓ Basketball team search returned: {team['name']} (ID: {team['id']})")
    
    def test_basketball_search_teams_celtics(self):
        """Test basketball team search for Celtics"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": "Celtics"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert "teams" in data
        assert len(data["teams"]) > 0
        team = data["teams"][0]
        assert "id" in team
        assert "name" in team
        assert "Celtics" in team["name"]
        print(f"✓ Basketball team search returned: {team['name']} (ID: {team['id']})")
    
    def test_basketball_search_teams_empty_query(self):
        """Test basketball team search with empty query returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/basketball/search-teams",
            json={"query": ""},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        assert response.status_code == 400
        print("✓ Empty query correctly returns 400")
    
    def test_basketball_predict_endpoint(self):
        """Test basketball prediction endpoint returns valid JSON structure
        Note: This test takes ~30-50 seconds due to multi-AI consensus engine
        """
        payload = {
            "teamId": 145,  # Lakers
            "teamName": "Los Angeles Lakers",
            "opponentId": 133,  # Celtics
            "opponentName": "Boston Celtics",
            "playerName": "LeBron James",
            "venue": "home",
            "propType": "points",
            "line": 25.5
        }
        
        print("Starting basketball prediction (may take ~30-50 seconds)...")
        start_time = time.time()
        
        response = requests.post(
            f"{BASE_URL}/api/basketball/predict",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120  # Long timeout for AI processing
        )
        
        elapsed = time.time() - start_time
        print(f"Prediction completed in {elapsed:.1f}s")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify required fields
        assert "projectedValue" in data, "Missing projectedValue"
        assert "recommendation" in data, "Missing recommendation"
        assert "confidenceScore" in data, "Missing confidenceScore"
        assert "sport" in data, "Missing sport field"
        assert data["sport"] == "basketball", f"Expected sport='basketball', got '{data.get('sport')}'"
        
        # Verify recommendation is valid
        assert data["recommendation"] in ["over", "under"], f"Invalid recommendation: {data['recommendation']}"
        
        # Verify projectedValue is a number
        assert isinstance(data["projectedValue"], (int, float)), "projectedValue should be numeric"
        
        # Verify confidenceScore is reasonable
        conf = data["confidenceScore"]
        assert isinstance(conf, (int, float)), "confidenceScore should be numeric"
        
        # Check for tacticalBreakdown
        assert "tacticalBreakdown" in data, "Missing tacticalBreakdown"
        assert len(data.get("tacticalBreakdown", "")) > 100, "tacticalBreakdown should be substantial"
        
        # Check for recentSamples
        assert "recentSamples" in data, "Missing recentSamples"
        
        print(f"✓ Basketball prediction: {data['recommendation'].upper()} {payload['line']} (Proj: {data['projectedValue']}, Conf: {data['confidenceScore']}%)")
        print(f"✓ Sport field correctly set to: {data['sport']}")
    
    def test_scan_prop_accepts_basketball_sport(self):
        """Test scan-prop endpoint accepts sport='basketball' parameter"""
        # Use a minimal base64 image (1x1 transparent PNG)
        minimal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": minimal_png_base64, "sport": "basketball"},
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        
        # Should not return 400 for invalid sport parameter
        # May return 422 if AI can't parse the minimal image, which is expected
        assert response.status_code in [200, 422], f"Unexpected status: {response.status_code}"
        
        if response.status_code == 422:
            # Expected - AI couldn't parse minimal image
            print("✓ Scan endpoint accepts sport='basketball' (422 = AI couldn't parse minimal test image)")
        else:
            data = response.json()
            print(f"✓ Scan endpoint accepts sport='basketball', returned: {data}")


class TestSoccerRegression:
    """Regression tests to ensure soccer prediction still works"""
    
    def test_soccer_predict_endpoint(self):
        """Test soccer prediction endpoint still works after basketball integration"""
        payload = {
            "leagueId": 39,  # Premier League
            "playerId": 1100,  # Example player ID
            "playerName": "Mohamed Salah",
            "teamId": 40,  # Liverpool
            "teamName": "Liverpool",
            "opponentId": 42,  # Arsenal
            "opponentName": "Arsenal",
            "venue": "home",
            "propType": "shots",
            "line": 3.5
        }
        
        print("Starting soccer prediction (may take ~20-40 seconds)...")
        start_time = time.time()
        
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120
        )
        
        elapsed = time.time() - start_time
        print(f"Soccer prediction completed in {elapsed:.1f}s")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify required fields
        assert "projectedValue" in data, "Missing projectedValue"
        assert "recommendation" in data, "Missing recommendation"
        assert "confidenceScore" in data, "Missing confidenceScore"
        
        print(f"✓ Soccer prediction: {data['recommendation'].upper()} {payload['line']} (Proj: {data['projectedValue']}, Conf: {data['confidenceScore']}%)")


class TestNoBaseballReferences:
    """Verify no baseball references remain in backend"""
    
    def test_no_baseball_in_routes(self):
        """Verify no baseball references in backend routes"""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "baseball", "/app/backend/routes/"],
            capture_output=True,
            text=True
        )
        # grep returns 1 if no matches found (which is what we want)
        assert result.returncode == 1, f"Found baseball references: {result.stdout}"
        print("✓ No baseball references in /app/backend/routes/")
    
    def test_no_baseball_in_server(self):
        """Verify no baseball references in server.py"""
        import subprocess
        result = subprocess.run(
            ["grep", "-n", "baseball", "/app/backend/server.py"],
            capture_output=True,
            text=True
        )
        # grep returns 1 if no matches found (which is what we want)
        # But basketball_router is fine - we're checking for "baseball" not "basketball"
        assert result.returncode == 1, f"Found baseball references: {result.stdout}"
        print("✓ No baseball references in server.py")


class TestOwnerAuth:
    """Test owner authentication still works"""
    
    def test_owner_verify_whop(self):
        """Test owner email auto-verifies"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True
        assert data.get("access_type") == "Owner"
        print(f"✓ Owner email verified: access_type={data.get('access_type')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
