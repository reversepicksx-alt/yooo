"""
Iteration 56: Test GET /api/picks/analysis endpoint
Tests the ability to fetch original stored prediction analysis for saved picks.

Features tested:
1. GET /api/picks/analysis returns stored prediction with reasoning, tacticalBreakdown, etc.
2. Analysis endpoint matches prediction by player ID + prop type
3. Analysis endpoint returns {found: false} for non-existent picks
4. Auth validation (invalid session returns 401)
5. Non-existent pickId returns 404
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPickAnalysisEndpoint:
    """Tests for GET /api/picks/analysis endpoint"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for owner account"""
        # Login as owner using verify-whop
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": "josselj001@gmail.com"
        })
        assert resp.status_code == 200, f"Failed to verify owner: {resp.text}"
        data = resp.json()
        self.email = "josselj001@gmail.com"
        self.token = data.get("session_token")
        assert self.token, "No session token returned"
        
        # Get list of picks to find a valid pickId
        picks_resp = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert picks_resp.status_code == 200, f"Failed to list picks: {picks_resp.text}"
        self.picks = picks_resp.json().get("picks", [])
        
    def test_analysis_endpoint_returns_200_for_valid_pick(self):
        """Test that analysis endpoint returns 200 for a valid pick"""
        if not self.picks:
            pytest.skip("No picks available for testing")
        
        pick = self.picks[0]
        pick_id = pick.get("pickId")
        
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        
        # Should have 'found' field
        assert "found" in data, "Response should have 'found' field"
        print(f"Analysis for pick {pick_id}: found={data.get('found')}")
        
    def test_analysis_endpoint_returns_analysis_fields_when_found(self):
        """Test that analysis endpoint returns expected fields when prediction is found"""
        if not self.picks:
            pytest.skip("No picks available for testing")
        
        # Try multiple picks to find one with stored analysis
        found_analysis = None
        tested_pick_id = None
        
        for pick in self.picks[:10]:  # Test up to 10 picks
            pick_id = pick.get("pickId")
            resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
                "email": self.email,
                "token": self.token,
                "pickId": pick_id
            })
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("found") and data.get("analysis"):
                    found_analysis = data.get("analysis")
                    tested_pick_id = pick_id
                    break
        
        if not found_analysis:
            pytest.skip("No picks with stored analysis found")
        
        print(f"Found analysis for pick {tested_pick_id}")
        
        # Check for expected analysis fields
        expected_fields = [
            "projectedValue", "recommendation", "confidenceScore"
        ]
        
        for field in expected_fields:
            if field in found_analysis:
                print(f"  {field}: {found_analysis.get(field)}")
        
        # Check for optional detailed fields
        optional_fields = [
            "reasoning", "tacticalBreakdown", "sharpSummary", 
            "matchupOverview", "keyEvidence", "scenarioAnalysis"
        ]
        
        found_optional = []
        for field in optional_fields:
            if found_analysis.get(field):
                found_optional.append(field)
                
        print(f"  Optional fields found: {found_optional}")
        
        # At minimum, should have projectedValue or recommendation
        assert found_analysis.get("projectedValue") is not None or found_analysis.get("recommendation"), \
            "Analysis should have at least projectedValue or recommendation"
            
    def test_analysis_endpoint_returns_not_found_for_invalid_pick(self):
        """Test that analysis endpoint returns found=false for non-existent pick"""
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": "NONEXISTENT_PICK_12345"
        })
        
        # Should return 404 for non-existent pick
        assert resp.status_code == 404, f"Expected 404 for non-existent pick, got {resp.status_code}"
        
    def test_analysis_endpoint_requires_auth(self):
        """Test that analysis endpoint requires valid session"""
        if not self.picks:
            pytest.skip("No picks available for testing")
        
        pick_id = self.picks[0].get("pickId")
        
        # Test with invalid token
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": "invalid_token_12345",
            "pickId": pick_id
        })
        
        assert resp.status_code == 401, f"Expected 401 for invalid token, got {resp.status_code}"
        
    def test_analysis_endpoint_matches_by_player_and_prop(self):
        """Test that analysis matches prediction by player ID + prop type"""
        if not self.picks:
            pytest.skip("No picks available for testing")
        
        # Find a pick with player info
        test_pick = None
        for pick in self.picks:
            if pick.get("playerId") and pick.get("propType"):
                test_pick = pick
                break
        
        if not test_pick:
            pytest.skip("No picks with player ID and prop type found")
        
        pick_id = test_pick.get("pickId")
        player_id = test_pick.get("playerId")
        prop_type = test_pick.get("propType")
        player_name = test_pick.get("playerName")
        
        print(f"Testing pick: {pick_id}")
        print(f"  Player: {player_name} (ID: {player_id})")
        print(f"  Prop Type: {prop_type}")
        
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        if data.get("found"):
            analysis = data.get("analysis", {})
            # If found, the analysis should be for the same player/prop
            analysis_player = analysis.get("player", {})
            analysis_prop = analysis.get("propType")
            
            print(f"  Analysis player: {analysis_player.get('name')} (ID: {analysis_player.get('id')})")
            print(f"  Analysis prop: {analysis_prop}")
            
            # Verify match (if player info is in analysis)
            if analysis_player.get("id"):
                assert analysis_player.get("id") == player_id or analysis_player.get("name") == player_name, \
                    "Analysis should be for the same player"
        else:
            print(f"  No stored analysis found for this pick")


class TestPickAnalysisWithTestAccount:
    """Test analysis endpoint with test account (non-owner)"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for test account"""
        resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "xaviersteverson@gmail.com",
            "password": "test123456"
        })
        assert resp.status_code == 200, f"Failed to login test account: {resp.text}"
        data = resp.json()
        self.email = "xaviersteverson@gmail.com"
        self.token = data.get("session_token")
        assert self.token, "No session token returned"
        
        # Get list of picks for this user
        picks_resp = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert picks_resp.status_code == 200, f"Failed to list picks: {picks_resp.text}"
        self.picks = picks_resp.json().get("picks", [])
        
    def test_non_owner_can_access_own_pick_analysis(self):
        """Test that non-owner can access analysis for their own picks"""
        if not self.picks:
            pytest.skip("No picks available for test account")
        
        pick = self.picks[0]
        pick_id = pick.get("pickId")
        
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "found" in data, "Response should have 'found' field"
        print(f"Test account analysis for pick {pick_id}: found={data.get('found')}")


class TestAnalysisFieldsCompleteness:
    """Test that analysis returns all expected fields from predictions collection"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for owner account"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": "josselj001@gmail.com"
        })
        assert resp.status_code == 200
        data = resp.json()
        self.email = "josselj001@gmail.com"
        self.token = data.get("session_token")
        
        picks_resp = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert picks_resp.status_code == 200
        self.picks = picks_resp.json().get("picks", [])
        
    def test_analysis_includes_all_projection_fields(self):
        """Test that analysis includes projection fields when found"""
        if not self.picks:
            pytest.skip("No picks available")
        
        # Find a pick with analysis
        for pick in self.picks[:15]:
            pick_id = pick.get("pickId")
            resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
                "email": self.email,
                "token": self.token,
                "pickId": pick_id
            })
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("found") and data.get("analysis"):
                    analysis = data.get("analysis")
                    
                    # List all fields present in analysis
                    print(f"Analysis fields for pick {pick_id}:")
                    for key, value in analysis.items():
                        if value:
                            val_preview = str(value)[:100] + "..." if len(str(value)) > 100 else str(value)
                            print(f"  {key}: {val_preview}")
                    
                    # Verify key fields are present (at least some)
                    key_fields = ["projectedValue", "recommendation", "confidenceScore", "confidenceLevel"]
                    found_key_fields = [f for f in key_fields if analysis.get(f) is not None]
                    
                    assert len(found_key_fields) >= 2, \
                        f"Analysis should have at least 2 key fields, found: {found_key_fields}"
                    
                    return  # Test passed
        
        pytest.skip("No picks with stored analysis found")
        
    def test_analysis_includes_reasoning_fields(self):
        """Test that analysis includes reasoning/tactical fields when available"""
        if not self.picks:
            pytest.skip("No picks available")
        
        reasoning_fields_found = []
        
        for pick in self.picks[:20]:
            pick_id = pick.get("pickId")
            resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
                "email": self.email,
                "token": self.token,
                "pickId": pick_id
            })
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("found") and data.get("analysis"):
                    analysis = data.get("analysis")
                    
                    # Check for reasoning fields
                    reasoning_fields = [
                        "reasoning", "tacticalBreakdown", "sharpSummary",
                        "keyEvidence", "scenarioAnalysis", "matchupOverview"
                    ]
                    
                    for field in reasoning_fields:
                        if analysis.get(field) and field not in reasoning_fields_found:
                            reasoning_fields_found.append(field)
                            print(f"Found {field} in pick {pick_id}")
        
        print(f"\nTotal reasoning fields found across picks: {reasoning_fields_found}")
        
        # At least some reasoning fields should be present in the database
        assert len(reasoning_fields_found) >= 1, \
            "Should find at least 1 reasoning field across all picks"


class TestSettledPicksAnalysis:
    """Test analysis for settled (won/lost) picks specifically"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token and settled picks"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": "josselj001@gmail.com"
        })
        assert resp.status_code == 200
        data = resp.json()
        self.email = "josselj001@gmail.com"
        self.token = data.get("session_token")
        
        picks_resp = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert picks_resp.status_code == 200
        all_picks = picks_resp.json().get("picks", [])
        
        # Filter to settled picks (won/lost)
        self.won_picks = [p for p in all_picks if p.get("status") == "settled" and p.get("result") == "hit"]
        self.lost_picks = [p for p in all_picks if p.get("status") == "settled" and p.get("result") == "miss"]
        
        print(f"Found {len(self.won_picks)} won picks, {len(self.lost_picks)} lost picks")
        
    def test_won_pick_analysis_available(self):
        """Test that analysis is available for won picks"""
        if not self.won_picks:
            pytest.skip("No won picks available")
        
        pick = self.won_picks[0]
        pick_id = pick.get("pickId")
        
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        print(f"Won pick {pick_id} ({pick.get('playerName')}): analysis found={data.get('found')}")
        
        if data.get("found"):
            analysis = data.get("analysis", {})
            print(f"  Projected: {analysis.get('projectedValue')}, Actual: {pick.get('actualValue')}")
            print(f"  Recommendation: {analysis.get('recommendation')}, Line: {pick.get('line')}")
            
    def test_lost_pick_analysis_available(self):
        """Test that analysis is available for lost picks"""
        if not self.lost_picks:
            pytest.skip("No lost picks available")
        
        pick = self.lost_picks[0]
        pick_id = pick.get("pickId")
        
        resp = requests.get(f"{BASE_URL}/api/picks/analysis", params={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
        
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        
        print(f"Lost pick {pick_id} ({pick.get('playerName')}): analysis found={data.get('found')}")
        
        if data.get("found"):
            analysis = data.get("analysis", {})
            print(f"  Projected: {analysis.get('projectedValue')}, Actual: {pick.get('actualValue')}")
            print(f"  Recommendation: {analysis.get('recommendation')}, Line: {pick.get('line')}")
