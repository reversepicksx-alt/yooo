"""
Iteration 59: Test the new /api/intel/sheet endpoint (flat spreadsheet view)
Tests:
- GET /api/intel/sheet returns rows array with all required columns
- Sheet endpoint is owner-only (josselj001@gmail.com)
- Each row has correct data types
- Error direction (errDir) logic
- Non-owner access denied
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = "josselj001@gmail.com"
NON_OWNER_EMAIL = "xaviersteverson@gmail.com"
NON_OWNER_PASSWORD = "test123456"


class TestIntelSheetEndpoint:
    """Tests for the new /api/intel/sheet endpoint"""
    
    @pytest.fixture(scope="class")
    def owner_token(self):
        """Get owner session token via verify-whop"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200, f"Failed to get owner token: {resp.text}"
        data = resp.json()
        assert "session_token" in data, f"No session_token in response: {data}"
        return data["session_token"]
    
    @pytest.fixture(scope="class")
    def non_owner_token(self):
        """Get non-owner session token via login"""
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": NON_OWNER_EMAIL,
            "password": NON_OWNER_PASSWORD
        })
        assert resp.status_code == 200, f"Failed to login non-owner: {resp.text}"
        data = resp.json()
        assert "session_token" in data, f"No session_token in response: {data}"
        return data["session_token"]
    
    # ==================== OWNER ACCESS TESTS ====================
    
    def test_sheet_endpoint_returns_200_for_owner(self, owner_token):
        """Test that owner can access /api/intel/sheet"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "error" not in data, f"Got error: {data.get('error')}"
    
    def test_sheet_returns_rows_array(self, owner_token):
        """Test that response contains rows array"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        assert "rows" in data, f"Missing 'rows' in response: {data.keys()}"
        assert isinstance(data["rows"], list), f"rows should be list, got {type(data['rows'])}"
    
    def test_sheet_returns_meta_fields(self, owner_token):
        """Test that response contains total, hits, misses, rate"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        assert "total" in data, "Missing 'total' field"
        assert "hits" in data, "Missing 'hits' field"
        assert "misses" in data, "Missing 'misses' field"
        assert "rate" in data, "Missing 'rate' field"
    
    def test_sheet_row_has_all_required_columns(self, owner_token):
        """Test that each row has all 19 required columns"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        if len(rows) == 0:
            pytest.skip("No rows in soccer data to test")
        
        # Required columns per the spec
        required_cols = [
            "player", "position", "prop", "rec", "line", "proj", "actual", 
            "error", "errDir", "result", "league", "venue", "gameType", 
            "matchResult", "score", "confidence", "role", "opponent"
        ]
        
        row = rows[0]
        for col in required_cols:
            assert col in row, f"Missing column '{col}' in row. Available: {list(row.keys())}"
    
    def test_sheet_row_data_types(self, owner_token):
        """Test that numeric fields are numbers and string fields are strings"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        if len(rows) == 0:
            pytest.skip("No rows in soccer data to test")
        
        row = rows[0]
        
        # Numeric fields
        numeric_fields = ["line", "proj", "actual", "error", "confidence"]
        for field in numeric_fields:
            val = row.get(field)
            assert isinstance(val, (int, float)), f"Field '{field}' should be numeric, got {type(val)}: {val}"
        
        # String fields
        string_fields = ["player", "position", "prop", "rec", "result", "league", "venue", "gameType", "matchResult", "score", "errDir", "role", "opponent"]
        for field in string_fields:
            val = row.get(field)
            assert isinstance(val, str), f"Field '{field}' should be string, got {type(val)}: {val}"
    
    def test_sheet_error_direction_logic(self, owner_token):
        """Test errDir shows 'over' when error < -0.5 and 'under' when error > 0.5"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        if len(rows) == 0:
            pytest.skip("No rows in soccer data to test")
        
        for row in rows:
            error = row.get("error", 0)
            err_dir = row.get("errDir", "")
            
            if error < -0.5:
                assert err_dir == "over", f"Error {error} < -0.5 should have errDir='over', got '{err_dir}'"
            elif error > 0.5:
                assert err_dir == "under", f"Error {error} > 0.5 should have errDir='under', got '{err_dir}'"
            else:
                assert err_dir == "", f"Error {error} between -0.5 and 0.5 should have errDir='', got '{err_dir}'"
    
    def test_sheet_basketball_sport(self, owner_token):
        """Test that basketball sport filter works"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "basketball"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert "total" in data
    
    # ==================== NON-OWNER ACCESS TESTS ====================
    
    def test_sheet_denied_for_non_owner(self, non_owner_token):
        """Test that non-owner gets 'Owner only' error"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": NON_OWNER_EMAIL,
            "token": non_owner_token,
            "sport": "soccer"
        })
        assert resp.status_code == 200  # API returns 200 with error in body
        data = resp.json()
        assert "error" in data, f"Expected error for non-owner, got: {data}"
        assert data["error"] == "Owner only", f"Expected 'Owner only' error, got: {data['error']}"
    
    def test_sheet_denied_for_invalid_token(self):
        """Test that invalid token gets error"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": "invalid_token_12345",
            "sport": "soccer"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data, f"Expected error for invalid token, got: {data}"
        assert data["error"] == "Invalid session", f"Expected 'Invalid session' error, got: {data['error']}"
    
    # ==================== DATA VALIDATION TESTS ====================
    
    def test_sheet_result_values(self, owner_token):
        """Test that result field only contains hit/miss/push"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        valid_results = {"hit", "miss", "push"}
        for row in rows:
            result = row.get("result", "")
            assert result in valid_results, f"Invalid result '{result}', expected one of {valid_results}"
    
    def test_sheet_rec_values(self, owner_token):
        """Test that rec field only contains over/under or empty"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        valid_recs = {"over", "under", ""}
        for row in rows:
            rec = row.get("rec", "")
            assert rec in valid_recs, f"Invalid rec '{rec}', expected one of {valid_recs}"
    
    def test_sheet_sorted_by_timestamp_desc(self, owner_token):
        """Test that rows are sorted by timestamp descending (newest first)"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        if len(rows) < 2:
            pytest.skip("Need at least 2 rows to test sorting")
        
        timestamps = [row.get("timestamp", "") for row in rows]
        # Filter out empty timestamps
        timestamps = [t for t in timestamps if t]
        
        if len(timestamps) < 2:
            pytest.skip("Not enough timestamps to verify sorting")
        
        # Check descending order
        for i in range(len(timestamps) - 1):
            assert timestamps[i] >= timestamps[i+1], f"Rows not sorted by timestamp desc: {timestamps[i]} < {timestamps[i+1]}"


class TestIntelSheetDataIntegrity:
    """Additional data integrity tests for intel sheet"""
    
    @pytest.fixture(scope="class")
    def owner_token(self):
        """Get owner session token via verify-whop"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        data = resp.json()
        return data.get("session_token")
    
    def test_soccer_has_expected_data(self, owner_token):
        """Test that soccer has the expected 15 settled picks"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        # Per iteration 58 context: 15 settled soccer picks
        assert data.get("total", 0) >= 15, f"Expected at least 15 soccer picks, got {data.get('total')}"
    
    def test_basketball_has_expected_data(self, owner_token):
        """Test that basketball has the expected 11 settled picks"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "basketball"
        })
        data = resp.json()
        # Per iteration 58 context: 11 settled basketball picks
        assert data.get("total", 0) >= 11, f"Expected at least 11 basketball picks, got {data.get('total')}"
    
    def test_match_result_values(self, owner_token):
        """Test that matchResult field only contains win/loss/draw or empty"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        valid_match_results = {"win", "loss", "draw", ""}
        for row in rows:
            match_result = row.get("matchResult", "")
            assert match_result in valid_match_results, f"Invalid matchResult '{match_result}', expected one of {valid_match_results}"
    
    def test_venue_values(self, owner_token):
        """Test that venue field only contains home/away or empty"""
        resp = requests.get(f"{BASE_URL}/api/intel/sheet", params={
            "email": OWNER_EMAIL,
            "token": owner_token,
            "sport": "soccer"
        })
        data = resp.json()
        rows = data.get("rows", [])
        
        valid_venues = {"home", "away", ""}
        for row in rows:
            venue = row.get("venue", "")
            assert venue in valid_venues, f"Invalid venue '{venue}', expected one of {valid_venues}"
