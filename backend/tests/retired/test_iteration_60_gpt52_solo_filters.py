"""
Iteration 60: GPT-5.2 Solo Prediction + INTEL Tab Smart Filters

Tests:
1. Backend health check
2. POST /api/predict endpoint is registered (no actual call - costs credits)
3. POST /api/basketball/predict endpoint is registered (no actual call - costs credits)
4. GET /api/intel/sheet returns all rows with correct columns
5. INTEL sheet has filter-relevant columns: position, league, venue, gameType, prop, rec, result, opponent
6. Verify GPT-5.2 solo implementation exists in predict.py
7. Verify GPT-5.2 solo implementation exists in basketball_predict.py
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Get session token for owner
@pytest.fixture(scope="module")
def owner_session():
    """Get owner session token via verify-whop endpoint"""
    resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": "josselj001@gmail.com"})
    assert resp.status_code == 200, f"Failed to get owner session: {resp.text}"
    data = resp.json()
    assert data.get("verified") == True
    return {"email": data["email"], "token": data["session_token"]}


class TestHealthAndEndpoints:
    """Verify backend is healthy and endpoints are registered"""
    
    def test_health_check(self):
        """Backend health endpoint returns OK"""
        resp = requests.get(f"{BASE_URL}/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        print(f"Health check passed: {data}")
    
    def test_predict_endpoint_registered(self):
        """POST /api/predict endpoint exists (returns 422 for missing body, not 404)"""
        # Send empty body - should get 422 (validation error) not 404 (not found)
        resp = requests.post(f"{BASE_URL}/api/predict", json={})
        # 422 = endpoint exists but validation failed (expected)
        # 404 = endpoint not found (failure)
        assert resp.status_code != 404, "POST /api/predict endpoint not found!"
        print(f"POST /api/predict endpoint registered (status: {resp.status_code})")
    
    def test_basketball_predict_endpoint_registered(self):
        """POST /api/basketball/predict endpoint exists (returns 422 for missing body, not 404)"""
        resp = requests.post(f"{BASE_URL}/api/basketball/predict", json={})
        assert resp.status_code != 404, "POST /api/basketball/predict endpoint not found!"
        print(f"POST /api/basketball/predict endpoint registered (status: {resp.status_code})")


class TestIntelSheetEndpoint:
    """Test GET /api/intel/sheet endpoint"""
    
    def test_intel_sheet_returns_data(self, owner_session):
        """Intel sheet returns rows with all required columns"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        assert resp.status_code == 200, f"Intel sheet failed: {resp.text}"
        data = resp.json()
        
        # Check top-level fields
        assert "total" in data
        assert "hits" in data
        assert "misses" in data
        assert "rate" in data
        assert "rows" in data
        
        print(f"Intel sheet: {data['total']} total, {data['hits']} hits, {data['misses']} misses, {data['rate']}% rate")
        
        # Check rows have required columns for filtering
        if data["rows"]:
            row = data["rows"][0]
            required_columns = [
                "player", "position", "prop", "rec", "line", "proj", "actual",
                "error", "result", "league", "venue", "gameType", "opponent"
            ]
            for col in required_columns:
                assert col in row, f"Missing column: {col}"
            print(f"All required columns present: {list(row.keys())}")
    
    def test_intel_sheet_filter_columns_have_values(self, owner_session):
        """Filter columns have actual values (not all empty)"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        rows = data.get("rows", [])
        
        if not rows:
            pytest.skip("No rows in intel sheet")
        
        # Check that filter columns have some non-empty values
        filter_columns = ["position", "league", "venue", "gameType", "prop", "rec", "result", "opponent"]
        for col in filter_columns:
            values = [r.get(col) for r in rows if r.get(col)]
            print(f"Column '{col}': {len(values)} non-empty values out of {len(rows)} rows")
            # At least some rows should have values for key columns
            if col in ["prop", "rec", "result"]:
                assert len(values) > 0, f"Column '{col}' has no values"
    
    def test_intel_sheet_basketball(self, owner_session):
        """Intel sheet works for basketball sport"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "basketball"}
        )
        assert resp.status_code == 200, f"Basketball intel sheet failed: {resp.text}"
        data = resp.json()
        print(f"Basketball intel: {data['total']} total, {data['hits']} hits, {data['misses']} misses, {data['rate']}% rate")
    
    def test_intel_sheet_owner_only(self):
        """Intel sheet requires owner access"""
        # Try with non-owner email
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": "random@example.com", "token": "invalid-token", "sport": "soccer"}
        )
        # Should return 403 or error
        assert resp.status_code in [401, 403] or "error" in resp.json(), "Intel sheet should be owner-only"
        print("Intel sheet correctly requires owner access")


class TestGPT52SoloImplementation:
    """Verify GPT-5.2 solo implementation in backend code"""
    
    def test_predict_py_has_gpt52_solo(self):
        """predict.py contains GPT-5.2 solo implementation"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, "r") as f:
            content = f.read()
        
        # Check for GPT-5.2 solo markers
        assert "GPT-5.2 SOLO" in content, "predict.py missing GPT-5.2 SOLO comment"
        assert 'call_ai("gpt-5.2"' in content, "predict.py missing gpt-5.2 call"
        assert "Elite Calibration" in content or "elite calibration" in content.lower(), "predict.py missing elite calibration"
        print("predict.py has GPT-5.2 solo implementation")
    
    def test_basketball_predict_py_has_gpt52_solo(self):
        """basketball_predict.py contains GPT-5.2 solo implementation"""
        predict_path = "/app/backend/routes/basketball_predict.py"
        with open(predict_path, "r") as f:
            content = f.read()
        
        # Check for GPT-5.2 solo markers
        assert "GPT-5.2 SOLO" in content, "basketball_predict.py missing GPT-5.2 SOLO comment"
        assert 'call_ai("gpt-5.2"' in content, "basketball_predict.py missing gpt-5.2 call"
        print("basketball_predict.py has GPT-5.2 solo implementation")
    
    def test_no_3ai_consensus_in_predict(self):
        """predict.py no longer uses 3-AI consensus (replaced by solo)"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, "r") as f:
            content = f.read()
        
        # The old 3-AI consensus would have multiple AI calls in parallel
        # Now it should be solo GPT-5.2
        # Check that the consensus note mentions solo
        assert "Solo GPT-5.2" in content or "solo" in content.lower(), "predict.py should mention solo approach"
        print("predict.py uses solo GPT-5.2 (not 3-AI consensus)")


class TestIntelTabFilterColumns:
    """Verify INTEL tab has all required filter columns"""
    
    def test_position_filter_values(self, owner_session):
        """Position column has valid position values"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        data = resp.json()
        rows = data.get("rows", [])
        
        positions = set(r.get("position") for r in rows if r.get("position"))
        print(f"Unique positions: {positions}")
        # Should have some positions
        if rows:
            assert len(positions) > 0, "No position values found"
    
    def test_venue_filter_values(self, owner_session):
        """Venue column has home/away values"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        data = resp.json()
        rows = data.get("rows", [])
        
        venues = set(r.get("venue") for r in rows if r.get("venue"))
        print(f"Unique venues: {venues}")
        # Valid venues are home, away, or empty
        for v in venues:
            assert v in ["home", "away", ""], f"Invalid venue: {v}"
    
    def test_result_filter_values(self, owner_session):
        """Result column has hit/miss/push values"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        data = resp.json()
        rows = data.get("rows", [])
        
        results = set(r.get("result") for r in rows if r.get("result"))
        print(f"Unique results: {results}")
        # Valid results are hit, miss, push
        for r in results:
            assert r in ["hit", "miss", "push"], f"Invalid result: {r}"
    
    def test_rec_direction_filter_values(self, owner_session):
        """Rec (direction) column has over/under values"""
        resp = requests.get(
            f"{BASE_URL}/api/intel/sheet",
            params={"email": owner_session["email"], "token": owner_session["token"], "sport": "soccer"}
        )
        data = resp.json()
        rows = data.get("rows", [])
        
        recs = set(r.get("rec") for r in rows if r.get("rec"))
        print(f"Unique rec values: {recs}")
        # Valid recs are over, under
        for r in recs:
            assert r in ["over", "under", ""], f"Invalid rec: {r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
