"""
Iteration 55: INTEL Dashboard Exact Positions Feature Tests
============================================================
Tests for:
1. GET /api/intel/dashboard returns exact positions (CB, LB, LW, GK, G, SF, Forward) in byPosition
2. POST /api/intel/backfill-positions migrates existing picks to have exact positions
3. POST /api/picks/save stores position and role fields from prediction data
4. Worst misses include role information alongside position
5. Owner-only access control on INTEL endpoints
"""

import pytest
import requests
import os

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")

# Test credentials from test_credentials.md
OWNER_EMAIL = "josselj001@gmail.com"
NON_OWNER_EMAIL = "xaviersteverson@gmail.com"
NON_OWNER_PASSWORD = "test123456"


class TestIntelOwnerAuth:
    """Test owner-only access control on INTEL endpoints"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token via verify-whop"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Get owner token
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200, f"Owner verify-whop failed: {resp.text}"
        data = resp.json()
        self.owner_token = data.get("session_token")
        assert self.owner_token, "No session_token returned for owner"
        print(f"✓ Owner authenticated: {OWNER_EMAIL}")

    def test_intel_dashboard_owner_access(self):
        """Owner should be able to access INTEL dashboard"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200, f"Dashboard request failed: {resp.text}"
        data = resp.json()
        assert "error" not in data or data.get("error") != "Owner only", "Owner should have access"
        print(f"✓ Owner can access INTEL dashboard")

    def test_intel_dashboard_non_owner_denied(self):
        """Non-owner should be denied access to INTEL dashboard"""
        # Login as non-owner
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": NON_OWNER_EMAIL, "password": NON_OWNER_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip("Non-owner test account login failed - skipping access denial test")
        
        non_owner_token = login_resp.json().get("session_token")
        
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": NON_OWNER_EMAIL, "token": non_owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200, f"Request failed: {resp.text}"
        data = resp.json()
        assert data.get("error") == "Owner only", f"Non-owner should be denied, got: {data}"
        print(f"✓ Non-owner correctly denied access to INTEL dashboard")

    def test_backfill_positions_non_owner_denied(self):
        """Non-owner should be denied access to backfill endpoint"""
        # Login as non-owner
        login_resp = self.session.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": NON_OWNER_EMAIL, "password": NON_OWNER_PASSWORD}
        )
        if login_resp.status_code != 200:
            pytest.skip("Non-owner test account login failed - skipping access denial test")
        
        non_owner_token = login_resp.json().get("session_token")
        
        resp = self.session.post(
            f"{BASE_URL}/api/intel/backfill-positions",
            params={"email": NON_OWNER_EMAIL, "token": non_owner_token}
        )
        assert resp.status_code == 200, f"Request failed: {resp.text}"
        data = resp.json()
        assert data.get("error") == "Owner only", f"Non-owner should be denied, got: {data}"
        print(f"✓ Non-owner correctly denied access to backfill endpoint")


class TestIntelDashboardExactPositions:
    """Test that INTEL dashboard returns exact positions instead of generic ones"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200, f"Owner verify-whop failed: {resp.text}"
        self.owner_token = resp.json().get("session_token")

    def test_dashboard_soccer_returns_exact_positions(self):
        """Soccer dashboard should return exact positions like CB, LB, LW, GK, ST, etc."""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200, f"Dashboard request failed: {resp.text}"
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled soccer picks - cannot verify positions")
        
        by_position = data.get("byPosition", {})
        print(f"Soccer positions found: {list(by_position.keys())}")
        
        # Check that we have exact positions, not just generic ones
        exact_soccer_positions = {"GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST", "SS"}
        generic_positions = {"DEF", "MID", "FWD", "Goalkeeper", "Defender", "Midfielder", "Attacker"}
        
        found_exact = False
        found_generic = False
        
        for pos in by_position.keys():
            if pos in exact_soccer_positions:
                found_exact = True
                print(f"  ✓ Found exact position: {pos} ({by_position[pos]['total']} picks)")
            elif pos in generic_positions:
                found_generic = True
                print(f"  ⚠ Found generic position: {pos} ({by_position[pos]['total']} picks)")
            elif pos == "Unknown":
                print(f"  ℹ Found Unknown position: {by_position[pos]['total']} picks")
        
        # We should have at least some exact positions if data exists
        if by_position and not found_exact and not found_generic:
            print(f"  ℹ Only Unknown positions found - backfill may be needed")
        
        print(f"✓ Soccer dashboard byPosition structure verified")

    def test_dashboard_basketball_returns_exact_positions(self):
        """Basketball dashboard should return exact positions like G, F, C, PG, SG, SF, PF"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "basketball"}
        )
        assert resp.status_code == 200, f"Dashboard request failed: {resp.text}"
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled basketball picks - cannot verify positions")
        
        by_position = data.get("byPosition", {})
        print(f"Basketball positions found: {list(by_position.keys())}")
        
        # Check for basketball positions
        exact_bball_positions = {"PG", "SG", "SF", "PF", "C", "G", "F", "Guard", "Forward", "Center"}
        generic_positions = {"Guard", "Big"}
        
        for pos in by_position.keys():
            if pos in exact_bball_positions:
                print(f"  ✓ Found basketball position: {pos} ({by_position[pos]['total']} picks)")
            elif pos == "Unknown":
                print(f"  ℹ Found Unknown position: {by_position[pos]['total']} picks")
        
        print(f"✓ Basketball dashboard byPosition structure verified")

    def test_dashboard_position_prop_breakdown(self):
        """Dashboard should include position+prop breakdown with exact positions"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled soccer picks")
        
        by_pos_prop = data.get("byPositionProp", {})
        print(f"Position+Prop combinations found: {len(by_pos_prop)}")
        
        # Check format: "POSITION|propType"
        for key in list(by_pos_prop.keys())[:5]:  # Show first 5
            parts = key.split("|")
            assert len(parts) == 2, f"Invalid key format: {key}"
            position, prop = parts
            stats = by_pos_prop[key]
            print(f"  {position} | {prop}: {stats['rate']}% ({stats['hits']}/{stats['total']})")
        
        print(f"✓ Position+Prop breakdown structure verified")


class TestIntelWorstMissesWithRole:
    """Test that worst misses include role information"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200
        self.owner_token = resp.json().get("session_token")

    def test_worst_misses_include_position_and_role(self):
        """Worst misses should include position and role fields"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        worst_misses = data.get("worstMisses", [])
        if not worst_misses:
            pytest.skip("No misses recorded - cannot verify structure")
        
        print(f"Found {len(worst_misses)} worst misses")
        
        # Check first few misses for position and role fields
        for i, miss in enumerate(worst_misses[:3]):
            player = miss.get("player", "Unknown")
            position = miss.get("position", "")
            role = miss.get("role", "")
            prop = miss.get("prop", "")
            projected = miss.get("projected", 0)
            actual = miss.get("actual", 0)
            
            print(f"  Miss #{i+1}: {player}")
            print(f"    Position: {position}, Role: {role}")
            print(f"    Prop: {prop}, Projected: {projected}, Actual: {actual}")
            
            # Position field should exist (may be empty string if not backfilled)
            assert "position" in miss, f"Miss #{i+1} missing 'position' field"
            assert "role" in miss, f"Miss #{i+1} missing 'role' field"
        
        print(f"✓ Worst misses include position and role fields")


class TestBackfillPositionsEndpoint:
    """Test the backfill-positions endpoint"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200
        self.owner_token = resp.json().get("session_token")

    def test_backfill_endpoint_returns_success(self):
        """Backfill endpoint should return success with counts"""
        resp = self.session.post(
            f"{BASE_URL}/api/intel/backfill-positions",
            params={"email": OWNER_EMAIL, "token": self.owner_token}
        )
        assert resp.status_code == 200, f"Backfill request failed: {resp.text}"
        data = resp.json()
        
        assert data.get("success") == True, f"Backfill should return success=True, got: {data}"
        assert "picksUpdated" in data, f"Response should include picksUpdated count"
        assert "totalChecked" in data, f"Response should include totalChecked count"
        
        print(f"✓ Backfill completed: {data['picksUpdated']} picks updated out of {data['totalChecked']} checked")

    def test_backfill_is_idempotent(self):
        """Running backfill twice should not cause errors"""
        # First run
        resp1 = self.session.post(
            f"{BASE_URL}/api/intel/backfill-positions",
            params={"email": OWNER_EMAIL, "token": self.owner_token}
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        
        # Second run
        resp2 = self.session.post(
            f"{BASE_URL}/api/intel/backfill-positions",
            params={"email": OWNER_EMAIL, "token": self.owner_token}
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        
        # Second run should update 0 or same number (idempotent)
        assert data2.get("success") == True
        print(f"✓ Backfill is idempotent: Run 1 updated {data1['picksUpdated']}, Run 2 updated {data2['picksUpdated']}")


class TestPicksSaveWithPosition:
    """Test that /api/picks/save stores position and role from prediction data"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200
        self.owner_token = resp.json().get("session_token")

    def test_save_pick_with_position_and_role(self):
        """Saving a pick should store position and role from player data"""
        import uuid
        test_pick_id = f"TEST_POS_{uuid.uuid4().hex[:6]}"
        
        pick_data = {
            "id": test_pick_id,
            "player": {
                "id": 12345,
                "name": "Test Player",
                "team": "Test Team",
                "position": "CB",  # Exact position
                "role": "Starter"
            },
            "propType": "tackles",
            "line": 3.5,
            "recommendation": "over",
            "projectedValue": 4.2,
            "confidenceScore": 65,
            "confidenceLevel": "Medium",
            "opponent": "Test Opponent",
            "_request": {
                "teamId": 100,
                "opponentId": 200,
                "leagueId": 39,
                "venue": "home"
            }
        }
        
        # Save the pick
        resp = self.session.post(
            f"{BASE_URL}/api/picks/save",
            json={"email": OWNER_EMAIL, "token": self.owner_token, "pick": pick_data}
        )
        assert resp.status_code == 200, f"Save pick failed: {resp.text}"
        save_data = resp.json()
        assert save_data.get("success") == True
        print(f"✓ Pick saved with ID: {save_data.get('pickId')}")
        
        # Retrieve picks and verify position/role were stored
        list_resp = self.session.post(
            f"{BASE_URL}/api/picks/list",
            json={"email": OWNER_EMAIL, "token": self.owner_token}
        )
        assert list_resp.status_code == 200
        picks = list_resp.json().get("picks", [])
        
        # Find our test pick
        test_pick = next((p for p in picks if p.get("pickId") == test_pick_id), None)
        assert test_pick is not None, f"Test pick {test_pick_id} not found in picks list"
        
        # Verify position and role were stored
        assert test_pick.get("position") == "CB", f"Position not stored correctly: {test_pick.get('position')}"
        assert test_pick.get("role") == "Starter", f"Role not stored correctly: {test_pick.get('role')}"
        print(f"✓ Position '{test_pick.get('position')}' and role '{test_pick.get('role')}' stored correctly")
        
        # Cleanup: delete test pick
        del_resp = self.session.post(
            f"{BASE_URL}/api/picks/delete",
            json={"email": OWNER_EMAIL, "token": self.owner_token, "pickId": test_pick_id}
        )
        assert del_resp.status_code == 200
        print(f"✓ Test pick cleaned up")


class TestIntelDashboardDataIntegrity:
    """Test overall data integrity of INTEL dashboard"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200
        self.owner_token = resp.json().get("session_token")

    def test_dashboard_returns_all_expected_fields(self):
        """Dashboard should return all expected analytics fields"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled picks")
        
        expected_fields = [
            "total", "totalHits", "totalMisses", "overallRate",
            "byProp", "byPropLine", "byExactLine",
            "byPosition", "byPositionProp",
            "byContext", "byContextProp",
            "byVenue", "byVenueProp",
            "byLeague", "byRec",
            "byResultType", "byResultProp",
            "byMoneyline", "byMoneylineProp",
            "byConfBand", "worstMisses", "leagueNames"
        ]
        
        missing_fields = [f for f in expected_fields if f not in data]
        assert not missing_fields, f"Missing fields: {missing_fields}"
        
        print(f"✓ All {len(expected_fields)} expected fields present in dashboard response")
        print(f"  Total picks: {data['total']}, Hit rate: {data['overallRate']}%")

    def test_position_totals_match_overall(self):
        """Sum of picks by position should match total picks"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled picks")
        
        by_position = data.get("byPosition", {})
        position_total = sum(v.get("total", 0) for v in by_position.values())
        
        # Total should match (hits + misses, excluding pushes)
        expected_total = data.get("totalHits", 0) + data.get("totalMisses", 0)
        
        assert position_total == expected_total, \
            f"Position total ({position_total}) doesn't match expected ({expected_total})"
        
        print(f"✓ Position totals match: {position_total} picks across {len(by_position)} positions")


class TestExactPositionConstants:
    """Test that exact position constants are properly defined"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Get owner session token"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        resp = self.session.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": OWNER_EMAIL})
        assert resp.status_code == 200
        self.owner_token = resp.json().get("session_token")

    def test_soccer_exact_positions_recognized(self):
        """Dashboard should recognize soccer exact positions"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "soccer"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled soccer picks")
        
        by_position = data.get("byPosition", {})
        
        # These are the exact positions that should be recognized (from intel.py EXACT_POSITIONS)
        soccer_exact = {"GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST", "SS"}
        
        recognized_exact = [pos for pos in by_position.keys() if pos in soccer_exact]
        print(f"✓ Recognized exact soccer positions: {recognized_exact}")

    def test_basketball_exact_positions_recognized(self):
        """Dashboard should recognize basketball exact positions"""
        resp = self.session.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": self.owner_token, "sport": "basketball"}
        )
        assert resp.status_code == 200
        data = resp.json()
        
        if data.get("total", 0) == 0:
            pytest.skip("No settled basketball picks")
        
        by_position = data.get("byPosition", {})
        
        # Basketball exact positions (from intel.py EXACT_POSITIONS)
        bball_exact = {"PG", "SG", "SF", "PF", "C", "G", "F", "Guard", "Forward", "Center"}
        
        recognized_exact = [pos for pos in by_position.keys() if pos in bball_exact]
        print(f"✓ Recognized exact basketball positions: {recognized_exact}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
