"""
Test tracking features for picks:
- trackingId generation and persistence
- sport field on picks (basketball/soccer)
- live-update endpoint for basketball with quarter info
- propLabel display
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials - owner email auto-verifies
TEST_EMAIL = "josselj001@gmail.com"


class TestPicksTrackingFeatures:
    """Test tracking ID and sport field on picks"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for owner email via verify-whop (auto-verifies)"""
        # Owner email auto-verifies via Whop verify endpoint
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": TEST_EMAIL
        })
        assert response.status_code == 200, f"Verify-whop failed: {response.text}"
        data = response.json()
        assert data.get("verified") == True, f"Owner email not verified: {data}"
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
        assert self.token, "No session token returned"
        yield
    
    def test_save_pick_returns_tracking_id(self):
        """POST /api/picks/save should return trackingId in response"""
        # Create a test basketball pick
        pick_data = {
            "email": self.email,
            "token": self.token,
            "pick": {
                "id": f"test-{int(time.time())}",
                "sport": "basketball",
                "player": {"id": 123, "name": "Test Player", "team": "Test Team"},
                "propType": "points",
                "line": 25.5,
                "recommendation": "over",
                "projectedValue": 28,
                "confidenceScore": 75,
                "confidenceLevel": "High",
                "opponent": "Opponent Team",
                "_request": {"teamId": 155, "opponentId": 146, "leagueId": 12, "venue": "home"}
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert response.status_code == 200, f"Save pick failed: {response.text}"
        
        data = response.json()
        assert data.get("success") == True, "Save pick did not return success"
        assert "trackingId" in data, "Response missing trackingId"
        assert data["trackingId"].startswith("TRK-"), f"trackingId format wrong: {data['trackingId']}"
        assert len(data["trackingId"]) == 12, f"trackingId length wrong: {data['trackingId']}"  # TRK-XXXXXXXX
        
        print(f"✓ Save pick returned trackingId: {data['trackingId']}")
        
        # Clean up - delete the test pick
        requests.post(f"{BASE_URL}/api/picks/delete", json={
            "email": self.email,
            "token": self.token,
            "pickId": data["pickId"]
        })
    
    def test_list_picks_returns_tracking_id_and_sport(self):
        """POST /api/picks/list should return trackingId and sport on every pick"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200, f"List picks failed: {response.text}"
        
        data = response.json()
        picks = data.get("picks", [])
        
        print(f"Found {len(picks)} picks")
        
        # Check each pick has trackingId and sport
        for pick in picks:
            pick_id = pick.get("pickId", "unknown")
            
            # Check trackingId
            assert "trackingId" in pick, f"Pick {pick_id} missing trackingId"
            assert pick["trackingId"].startswith("TRK-"), f"Pick {pick_id} trackingId format wrong: {pick['trackingId']}"
            
            # Check sport field
            assert "sport" in pick, f"Pick {pick_id} missing sport field"
            assert pick["sport"] in ["basketball", "soccer"], f"Pick {pick_id} has invalid sport: {pick['sport']}"
            
            print(f"✓ Pick {pick_id}: trackingId={pick['trackingId']}, sport={pick['sport']}, player={pick.get('playerName', 'N/A')}")
        
        print(f"✓ All {len(picks)} picks have trackingId and sport fields")
    
    def test_list_picks_backfills_tracking_id(self):
        """Verify that old picks without trackingId get backfilled"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        data = response.json()
        picks = data.get("picks", [])
        
        # All picks should have trackingId after backfill
        picks_without_tracking = [p for p in picks if not p.get("trackingId")]
        assert len(picks_without_tracking) == 0, f"Found {len(picks_without_tracking)} picks without trackingId"
        
        print(f"✓ All {len(picks)} picks have trackingId (backfill working)")
    
    def test_live_update_basketball_picks(self):
        """POST /api/picks/live-update should return basketball-specific fields"""
        response = requests.post(f"{BASE_URL}/api/picks/live-update", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200, f"Live update failed: {response.text}"
        
        data = response.json()
        updates = data.get("updates", [])
        
        print(f"Got {len(updates)} live updates")
        
        # Check basketball picks have quarter info
        for update in updates:
            pick_id = update.get("pickId", "unknown")
            match_status = update.get("matchStatus", "unknown")
            
            print(f"  Pick {pick_id}: status={match_status}")
            
            if match_status == "live":
                # Live basketball games should have quarter info
                if "quarter" in update or "period" in update:
                    quarter = update.get("quarter") or update.get("period")
                    print(f"    ✓ Quarter/Period: {quarter}")
                
                # Should have currentValue, pace, hitPct
                if "currentValue" in update:
                    print(f"    ✓ currentValue: {update['currentValue']}")
                if "pace" in update:
                    print(f"    ✓ pace: {update['pace']}")
                if "hitPct" in update:
                    print(f"    ✓ hitPct: {update['hitPct']}%")
                if "matchScore" in update:
                    print(f"    ✓ matchScore: {update['matchScore']}")
        
        print(f"✓ Live update endpoint working")
    
    def test_basketball_pick_has_correct_sport(self):
        """Basketball picks should have sport='basketball'"""
        # Create a basketball pick
        pick_data = {
            "email": self.email,
            "token": self.token,
            "pick": {
                "id": f"bball-test-{int(time.time())}",
                "sport": "basketball",
                "player": {"id": 456, "name": "Basketball Player", "team": "NBA Team"},
                "propType": "points",
                "line": 20.5,
                "recommendation": "over",
                "projectedValue": 24,
                "confidenceScore": 70,
                "confidenceLevel": "Medium",
                "opponent": "Other Team",
                "_request": {"teamId": 145, "opponentId": 133, "leagueId": 12, "venue": "home"}
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert response.status_code == 200
        pick_id = response.json()["pickId"]
        
        # Verify sport field in list
        list_response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert list_response.status_code == 200
        
        picks = list_response.json().get("picks", [])
        saved_pick = next((p for p in picks if p["pickId"] == pick_id), None)
        
        assert saved_pick is not None, f"Could not find saved pick {pick_id}"
        assert saved_pick.get("sport") == "basketball", f"Sport should be 'basketball', got: {saved_pick.get('sport')}"
        
        print(f"✓ Basketball pick has sport='basketball'")
        
        # Clean up
        requests.post(f"{BASE_URL}/api/picks/delete", json={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })
    
    def test_soccer_pick_has_correct_sport(self):
        """Soccer picks should have sport='soccer'"""
        # Create a soccer pick
        pick_data = {
            "email": self.email,
            "token": self.token,
            "pick": {
                "id": f"soccer-test-{int(time.time())}",
                "sport": "soccer",
                "player": {"id": 789, "name": "Soccer Player", "team": "Soccer Team"},
                "propType": "shots",
                "line": 2.5,
                "recommendation": "over",
                "projectedValue": 3,
                "confidenceScore": 65,
                "confidenceLevel": "Medium",
                "opponent": "Other Soccer Team",
                "_request": {"teamId": 50, "opponentId": 51, "leagueId": 39, "venue": "away"}
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert response.status_code == 200
        pick_id = response.json()["pickId"]
        
        # Verify sport field in list
        list_response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert list_response.status_code == 200
        
        picks = list_response.json().get("picks", [])
        saved_pick = next((p for p in picks if p["pickId"] == pick_id), None)
        
        assert saved_pick is not None, f"Could not find saved pick {pick_id}"
        assert saved_pick.get("sport") == "soccer", f"Sport should be 'soccer', got: {saved_pick.get('sport')}"
        
        print(f"✓ Soccer pick has sport='soccer'")
        
        # Clean up
        requests.post(f"{BASE_URL}/api/picks/delete", json={
            "email": self.email,
            "token": self.token,
            "pickId": pick_id
        })


class TestExistingPicksVerification:
    """Verify existing picks in the database have correct fields"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for owner email via verify-whop"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": TEST_EMAIL
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
        yield
    
    def test_verify_sga_pick_tracking(self):
        """Verify SGA pick (teonas1) has trackingId and basketball sport"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        picks = response.json().get("picks", [])
        sga_pick = next((p for p in picks if "teonas1" in p.get("pickId", "")), None)
        
        if sga_pick:
            print(f"Found SGA pick: {sga_pick.get('playerName')}")
            assert sga_pick.get("trackingId"), "SGA pick missing trackingId"
            assert sga_pick.get("sport") == "basketball", f"SGA pick should be basketball, got: {sga_pick.get('sport')}"
            print(f"✓ SGA pick: trackingId={sga_pick['trackingId']}, sport={sga_pick['sport']}")
        else:
            print("SGA pick (teonas1) not found - may have been deleted")
    
    def test_verify_jalen_green_pick(self):
        """Verify Jalen Green pick (ouljbis) has trackingId"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        picks = response.json().get("picks", [])
        jg_pick = next((p for p in picks if "ouljbis" in p.get("pickId", "")), None)
        
        if jg_pick:
            print(f"Found Jalen Green pick: {jg_pick.get('playerName')}")
            assert jg_pick.get("trackingId"), "Jalen Green pick missing trackingId"
            print(f"✓ Jalen Green pick: trackingId={jg_pick['trackingId']}, result={jg_pick.get('result')}")
        else:
            print("Jalen Green pick (ouljbis) not found - may have been deleted")
    
    def test_verify_hojbjerg_pick(self):
        """Verify P. Hojbjerg pick (cxpmaww) has trackingId and soccer sport"""
        response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        picks = response.json().get("picks", [])
        hoj_pick = next((p for p in picks if "cxpmaww" in p.get("pickId", "")), None)
        
        if hoj_pick:
            print(f"Found Hojbjerg pick: {hoj_pick.get('playerName')}")
            assert hoj_pick.get("trackingId"), "Hojbjerg pick missing trackingId"
            assert hoj_pick.get("sport") == "soccer", f"Hojbjerg pick should be soccer, got: {hoj_pick.get('sport')}"
            print(f"✓ Hojbjerg pick: trackingId={hoj_pick['trackingId']}, sport={hoj_pick['sport']}")
        else:
            print("Hojbjerg pick (cxpmaww) not found - may have been deleted")


class TestLiveUpdateSportSeparation:
    """Test that live-update correctly separates basketball and soccer picks"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token via verify-whop"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": TEST_EMAIL
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True
        self.token = data.get("session_token")
        self.email = TEST_EMAIL
        yield
    
    def test_live_update_returns_updates(self):
        """Live update should return updates array"""
        response = requests.post(f"{BASE_URL}/api/picks/live-update", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        data = response.json()
        assert "updates" in data, "Response missing 'updates' field"
        
        updates = data["updates"]
        print(f"Live update returned {len(updates)} updates")
        
        for u in updates:
            print(f"  - {u.get('pickId')}: status={u.get('matchStatus')}, quarter={u.get('quarter', 'N/A')}")
    
    def test_live_update_basketball_has_quarter(self):
        """Basketball live updates should include quarter field"""
        # First get picks to find basketball ones
        list_response = requests.post(f"{BASE_URL}/api/picks/list", json={
            "email": self.email,
            "token": self.token
        })
        assert list_response.status_code == 200
        
        picks = list_response.json().get("picks", [])
        basketball_picks = [p for p in picks if p.get("sport") == "basketball" and p.get("status") == "live"]
        
        print(f"Found {len(basketball_picks)} live basketball picks")
        
        if not basketball_picks:
            pytest.skip("No live basketball picks to test")
        
        # Get live updates
        response = requests.post(f"{BASE_URL}/api/picks/live-update", json={
            "email": self.email,
            "token": self.token
        })
        assert response.status_code == 200
        
        updates = response.json().get("updates", [])
        basketball_pick_ids = {p["pickId"] for p in basketball_picks}
        
        for update in updates:
            if update.get("pickId") in basketball_pick_ids:
                if update.get("matchStatus") == "live":
                    # Live basketball games should have quarter
                    assert "quarter" in update or "period" in update, f"Live basketball update missing quarter: {update}"
                    print(f"✓ Basketball update {update['pickId']}: quarter={update.get('quarter') or update.get('period')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
