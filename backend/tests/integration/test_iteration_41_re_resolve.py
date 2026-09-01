"""
Iteration 41: Test POST /api/re-resolve endpoint
Tests the new feature that allows users to correct player/team/opponent info after AI scan
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestReResolveEndpoint:
    """Tests for POST /api/re-resolve endpoint - user correction of scan results"""
    
    def test_re_resolve_endpoint_exists(self):
        """Test that the /api/re-resolve endpoint exists and accepts POST"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={"playerName": "Test", "playerTeam": "Test Team"},
            headers={"Content-Type": "application/json"}
        )
        # Should not return 404 (endpoint exists)
        assert response.status_code != 404, f"Endpoint /api/re-resolve not found. Status: {response.status_code}"
        # Should not return 405 (method allowed)
        assert response.status_code != 405, f"POST method not allowed. Status: {response.status_code}"
    
    def test_re_resolve_known_soccer_player_messi(self):
        """Test re-resolve with known player: Messi, Inter Miami, vs LA Galaxy"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Messi",
                "playerTeam": "Inter Miami",
                "opponentName": "LA Galaxy",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify response structure
        assert "resolved" in data, "Response missing 'resolved' field"
        assert "resolvedOpponent" in data, "Response missing 'resolvedOpponent' field"
        assert "leagueId" in data, "Response missing 'leagueId' field"
        assert "leagueName" in data, "Response missing 'leagueName' field"
        
        # Verify player resolution (Messi should be found)
        if data["resolved"]:
            assert "playerId" in data["resolved"], "Resolved player missing playerId"
            assert "playerName" in data["resolved"], "Resolved player missing playerName"
            assert "teamId" in data["resolved"], "Resolved player missing teamId"
            assert "teamName" in data["resolved"], "Resolved player missing teamName"
            # Messi's name should contain "Messi" or "Lionel"
            player_name = data["resolved"]["playerName"].lower()
            assert "messi" in player_name or "lionel" in player_name, f"Expected Messi, got {data['resolved']['playerName']}"
        
        # Verify league is MLS (253)
        assert data["leagueId"] == 253, f"Expected MLS league (253), got {data['leagueId']}"
    
    def test_re_resolve_with_corrected_team_name(self):
        """Test re-resolve with corrected team name uses correct league resolution"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Salah",
                "playerTeam": "Liverpool",
                "opponentName": "Manchester City",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify league is Premier League (39)
        assert data["leagueId"] == 39, f"Expected Premier League (39), got {data['leagueId']}"
        
        # Verify opponent resolution
        if data["resolvedOpponent"]:
            assert "teamId" in data["resolvedOpponent"], "Resolved opponent missing teamId"
            assert "teamName" in data["resolvedOpponent"], "Resolved opponent missing teamName"
    
    def test_re_resolve_basketball_sport_parameter(self):
        """Test re-resolve with basketball sport parameter"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "LeBron James",
                "playerTeam": "Lakers",
                "opponentName": "Celtics",
                "sport": "basketball"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify response structure for basketball
        assert "resolved" in data, "Response missing 'resolved' field"
        assert "resolvedOpponent" in data, "Response missing 'resolvedOpponent' field"
        assert "leagueName" in data, "Response missing 'leagueName' field"
        
        # Basketball should return NBA as league
        assert data["leagueName"] == "NBA", f"Expected NBA, got {data['leagueName']}"
    
    def test_re_resolve_unknown_player_graceful_null(self):
        """Test re-resolve with unknown player returns resolved=null gracefully"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "XYZ Unknown Player 12345",
                "playerTeam": "Unknown Team ABC",
                "opponentName": "Another Unknown Team",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Should return null for resolved player (not found)
        assert data["resolved"] is None, f"Expected resolved=null for unknown player, got {data['resolved']}"
        
        # Response should still have proper structure
        assert "resolvedOpponent" in data, "Response missing 'resolvedOpponent' field"
        assert "leagueId" in data, "Response missing 'leagueId' field"
    
    def test_re_resolve_missing_player_name_returns_400(self):
        """Test re-resolve with missing playerName returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerTeam": "Inter Miami",
                "opponentName": "LA Galaxy",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        # Should return 422 (validation error) or 400 (bad request)
        assert response.status_code in [400, 422], f"Expected 400/422 for missing playerName, got {response.status_code}"
    
    def test_re_resolve_missing_player_team_returns_400(self):
        """Test re-resolve with missing playerTeam returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Messi",
                "opponentName": "LA Galaxy",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        # Should return 422 (validation error) or 400 (bad request)
        assert response.status_code in [400, 422], f"Expected 400/422 for missing playerTeam, got {response.status_code}"
    
    def test_re_resolve_empty_player_name_returns_400(self):
        """Test re-resolve with empty playerName returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "",
                "playerTeam": "Inter Miami",
                "opponentName": "LA Galaxy",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400, f"Expected 400 for empty playerName, got {response.status_code}"
    
    def test_re_resolve_empty_player_team_returns_400(self):
        """Test re-resolve with empty playerTeam returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Messi",
                "playerTeam": "",
                "opponentName": "LA Galaxy",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400, f"Expected 400 for empty playerTeam, got {response.status_code}"
    
    def test_re_resolve_optional_opponent_name(self):
        """Test re-resolve works without opponentName (optional field)"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Messi",
                "playerTeam": "Inter Miami",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Should still resolve player
        assert "resolved" in data, "Response missing 'resolved' field"
        # Opponent should be null since not provided
        assert data["resolvedOpponent"] is None, f"Expected null opponent when not provided, got {data['resolvedOpponent']}"
    
    def test_re_resolve_default_sport_is_soccer(self):
        """Test re-resolve defaults to soccer when sport not specified"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Salah",
                "playerTeam": "Liverpool",
                "opponentName": "Chelsea"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Should return soccer league (not NBA)
        assert data["leagueName"] != "NBA", "Should default to soccer, not basketball"
        # Should return Premier League for Liverpool
        assert data["leagueId"] == 39, f"Expected Premier League (39), got {data['leagueId']}"
    
    def test_re_resolve_la_liga_player(self):
        """Test re-resolve with La Liga player"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Vinicius",
                "playerTeam": "Real Madrid",
                "opponentName": "Barcelona",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify league is La Liga (140)
        assert data["leagueId"] == 140, f"Expected La Liga (140), got {data['leagueId']}"
    
    def test_re_resolve_bundesliga_player(self):
        """Test re-resolve with Bundesliga player"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Musiala",
                "playerTeam": "Bayern Munich",
                "opponentName": "Dortmund",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify league is Bundesliga (78)
        assert data["leagueId"] == 78, f"Expected Bundesliga (78), got {data['leagueId']}"
    
    def test_re_resolve_position_info_returned(self):
        """Test re-resolve returns position info when available"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Salah",
                "playerTeam": "Liverpool",
                "opponentName": "Chelsea",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Position field should exist in response (may be empty dict if not cached)
        assert "position" in data, "Response missing 'position' field"


class TestReResolveIntegration:
    """Integration tests for re-resolve with scan workflow"""
    
    def test_re_resolve_response_can_update_scan_result(self):
        """Test that re-resolve response has all fields needed to update scan result"""
        response = requests.post(
            f"{BASE_URL}/api/re-resolve",
            json={
                "playerName": "Haaland",
                "playerTeam": "Manchester City",
                "opponentName": "Arsenal",
                "sport": "soccer"
            },
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify all fields needed for scan result update are present
        required_fields = ["resolved", "resolvedOpponent", "leagueId", "leagueName", "position"]
        for field in required_fields:
            assert field in data, f"Response missing required field: {field}"
        
        # If player resolved, verify it has all needed sub-fields
        if data["resolved"]:
            player_fields = ["playerId", "playerName", "teamId", "teamName"]
            for field in player_fields:
                assert field in data["resolved"], f"Resolved player missing field: {field}"
        
        # If opponent resolved, verify it has all needed sub-fields
        if data["resolvedOpponent"]:
            opponent_fields = ["teamId", "teamName"]
            for field in opponent_fields:
                assert field in data["resolvedOpponent"], f"Resolved opponent missing field: {field}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
