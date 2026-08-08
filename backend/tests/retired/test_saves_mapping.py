"""
Test file for verifying the 'saves' prop type mapping fix.
Bug: 'saves' prop was showing pass_attempts values instead of actual saves.
Fix: Added 'goals_saves' extraction and 'saves' -> 'goals_saves' mapping in 3 locations.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


def get_auth_token():
    """Helper to get authentication token"""
    auth_response = requests.post(
        f"{BASE_URL}/api/auth/verify-whop",
        json={"email": "josselj001@gmail.com"},
        timeout=30
    )
    return auth_response.json().get("session_token")


class TestSavesPropMapping:
    """Tests for verifying saves prop type correctly maps to goals_saves field"""
    
    def test_health_check(self):
        """Verify API is accessible"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        print("✓ Health check passed")
    
    def test_predict_saves_prop_returns_reasonable_values(self):
        """
        Test that propType='saves' returns reasonable save values (not pass_attempts).
        Uses a Premier League goalkeeper (Alisson - ID 882, Liverpool team 40).
        
        Bug was: saves prop showed values like 28, 17, 31, 35 (pass_attempts)
        Fix: saves prop should show values like 2, 5, 3, 4 (actual saves)
        """
        token = get_auth_token()
        assert token, "Failed to get auth token"
        print("✓ Owner authentication successful")
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # Test predict endpoint with saves prop type
        predict_payload = {
            "playerId": 882,  # Alisson
            "playerName": "Alisson",
            "teamId": 40,     # Liverpool
            "opponentId": 50, # Man City
            "opponentName": "Manchester City",
            "leagueId": 39,   # Premier League
            "propType": "saves",
            "line": 2.5,
            "venue": "home"
        }
        
        print("Testing predict endpoint with saves prop for Alisson (ID 882)...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=predict_payload,
            headers=headers,
            timeout=120
        )
        
        assert response.status_code == 200, f"Predict failed: {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify propType is set correctly
        assert data.get("propType") == "saves", f"propType should be 'saves', got {data.get('propType')}"
        print("✓ propType correctly set to 'saves'")
        
        # Verify recentSamples contains reasonable save values
        recent_samples = data.get("recentSamples", [])
        assert len(recent_samples) > 0, "No recentSamples in response"
        print(f"✓ Got {len(recent_samples)} recent samples")
        
        # Extract values from recentSamples
        values = [s.get("value") for s in recent_samples if s.get("value") is not None]
        assert len(values) > 0, "No values in recentSamples"
        
        avg_value = sum(values) / len(values)
        max_value = max(values)
        
        print(f"✓ Sample values: {values[:5]}...")
        print(f"✓ Average value: {avg_value:.1f}, Max value: {max_value}")
        
        # KEY ASSERTION: Saves should typically be 0-10, not 20-40 like pass_attempts
        assert max_value <= 15, f"Max save value ({max_value}) is too high - might still be using pass_attempts"
        assert avg_value <= 10, f"Average save value ({avg_value:.1f}) is too high - might still be using pass_attempts"
        
        print("✓ Save values are in reasonable range (not pass_attempts)")
        
        # Verify projectedValue is reasonable for saves
        projected = data.get("projectedValue")
        if projected:
            assert projected <= 15, f"Projected saves ({projected}) seems too high"
            print(f"✓ Projected saves: {projected}")
        
        print("✓ Saves prop mapping test PASSED")
    
    def test_predict_pass_attempts_regression(self):
        """Regression test: pass_attempts prop should still work correctly"""
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        predict_payload = {
            "playerId": 306,  # Salah
            "playerName": "Mohamed Salah",
            "teamId": 40,
            "opponentId": 50,
            "opponentName": "Manchester City",
            "leagueId": 39,
            "propType": "pass_attempts",
            "line": 25.5,
            "venue": "home"
        }
        
        print("Testing predict endpoint with pass_attempts prop for Salah...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=predict_payload,
            headers=headers,
            timeout=120
        )
        
        assert response.status_code == 200, f"Predict failed: {response.text}"
        data = response.json()
        
        assert data.get("propType") == "pass_attempts"
        print("✓ propType correctly set to 'pass_attempts'")
        
        # Verify recentSamples has reasonable pass_attempts values (typically 20-50)
        recent_samples = data.get("recentSamples", [])
        if recent_samples:
            values = [s.get("value") for s in recent_samples if s.get("value") is not None]
            if values:
                avg_value = sum(values) / len(values)
                print(f"✓ Average pass_attempts: {avg_value:.1f} (sample: {values[:5]})")
                # Pass attempts should typically be > 15 for outfield players
                assert avg_value >= 10, f"Average pass_attempts ({avg_value:.1f}) seems too low"
        
        print("✓ pass_attempts regression test PASSED")
    
    def test_predict_shots_regression(self):
        """Regression test: shots prop should still work correctly"""
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        predict_payload = {
            "playerId": 306,
            "playerName": "Mohamed Salah",
            "teamId": 40,
            "opponentId": 50,
            "opponentName": "Manchester City",
            "leagueId": 39,
            "propType": "shots",
            "line": 2.5,
            "venue": "home"
        }
        
        print("Testing predict endpoint with shots prop...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=predict_payload,
            headers=headers,
            timeout=120
        )
        
        assert response.status_code == 200, f"Predict failed: {response.text}"
        data = response.json()
        
        assert data.get("propType") == "shots"
        print("✓ propType correctly set to 'shots'")
        
        recent_samples = data.get("recentSamples", [])
        if recent_samples:
            values = [s.get("value") for s in recent_samples if s.get("value") is not None]
            if values:
                avg_value = sum(values) / len(values)
                print(f"✓ Average shots: {avg_value:.1f} (sample: {values[:5]})")
        
        print("✓ shots regression test PASSED")
    
    def test_predict_tackles_regression(self):
        """Regression test: tackles prop should still work correctly"""
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        predict_payload = {
            "playerId": 306,
            "playerName": "Mohamed Salah",
            "teamId": 40,
            "opponentId": 50,
            "opponentName": "Manchester City",
            "leagueId": 39,
            "propType": "tackles",
            "line": 1.5,
            "venue": "home"
        }
        
        print("Testing predict endpoint with tackles prop...")
        response = requests.post(
            f"{BASE_URL}/api/predict",
            json=predict_payload,
            headers=headers,
            timeout=120
        )
        
        assert response.status_code == 200, f"Predict failed: {response.text}"
        data = response.json()
        
        assert data.get("propType") == "tackles"
        print("✓ propType correctly set to 'tackles'")
        
        recent_samples = data.get("recentSamples", [])
        if recent_samples:
            values = [s.get("value") for s in recent_samples if s.get("value") is not None]
            if values:
                avg_value = sum(values) / len(values)
                print(f"✓ Average tackles: {avg_value:.1f} (sample: {values[:5]})")
        
        print("✓ tackles regression test PASSED")


class TestStatFieldMapVerification:
    """Verify the stat_field_map dictionaries have correct mappings in code"""
    
    def test_verify_stat_field_map_in_code(self):
        """
        Code review test: Verify the stat_field_map has 'saves' -> 'goals_saves' mapping.
        """
        import re
        
        server_path = "/app/backend/server.py"
        with open(server_path, 'r') as f:
            content = f.read()
        
        # Check for goals_saves extraction in game_log dictionary
        assert 'goals_saves' in content, "goals_saves field not found in server.py"
        print("✓ goals_saves field extraction found in code")
        
        # Check for saves -> goals_saves mapping
        saves_mapping_pattern = r'"saves"\s*:\s*"goals_saves"'
        matches = re.findall(saves_mapping_pattern, content)
        
        # Should find 3 occurrences
        assert len(matches) >= 3, f"Expected 3 'saves' -> 'goals_saves' mappings, found {len(matches)}"
        print(f"✓ Found {len(matches)} 'saves' -> 'goals_saves' mappings in code")
        
        # Verify goals_saves extraction pattern
        assert '"goals_saves": stats.get("goals", {}).get("saves")' in content, \
               "goals_saves extraction pattern not found"
        print("✓ goals_saves extraction pattern found")
        
        print("✓ Code verification test PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
