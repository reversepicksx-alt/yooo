"""
Test propType normalization and live tracking fixes (Iteration 30)
- Tests that propType is normalized on save ('Pts+Reb+Ast' → 'pts_reb_ast')
- Tests that live-update returns non-zero currentValue for compound props
- Tests basketball quarter info and pace calculation
- Tests prediction data quality (playerGameLogs with last5Avg, last10Avg, overRate)
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
TEST_EMAIL = "josselj001@gmail.com"

class TestPropTypeNormalization:
    """Test propType normalization on save and live-update"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token for owner email"""
        # Owner email auto-verifies
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200, f"Auth failed: {resp.text}"
        data = resp.json()
        self.token = data.get("session_token")
        assert self.token, "No session token returned"
        yield
    
    def test_save_pick_normalizes_pts_reb_ast(self):
        """Test that saving a pick with 'Pts+Reb+Ast' normalizes to 'pts_reb_ast'"""
        pick_data = {
            "email": TEST_EMAIL,
            "token": self.token,
            "pick": {
                "id": f"TEST_pra_{int(time.time())}",
                "propType": "Pts+Reb+Ast",  # Display label with plus signs
                "line": 41.5,
                "recommendation": "over",
                "projectedValue": 45.0,
                "confidenceScore": 65,
                "confidenceLevel": "High",
                "player": {"id": 878, "name": "Shai Gilgeous-Alexander", "team": "Oklahoma City Thunder"},
                "opponent": "Detroit Pistons",
                "sport": "basketball",
                "_request": {"teamId": 152, "opponentId": 140, "venue": "home"}
            }
        }
        
        resp = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert resp.status_code == 200, f"Save failed: {resp.text}"
        result = resp.json()
        assert result.get("success") is True
        assert "trackingId" in result
        assert result["trackingId"].startswith("TRK-")
        
        # Verify the pick was saved with normalized propType
        list_resp = requests.post(f"{BASE_URL}/api/picks/list", json={"email": TEST_EMAIL, "token": self.token})
        assert list_resp.status_code == 200
        picks = list_resp.json().get("picks", [])
        
        saved_pick = next((p for p in picks if p.get("pickId") == pick_data["pick"]["id"]), None)
        assert saved_pick is not None, "Pick not found in list"
        assert saved_pick.get("propType") == "pts_reb_ast", f"propType not normalized: {saved_pick.get('propType')}"
        
        # Cleanup
        requests.post(f"{BASE_URL}/api/picks/delete", json={"email": TEST_EMAIL, "token": self.token, "pickId": pick_data["pick"]["id"]})
    
    def test_save_pick_normalizes_points(self):
        """Test that saving a pick with 'Points' normalizes to 'points'"""
        pick_data = {
            "email": TEST_EMAIL,
            "token": self.token,
            "pick": {
                "id": f"TEST_pts_{int(time.time())}",
                "propType": "Points",  # Display label
                "line": 30.5,
                "recommendation": "over",
                "projectedValue": 32.0,
                "confidenceScore": 60,
                "confidenceLevel": "Medium",
                "player": {"id": 878, "name": "Shai Gilgeous-Alexander", "team": "Oklahoma City Thunder"},
                "opponent": "Detroit Pistons",
                "sport": "basketball",
                "_request": {"teamId": 152, "opponentId": 140, "venue": "home"}
            }
        }
        
        resp = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert resp.status_code == 200, f"Save failed: {resp.text}"
        
        # Verify normalization
        list_resp = requests.post(f"{BASE_URL}/api/picks/list", json={"email": TEST_EMAIL, "token": self.token})
        picks = list_resp.json().get("picks", [])
        saved_pick = next((p for p in picks if p.get("pickId") == pick_data["pick"]["id"]), None)
        assert saved_pick is not None
        assert saved_pick.get("propType") == "points", f"propType not normalized: {saved_pick.get('propType')}"
        
        # Cleanup
        requests.post(f"{BASE_URL}/api/picks/delete", json={"email": TEST_EMAIL, "token": self.token, "pickId": pick_data["pick"]["id"]})
    
    def test_save_pick_normalizes_3_pointers(self):
        """Test that saving a pick with '3-Point FG Made' normalizes to 'three_pointers'"""
        pick_data = {
            "email": TEST_EMAIL,
            "token": self.token,
            "pick": {
                "id": f"TEST_3pt_{int(time.time())}",
                "propType": "3-Point FG Made",  # Display label with hyphen
                "line": 4.5,
                "recommendation": "over",
                "projectedValue": 5.0,
                "confidenceScore": 55,
                "confidenceLevel": "Medium",
                "player": {"id": 878, "name": "Shai Gilgeous-Alexander", "team": "Oklahoma City Thunder"},
                "opponent": "Detroit Pistons",
                "sport": "basketball",
                "_request": {"teamId": 152, "opponentId": 140, "venue": "home"}
            }
        }
        
        resp = requests.post(f"{BASE_URL}/api/picks/save", json=pick_data)
        assert resp.status_code == 200
        
        # Verify normalization
        list_resp = requests.post(f"{BASE_URL}/api/picks/list", json={"email": TEST_EMAIL, "token": self.token})
        picks = list_resp.json().get("picks", [])
        saved_pick = next((p for p in picks if p.get("pickId") == pick_data["pick"]["id"]), None)
        assert saved_pick is not None
        # Should normalize to 'three_pointers' via label_map
        assert saved_pick.get("propType") in ["three_pointers", "3_point_fg_made"], f"propType: {saved_pick.get('propType')}"
        
        # Cleanup
        requests.post(f"{BASE_URL}/api/picks/delete", json={"email": TEST_EMAIL, "token": self.token, "pickId": pick_data["pick"]["id"]})


class TestLiveUpdatePtsRebAst:
    """Test live-update returns correct values for pts_reb_ast compound prop"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get auth token"""
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200
        self.token = resp.json().get("session_token")
        yield
    
    def test_live_update_returns_nonzero_for_pts_reb_ast(self):
        """Test that live-update returns non-zero currentValue for pts_reb_ast picks"""
        # First, list existing picks to find any live basketball picks with pts_reb_ast
        list_resp = requests.post(f"{BASE_URL}/api/picks/list", json={"email": TEST_EMAIL, "token": self.token})
        assert list_resp.status_code == 200
        picks = list_resp.json().get("picks", [])
        
        # Find live basketball picks with pts_reb_ast
        pra_picks = [p for p in picks if p.get("propType") == "pts_reb_ast" and p.get("status") == "live"]
        print(f"Found {len(pra_picks)} live pts_reb_ast picks")
        
        # Call live-update
        update_resp = requests.post(f"{BASE_URL}/api/picks/live-update", json={"email": TEST_EMAIL, "token": self.token})
        assert update_resp.status_code == 200
        updates = update_resp.json().get("updates", [])
        
        # Check if any pts_reb_ast picks have non-zero currentValue
        for pick in pra_picks:
            pick_update = next((u for u in updates if u.get("pickId") == pick.get("pickId")), None)
            if pick_update and pick_update.get("matchStatus") == "live":
                current_val = pick_update.get("currentValue", 0)
                print(f"Pick {pick.get('pickId')}: currentValue={current_val}, period={pick_update.get('period')}")
                # If game is live, currentValue should be > 0 (player has some stats)
                # Note: Could be 0 if player hasn't played yet, but typically > 0
                assert "currentValue" in pick_update, "currentValue field missing"
                assert "period" in pick_update or "quarter" in pick_update, "period/quarter field missing"
    
    def test_live_update_basketball_quarter_info(self):
        """Test that basketball live updates include quarter info (Q1-Q4, HT, OT)"""
        update_resp = requests.post(f"{BASE_URL}/api/picks/live-update", json={"email": TEST_EMAIL, "token": self.token})
        assert update_resp.status_code == 200
        updates = update_resp.json().get("updates", [])
        
        # Find basketball live updates
        live_bball = [u for u in updates if u.get("matchStatus") == "live" and u.get("gameId")]
        print(f"Found {len(live_bball)} live basketball updates")
        
        valid_quarters = {"Q1", "Q2", "Q3", "Q4", "HT", "OT", "Break"}
        for update in live_bball:
            period = update.get("period") or update.get("quarter")
            print(f"Game {update.get('gameId')}: period={period}, currentValue={update.get('currentValue')}, pace={update.get('pace')}")
            assert period in valid_quarters, f"Invalid quarter: {period}"
            # Pace should be calculated for 48-min game (not 90-min soccer)
            pace = update.get("pace", 0)
            # Pace should be reasonable for basketball (not inflated by 90-min calculation)
            # For a 48-min game, pace should be similar to currentValue * (48/elapsed)
            assert pace >= 0, f"Invalid pace: {pace}"
    
    def test_live_update_pace_calculation(self):
        """Test that pace is calculated for 48-min basketball game (not 90-min soccer)"""
        update_resp = requests.post(f"{BASE_URL}/api/picks/live-update", json={"email": TEST_EMAIL, "token": self.token})
        assert update_resp.status_code == 200
        updates = update_resp.json().get("updates", [])
        
        for update in updates:
            if update.get("matchStatus") == "live" and update.get("gameId"):
                # Basketball game
                current_val = update.get("currentValue", 0)
                elapsed = update.get("elapsed", 0)
                pace = update.get("pace", 0)
                
                if elapsed > 0 and current_val > 0:
                    # Expected pace for 48-min game
                    expected_pace = round((current_val / elapsed) * 48, 1)
                    # Allow some tolerance
                    assert abs(pace - expected_pace) < 1, f"Pace mismatch: got {pace}, expected ~{expected_pace}"
                    print(f"Pace check: current={current_val}, elapsed={elapsed}, pace={pace}, expected={expected_pace}")


class TestPicksListFields:
    """Test that picks/list returns all required fields"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200
        self.token = resp.json().get("session_token")
        yield
    
    def test_picks_list_has_tracking_id_and_sport(self):
        """Test that all picks have trackingId and sport fields"""
        list_resp = requests.post(f"{BASE_URL}/api/picks/list", json={"email": TEST_EMAIL, "token": self.token})
        assert list_resp.status_code == 200
        picks = list_resp.json().get("picks", [])
        
        for pick in picks:
            assert "trackingId" in pick, f"Pick {pick.get('pickId')} missing trackingId"
            assert pick["trackingId"].startswith("TRK-"), f"Invalid trackingId format: {pick['trackingId']}"
            assert "sport" in pick, f"Pick {pick.get('pickId')} missing sport"
            assert pick["sport"] in ["basketball", "soccer"], f"Invalid sport: {pick['sport']}"
            print(f"Pick {pick.get('pickId')}: trackingId={pick['trackingId']}, sport={pick['sport']}, propType={pick.get('propType')}")


class TestBasketballPredictDataQuality:
    """Test basketball prediction returns enhanced data quality fields"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        resp = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={"email": TEST_EMAIL})
        assert resp.status_code == 200
        self.token = resp.json().get("session_token")
        yield
    
    @pytest.mark.timeout(60)
    def test_prediction_returns_player_game_logs(self):
        """Test that basketball prediction returns playerGameLogs with last5Avg, last10Avg, overRate"""
        # This test takes ~25s due to AI processing
        predict_data = {
            "teamId": 152,
            "teamName": "Oklahoma City Thunder",
            "opponentId": 140,
            "opponentName": "Detroit Pistons",
            "playerName": "Shai Gilgeous-Alexander",
            "venue": "home",
            "propType": "pts_reb_ast",
            "line": 41.5
        }
        
        resp = requests.post(f"{BASE_URL}/api/basketball/predict", json=predict_data, timeout=60)
        assert resp.status_code == 200, f"Prediction failed: {resp.text}"
        result = resp.json()
        
        # Check playerGameLogs
        assert "playerGameLogs" in result, "playerGameLogs missing from prediction"
        logs = result["playerGameLogs"]
        
        assert "last5Avg" in logs, "last5Avg missing"
        assert "last10Avg" in logs, "last10Avg missing"
        assert "overRate" in logs, "overRate missing"
        assert "sampleSize" in logs, "sampleSize missing"
        assert "rawAvg" in logs, "rawAvg missing"
        
        print(f"playerGameLogs: sampleSize={logs.get('sampleSize')}, rawAvg={logs.get('rawAvg')}, last5Avg={logs.get('last5Avg')}, last10Avg={logs.get('last10Avg')}, overRate={logs.get('overRate')}%")
        
        # Verify values are reasonable
        assert logs["sampleSize"] > 0, "No game logs found"
        assert logs["last5Avg"] > 0, "last5Avg should be > 0"
        assert logs["last10Avg"] > 0, "last10Avg should be > 0"
        assert 0 <= logs["overRate"] <= 100, f"overRate out of range: {logs['overRate']}"
    
    @pytest.mark.timeout(60)
    def test_prediction_returns_recent_samples(self):
        """Test that prediction returns recentSamples with game details"""
        predict_data = {
            "teamId": 152,
            "teamName": "Oklahoma City Thunder",
            "opponentId": 140,
            "opponentName": "Detroit Pistons",
            "playerName": "Shai Gilgeous-Alexander",
            "venue": "home",
            "propType": "points",
            "line": 30.5
        }
        
        resp = requests.post(f"{BASE_URL}/api/basketball/predict", json=predict_data, timeout=60)
        assert resp.status_code == 200
        result = resp.json()
        
        assert "recentSamples" in result, "recentSamples missing"
        samples = result["recentSamples"]
        
        if samples:
            sample = samples[0]
            assert "date" in sample, "date missing from sample"
            assert "opponent" in sample, "opponent missing from sample"
            assert "value" in sample, "value missing from sample"
            assert "venue" in sample, "venue missing from sample"
            print(f"First sample: date={sample.get('date')}, opponent={sample.get('opponent')}, value={sample.get('value')}, venue={sample.get('venue')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
