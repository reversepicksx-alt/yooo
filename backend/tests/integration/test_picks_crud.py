"""
Test suite for Picks CRUD endpoints and Live Update functionality
Tests: /api/picks/save, /api/picks/list, /api/picks/delete, /api/picks/live-update
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials - owner email bypasses password via verify-whop
OWNER_EMAIL = "josselj001@gmail.com"


class TestPicksCRUD:
    """Test suite for picks CRUD operations"""
    
    @pytest.fixture(scope="class")
    def auth_session(self):
        """Get authenticated session for owner"""
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        
        # Authenticate via verify-whop (owner bypass)
        response = session.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": OWNER_EMAIL
        })
        
        if response.status_code != 200:
            pytest.skip(f"Authentication failed: {response.status_code} - {response.text}")
        
        data = response.json()
        token = data.get("session_token") or data.get("token")
        
        if not token:
            pytest.skip(f"No token in response: {data}")
        
        return {"email": OWNER_EMAIL, "token": token, "session": session}
    
    @pytest.fixture
    def test_pick_data(self):
        """Sample pick data for testing"""
        return {
            "id": f"TEST_{int(time.time())}",
            "player": {
                "id": 12345,
                "name": "Test Player",
                "team": "Test FC"
            },
            "opponent": "Opponent FC",
            "propType": "shots",
            "line": 2.5,
            "recommendation": "over",
            "projectedValue": 3.2,
            "confidenceScore": 75,
            "confidenceLevel": "High",
            "confidenceInterval": [2.0, 4.5],
            "_request": {
                "teamId": 100,
                "opponentId": 200,
                "leagueId": 39,
                "venue": "home"
            }
        }
    
    def test_health_check(self):
        """Test API health endpoint"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Health check failed: {response.text}"
        print("✓ Health check passed")
    
    def test_save_pick_success(self, auth_session, test_pick_data):
        """Test POST /api/picks/save - should save a pick and return pickId"""
        response = auth_session["session"].post(f"{BASE_URL}/api/picks/save", json={
            "email": auth_session["email"],
            "token": auth_session["token"],
            "pick": test_pick_data
        })
        
        assert response.status_code == 200, f"Save pick failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert data.get("success") is True, f"Expected success=True, got: {data}"
        assert "pickId" in data, f"Expected pickId in response, got: {data}"
        
        # Store pickId for cleanup
        auth_session["test_pick_id"] = data["pickId"]
        print(f"✓ Save pick passed - pickId: {data['pickId']}")
    
    def test_save_pick_invalid_session(self, test_pick_data):
        """Test POST /api/picks/save with invalid token - should return 401"""
        response = requests.post(f"{BASE_URL}/api/picks/save", json={
            "email": OWNER_EMAIL,
            "token": "invalid_token_12345",
            "pick": test_pick_data
        }, headers={"Content-Type": "application/json"})
        
        assert response.status_code == 401, f"Expected 401, got: {response.status_code}"
        print("✓ Save pick with invalid session returns 401")
    
    def test_list_picks_success(self, auth_session):
        """Test POST /api/picks/list - should return all picks for user"""
        response = auth_session["session"].post(f"{BASE_URL}/api/picks/list", json={
            "email": auth_session["email"],
            "token": auth_session["token"]
        })
        
        assert response.status_code == 200, f"List picks failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "picks" in data, f"Expected 'picks' in response, got: {data}"
        assert isinstance(data["picks"], list), f"Expected picks to be a list, got: {type(data['picks'])}"
        
        # Verify the test pick we saved is in the list
        if auth_session.get("test_pick_id"):
            pick_ids = [p.get("pickId") for p in data["picks"]]
            assert auth_session["test_pick_id"] in pick_ids, f"Test pick not found in list: {pick_ids}"
        
        print(f"✓ List picks passed - found {len(data['picks'])} picks")
    
    def test_list_picks_invalid_session(self):
        """Test POST /api/picks/list with invalid token - should return 401"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": OWNER_EMAIL,
            "token": "invalid_token_12345"
        }, headers={"Content-Type": "application/json"})
        
        assert response.status_code == 401, f"Expected 401, got: {response.status_code}"
        print("✓ List picks with invalid session returns 401")
    
    def test_live_update_picks_success(self, auth_session):
        """Test POST /api/picks/live-update - should return updates for live picks"""
        response = auth_session["session"].post(f"{BASE_URL}/api/picks/live-update", json={
            "email": auth_session["email"],
            "token": auth_session["token"]
        })
        
        assert response.status_code == 200, f"Live update failed: {response.status_code} - {response.text}"
        
        data = response.json()
        assert "updates" in data, f"Expected 'updates' in response, got: {data}"
        assert isinstance(data["updates"], list), f"Expected updates to be a list, got: {type(data['updates'])}"
        
        # If there are updates, verify structure
        if data["updates"]:
            update = data["updates"][0]
            assert "pickId" in update, f"Expected pickId in update, got: {update}"
            assert "matchStatus" in update, f"Expected matchStatus in update, got: {update}"
            print(f"✓ Live update passed - {len(data['updates'])} updates, first status: {update.get('matchStatus')}")
        else:
            print("✓ Live update passed - no live picks to update (expected if no matches)")
    
    def test_live_update_invalid_session(self):
        """Test POST /api/picks/live-update with invalid token - should return 401"""
        response = requests.post(f"{BASE_URL}/api/picks/live-update", json={
            "email": OWNER_EMAIL,
            "token": "invalid_token_12345"
        }, headers={"Content-Type": "application/json"})
        
        assert response.status_code == 401, f"Expected 401, got: {response.status_code}"
        print("✓ Live update with invalid session returns 401")
    
    def test_delete_pick_success(self, auth_session, test_pick_data):
        """Test POST /api/picks/delete - should delete a pick by pickId"""
        # First save a pick to delete
        save_response = auth_session["session"].post(f"{BASE_URL}/api/picks/save", json={
            "email": auth_session["email"],
            "token": auth_session["token"],
            "pick": {**test_pick_data, "id": f"DELETE_TEST_{int(time.time())}"}
        })
        
        assert save_response.status_code == 200, f"Save for delete test failed: {save_response.text}"
        pick_id = save_response.json().get("pickId")
        
        # Now delete it
        delete_response = auth_session["session"].post(f"{BASE_URL}/api/picks/delete", json={
            "email": auth_session["email"],
            "token": auth_session["token"],
            "pickId": pick_id
        })
        
        assert delete_response.status_code == 200, f"Delete pick failed: {delete_response.status_code} - {delete_response.text}"
        
        data = delete_response.json()
        assert data.get("success") is True, f"Expected success=True, got: {data}"
        
        # Verify pick is no longer in list
        list_response = auth_session["session"].post(f"{BASE_URL}/api/picks/list", json={
            "email": auth_session["email"],
            "token": auth_session["token"]
        })
        
        picks = list_response.json().get("picks", [])
        pick_ids = [p.get("pickId") for p in picks]
        assert pick_id not in pick_ids, f"Deleted pick still in list: {pick_ids}"
        
        print(f"✓ Delete pick passed - pickId {pick_id} removed")
    
    def test_delete_pick_invalid_session(self):
        """Test POST /api/picks/delete with invalid token - should return 401"""
        response = requests.post(f"{BASE_URL}/api/picks/delete", json={
            "email": OWNER_EMAIL,
            "token": "invalid_token_12345",
            "pickId": "some_pick_id"
        }, headers={"Content-Type": "application/json"})
        
        assert response.status_code == 401, f"Expected 401, got: {response.status_code}"
        print("✓ Delete pick with invalid session returns 401")
    
    def test_pick_data_persistence(self, auth_session, test_pick_data):
        """Test that saved pick data is correctly persisted and retrieved"""
        unique_pick = {
            **test_pick_data,
            "id": f"PERSIST_TEST_{int(time.time())}",
            "player": {
                "id": 99999,
                "name": "Persistence Test Player",
                "team": "Persistence FC"
            },
            "propType": "tackles",
            "line": 4.5,
            "recommendation": "under",
            "projectedValue": 3.8,
            "confidenceScore": 82
        }
        
        # Save the pick
        save_response = auth_session["session"].post(f"{BASE_URL}/api/picks/save", json={
            "email": auth_session["email"],
            "token": auth_session["token"],
            "pick": unique_pick
        })
        
        assert save_response.status_code == 200
        pick_id = save_response.json().get("pickId")
        
        # Retrieve and verify
        list_response = auth_session["session"].post(f"{BASE_URL}/api/picks/list", json={
            "email": auth_session["email"],
            "token": auth_session["token"]
        })
        
        picks = list_response.json().get("picks", [])
        saved_pick = next((p for p in picks if p.get("pickId") == pick_id), None)
        
        assert saved_pick is not None, f"Saved pick not found in list"
        assert saved_pick.get("playerName") == "Persistence Test Player", f"Player name mismatch: {saved_pick.get('playerName')}"
        assert saved_pick.get("teamName") == "Persistence FC", f"Team name mismatch: {saved_pick.get('teamName')}"
        assert saved_pick.get("propType") == "tackles", f"Prop type mismatch: {saved_pick.get('propType')}"
        assert saved_pick.get("line") == 4.5, f"Line mismatch: {saved_pick.get('line')}"
        assert saved_pick.get("recommendation") == "under", f"Recommendation mismatch: {saved_pick.get('recommendation')}"
        assert saved_pick.get("projectedValue") == 3.8, f"Projected value mismatch: {saved_pick.get('projectedValue')}"
        assert saved_pick.get("confidenceScore") == 82, f"Confidence score mismatch: {saved_pick.get('confidenceScore')}"
        
        # Cleanup
        auth_session["session"].post(f"{BASE_URL}/api/picks/delete", json={
            "email": auth_session["email"],
            "token": auth_session["token"],
            "pickId": pick_id
        })
        
        print("✓ Pick data persistence verified - all fields correctly saved and retrieved")
    
    def test_cleanup_test_picks(self, auth_session):
        """Cleanup any TEST_ prefixed picks created during testing"""
        list_response = auth_session["session"].post(f"{BASE_URL}/api/picks/list", json={
            "email": auth_session["email"],
            "token": auth_session["token"]
        })
        
        picks = list_response.json().get("picks", [])
        test_picks = [p for p in picks if p.get("pickId", "").startswith("TEST_") or 
                      p.get("pickId", "").startswith("DELETE_") or
                      p.get("pickId", "").startswith("PERSIST_")]
        
        for pick in test_picks:
            auth_session["session"].post(f"{BASE_URL}/api/picks/delete", json={
                "email": auth_session["email"],
                "token": auth_session["token"],
                "pickId": pick["pickId"]
            })
        
        print(f"✓ Cleanup completed - removed {len(test_picks)} test picks")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
