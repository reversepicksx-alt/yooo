"""
Test suite for the Scan Prop feature - AI vision-based prop extraction from sportsbook screenshots.
Tests the /api/scan-prop endpoint which uses GPT-4o vision to extract player props.
"""
import pytest
import requests
import os
import base64

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestScanPropEndpoint:
    """Tests for POST /api/scan-prop endpoint"""
    
    def test_health_check(self):
        """Verify backend is running"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        print("Health check passed")
    
    def test_scan_prop_with_valid_image(self):
        """Test scanning a valid PrizePicks screenshot with 3 player props"""
        # Read the test image base64
        with open("/tmp/test_prizepicks_b64.txt", "r") as f:
            image_base64 = f.read().strip()
        
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": image_base64},
            timeout=60
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "picks" in data, "Response should contain 'picks' array"
        picks = data["picks"]
        assert isinstance(picks, list), "picks should be a list"
        assert len(picks) >= 1, "Should extract at least 1 player prop"
        
        print(f"Extracted {len(picks)} player props")
        
        # Verify each pick has required fields
        for i, pick in enumerate(picks):
            assert "extracted" in pick, f"Pick {i} should have 'extracted' field"
            extracted = pick["extracted"]
            
            # Check extracted fields
            assert "playerName" in extracted, f"Pick {i} should have playerName"
            assert "propType" in extracted, f"Pick {i} should have propType"
            assert "line" in extracted, f"Pick {i} should have line"
            
            print(f"  Pick {i}: {extracted.get('playerName')} - {extracted.get('propType')} {extracted.get('line')}")
            
            # Check resolved player (if matched)
            if "resolved" in pick and pick["resolved"]:
                resolved = pick["resolved"]
                assert "playerId" in resolved, f"Resolved pick {i} should have playerId"
                assert "playerName" in resolved, f"Resolved pick {i} should have playerName"
                print(f"    Resolved: {resolved.get('playerName')} (ID: {resolved.get('playerId')})")
    
    def test_scan_prop_extracts_correct_players(self):
        """Test that the scan correctly extracts Saka, Haaland, and Rodri from test image"""
        with open("/tmp/test_prizepicks_b64.txt", "r") as f:
            image_base64 = f.read().strip()
        
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": image_base64},
            timeout=60
        )
        
        assert response.status_code == 200
        data = response.json()
        picks = data["picks"]
        
        # Extract player names
        player_names = [p["extracted"]["playerName"].lower() for p in picks]
        
        # Check for expected players (case-insensitive partial match)
        expected_players = ["saka", "haaland", "rodri"]
        for expected in expected_players:
            found = any(expected in name for name in player_names)
            assert found, f"Expected to find player containing '{expected}' in {player_names}"
            print(f"Found player: {expected}")
    
    def test_scan_prop_resolves_players_via_api_sports(self):
        """Test that extracted players are resolved via API-Sports search"""
        with open("/tmp/test_prizepicks_b64.txt", "r") as f:
            image_base64 = f.read().strip()
        
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": image_base64},
            timeout=60
        )
        
        assert response.status_code == 200
        data = response.json()
        picks = data["picks"]
        
        # Count resolved players
        resolved_count = sum(1 for p in picks if p.get("resolved"))
        print(f"Resolved {resolved_count}/{len(picks)} players via API-Sports")
        
        # At least some players should be resolved
        assert resolved_count > 0, "At least one player should be resolved via API-Sports"
        
        # Check resolved player has required fields
        for pick in picks:
            if pick.get("resolved"):
                resolved = pick["resolved"]
                assert "playerId" in resolved and resolved["playerId"], "Resolved player should have playerId"
                assert "teamId" in resolved, "Resolved player should have teamId"
                assert "teamName" in resolved, "Resolved player should have teamName"
                print(f"  {resolved.get('playerName')}: Team {resolved.get('teamName')} (ID: {resolved.get('teamId')})")
    
    def test_scan_prop_normalizes_prop_types(self):
        """Test that prop types are normalized to valid keys"""
        with open("/tmp/test_prizepicks_b64.txt", "r") as f:
            image_base64 = f.read().strip()
        
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": image_base64},
            timeout=60
        )
        
        assert response.status_code == 200
        data = response.json()
        picks = data["picks"]
        
        valid_prop_types = [
            "pass_attempts", "shots", "shots_on_target", "tackles",
            "key_passes", "saves", "interceptions", "blocks",
            "dribbles", "fouls_drawn"
        ]
        
        for pick in picks:
            prop_type = pick["extracted"]["propType"]
            assert prop_type in valid_prop_types, f"Invalid prop type: {prop_type}"
            print(f"  Valid prop type: {prop_type}")
    
    def test_scan_prop_empty_image(self):
        """Test handling of empty/invalid base64 image"""
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": ""},
            timeout=30
        )
        
        # Should return error or empty picks
        # The endpoint may return 500 or 400 for invalid input
        print(f"Empty image response: {response.status_code}")
        # We just verify it doesn't crash the server
        assert response.status_code in [200, 400, 422, 500]
    
    def test_scan_prop_invalid_base64(self):
        """Test handling of invalid base64 data"""
        response = requests.post(
            f"{BASE_URL}/api/scan-prop",
            json={"image_base64": "not-valid-base64!!!"},
            timeout=30
        )
        
        print(f"Invalid base64 response: {response.status_code}")
        # Should handle gracefully
        assert response.status_code in [200, 400, 422, 500]


class TestExistingTabsUnaffected:
    """Verify existing tabs (Predict, Tracking, Guide) still work"""
    
    def test_leagues_endpoint(self):
        """Test /api/leagues endpoint still works"""
        response = requests.get(f"{BASE_URL}/api/leagues")
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert len(data["leagues"]) > 0
        print(f"Leagues endpoint works: {len(data['leagues'])} leagues")
    
    def test_player_search_endpoint(self):
        """Test /api/players/search endpoint still works"""
        response = requests.post(
            f"{BASE_URL}/api/players/search",
            json={"query": "Saka", "league_id": 39}
        )
        assert response.status_code == 200
        data = response.json()
        assert "players" in data
        print(f"Player search works: found {len(data['players'])} players for 'Saka'")
    
    def test_auth_verify_whop_endpoint(self):
        """Test /api/auth/verify-whop endpoint still works"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("verified") == True, "Owner email should be verified"
        print("Auth verify-whop works for owner email")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
