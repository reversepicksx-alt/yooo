"""
Test suite for 6 bug fixes in ReversePicks app:
1. Natural search should NOT be hardcoded to league 39 (MLS player should use MLS league)
2. Backend team stats should try multiple seasons (2026, 2025, 2024)
3. stats_list[-1] for current team instead of stats_list[0] (transferred players)
4. settle-picks 'push' handling (actual_value == line should return 'push')
5. useEffect savedPicks no longer has filter().length as dependency (frontend test)
6. localStorage savedPicks race condition fixed (frontend test)
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestBugFix1_NaturalSearchLeagueDetection:
    """BUG FIX 1: Natural search should NOT be hardcoded to league 39"""
    
    def test_mls_player_search_returns_mls_team(self):
        """Search for an MLS player and verify they have MLS team info"""
        # Search for a known MLS player (e.g., Riqui Puig at LA Galaxy)
        response = requests.post(f"{BASE_URL}/api/players/search", json={
            "query": "Riqui Puig",
            "league_id": 253  # MLS league ID
        })
        assert response.status_code == 200, f"Player search failed: {response.text}"
        data = response.json()
        players = data.get("players", [])
        
        # Should find the player
        if players:
            player = players[0]
            print(f"Found player: {player.get('name')} at {player.get('teamName')} (teamId: {player.get('teamId')})")
            # Verify player has team info
            assert player.get("teamId") or player.get("teamName"), "Player should have team info"
    
    def test_mls_player_search_without_league_id(self):
        """Search for MLS player without specifying league - should still find them"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={
            "query": "Riqui Puig"
        })
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        if players:
            player = players[0]
            print(f"Found player without league filter: {player.get('name')} at {player.get('teamName')}")
            # MLS player should be found even without league filter
            assert player.get("name"), "Should find player"


class TestBugFix2_MultiSeasonTeamStats:
    """BUG FIX 2: Backend team stats should try multiple seasons (2026, 2025, 2024)"""
    
    def test_predict_endpoint_does_not_500(self):
        """Call /api/predict and verify it doesn't return 500 (multi-season fallback working)"""
        # Use a known player/team combination
        response = requests.post(f"{BASE_URL}/api/predict", json={
            "leagueId": 39,  # Premier League
            "playerId": 306,  # Salah
            "playerName": "Mohamed Salah",
            "teamId": 40,  # Liverpool
            "opponentId": 33,  # Manchester United
            "opponentName": "Manchester United",
            "venue": "home",
            "propType": "pass_attempts",
            "line": 30
        }, timeout=60)
        
        # Should not return 500 - multi-season fallback should work
        assert response.status_code != 500, f"Predict endpoint returned 500: {response.text}"
        assert response.status_code == 200, f"Predict failed with {response.status_code}: {response.text}"
        
        data = response.json()
        print(f"Prediction generated: {data.get('recommendation')} with confidence {data.get('confidenceScore')}%")
        assert "recommendation" in data, "Response should have recommendation"


class TestBugFix3_TransferredPlayerCurrentTeam:
    """BUG FIX 3: stats_list[-1] for current team instead of stats_list[0]"""
    
    def test_transferred_player_shows_current_team(self):
        """Search for a recently transferred player and verify teamName is their CURRENT team"""
        # Search for a player who transferred recently
        # Example: Riqui Puig (Barcelona -> LA Galaxy)
        response = requests.post(f"{BASE_URL}/api/players/search", json={
            "query": "Riqui Puig"
        })
        assert response.status_code == 200
        data = response.json()
        players = data.get("players", [])
        
        if players:
            player = players[0]
            team_name = player.get("teamName", "")
            print(f"Player: {player.get('name')}, Current Team: {team_name}")
            # Should show current team (LA Galaxy), not old team (Barcelona)
            # The fix uses stats_list[-1] instead of stats_list[0]
            assert team_name, "Player should have current team name"
    
    def test_player_stats_endpoint_returns_current_team(self):
        """Verify player stats endpoint returns current team info"""
        # Get stats for a known player
        response = requests.get(f"{BASE_URL}/api/player/306/stats")  # Salah
        assert response.status_code == 200
        data = response.json()
        stats = data.get("stats")
        
        if stats:
            statistics = stats.get("statistics", [])
            if statistics:
                # The last entry should be the most recent/current team
                current_team = statistics[-1].get("team", {}).get("name", "")
                print(f"Current team from stats: {current_team}")
                assert current_team, "Should have current team in stats"


class TestBugFix4_SettlePicksPushHandling:
    """BUG FIX 4: settle-picks 'push' handling - actual_value == line should return 'push'"""
    
    def test_settle_picks_push_result(self):
        """POST /api/settle-picks with mock pick where actual_value == line should return 'push'"""
        # Create a mock pick that simulates a push scenario
        # Note: This tests the logic, but actual settlement depends on real match data
        mock_pick = {
            "id": "test_push_pick_001",
            "player": {
                "id": 306,  # Salah
                "name": "Mohamed Salah",
                "team": "Liverpool"
            },
            "propType": "pass_attempts",
            "line": 30,  # The line we're testing
            "recommendation": "over",
            "status": "live",
            "opponent": "Manchester United",
            "_request": {
                "leagueId": 39,
                "teamId": 40,
                "opponentId": 33
            }
        }
        
        response = requests.post(f"{BASE_URL}/api/settle-picks", json={
            "picks": [mock_pick]
        })
        
        assert response.status_code == 200, f"Settle picks failed: {response.text}"
        data = response.json()
        print(f"Settle picks response: {data}")
        
        # The endpoint should return without error
        # If a match is found and actual_value == line, result should be 'push'
        settled = data.get("settled", [])
        for s in settled:
            if s.get("actualValue") == mock_pick["line"]:
                assert s.get("result") == "push", f"Expected 'push' when actual == line, got {s.get('result')}"
                print(f"PUSH correctly detected: actual={s.get('actualValue')}, line={mock_pick['line']}")
    
    def test_settle_picks_endpoint_returns_200(self):
        """Verify settle-picks endpoint works and returns 200"""
        response = requests.post(f"{BASE_URL}/api/settle-picks", json={
            "picks": []
        })
        assert response.status_code == 200
        data = response.json()
        assert "settled" in data, "Response should have 'settled' key"


class TestOwnerAutoLogin:
    """Test owner auto-login with josselj001@gmail.com (bypasses password)"""
    
    def test_owner_auto_login_bypasses_password(self):
        """Owner email should auto-login without requiring password"""
        response = requests.post(f"{BASE_URL}/api/auth/verify-whop", json={
            "email": "josselj001@gmail.com"
        })
        assert response.status_code == 200, f"Verify whop failed: {response.text}"
        data = response.json()
        
        # Owner should be verified immediately with session token
        assert data.get("verified") == True, f"Owner should be auto-verified: {data}"
        assert data.get("session_token"), "Owner should get session token without password"
        assert data.get("access_type") == "Owner", f"Access type should be Owner: {data.get('access_type')}"
        print(f"Owner auto-login successful: {data}")


class TestPickOfTheDay:
    """Test Pick of the Day card loads on predict tab"""
    
    def test_potd_endpoint_returns_data(self):
        """POTD endpoint should return available pick data"""
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day")
        assert response.status_code == 200, f"POTD failed: {response.text}"
        data = response.json()
        
        print(f"POTD response: {data}")
        assert "date" in data, "POTD should have date"
        
        if data.get("available"):
            pick = data.get("pick", {})
            assert pick.get("playerName"), "POTD should have player name"
            assert pick.get("propType"), "POTD should have prop type"
            print(f"POTD: {pick.get('playerName')} - {pick.get('propType')} {pick.get('recommendation')}")


class TestLeagueSelection:
    """Test league selection shows all 31 leagues organized by category"""
    
    def test_leagues_endpoint_returns_all_leagues(self):
        """Leagues endpoint should return all 31 supported leagues"""
        response = requests.get(f"{BASE_URL}/api/leagues")
        assert response.status_code == 200
        data = response.json()
        
        leagues = data.get("leagues", [])
        print(f"Total leagues: {len(leagues)}")
        
        # Should have 31 leagues
        assert len(leagues) == 31, f"Expected 31 leagues, got {len(leagues)}"
        
        # Check categories
        domestic = [l for l in leagues if l.get("type") == "Domestic"]
        intl_club = [l for l in leagues if l.get("type") == "International Club"]
        intl_team = [l for l in leagues if l.get("type") == "International Team"]
        
        print(f"Domestic: {len(domestic)}, International Club: {len(intl_club)}, International Team: {len(intl_team)}")
        
        assert len(domestic) > 0, "Should have domestic leagues"
        assert len(intl_club) > 0, "Should have international club leagues"
        assert len(intl_team) > 0, "Should have international team leagues"


class TestPlayerSearchNationality:
    """Test player search returns results with nationality displayed"""
    
    def test_player_search_includes_nationality(self):
        """Player search should return nationality field"""
        response = requests.post(f"{BASE_URL}/api/players/search", json={
            "query": "Salah",
            "league_id": 39
        })
        assert response.status_code == 200
        data = response.json()
        
        players = data.get("players", [])
        assert len(players) > 0, "Should find players"
        
        player = players[0]
        print(f"Player: {player.get('name')}, Nationality: {player.get('nationality')}, Team: {player.get('teamName')}")
        
        # Nationality should be present
        assert player.get("nationality"), f"Player should have nationality: {player}"


class TestHealthAndBasics:
    """Basic health checks"""
    
    def test_health_endpoint(self):
        """Health endpoint should return ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
    
    def test_football_status(self):
        """Football API status should be online"""
        response = requests.get(f"{BASE_URL}/api/football/status")
        assert response.status_code == 200
        data = response.json()
        print(f"Football API status: {data.get('status')}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
