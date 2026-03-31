"""
Iteration 34: Live Tracking Logic Tests
Tests the fixes for soccer live tracking:
1. Soccer fixture fetching: 3 parallel queries (date=today, date=yesterday, last=3) with deduplication
2. Soccer fixture matching: ANY live game matches immediately (no opponent check)
3. Soccer fixture matching: finished games require opponent name + time proximity
4. Basketball fixture matching: ANY live game matches immediately (no opponent check)
5. Basketball fixture matching: finished games require opponent name + time proximity
"""
import pytest
import requests
import os
import sys
from datetime import datetime, timezone, timedelta

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestHealthEndpoint:
    """Basic health check"""
    
    def test_health_returns_200(self):
        """GET /api/health returns 200"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print("PASS: /api/health returns 200 with status=ok")


class TestSoccerFixtureMatchingLogic:
    """Test _match_soccer_fixture logic directly"""
    
    def test_live_game_matches_without_opponent_check(self):
        """A live soccer game should match immediately regardless of opponent name"""
        from routes.picks import _match_soccer_fixture
        
        # Simulate fixtures with a LIVE game
        fixtures = [
            {
                "fixture": {"id": 1001, "status": {"short": "1H"}, "date": "2026-03-31T18:00:00+00:00"},
                "teams": {"home": {"name": "Manchester United"}, "away": {"name": "Liverpool"}}
            },
            {
                "fixture": {"id": 1002, "status": {"short": "FT"}, "date": "2026-03-30T15:00:00+00:00"},
                "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}}
            }
        ]
        
        # Test with opponent_name='Unknown' - should STILL match the live game
        result = _match_soccer_fixture(fixtures, "Unknown", "2026-03-31T17:00:00+00:00")
        assert result is not None, "Live game should match even with opponent_name='Unknown'"
        assert result["fixture"]["id"] == 1001, "Should match the live game (1H status)"
        assert result["fixture"]["status"]["short"] == "1H"
        print("PASS: Live soccer game matches with opponent_name='Unknown'")
    
    def test_live_game_matches_with_empty_opponent(self):
        """A live soccer game should match even with empty opponent name"""
        from routes.picks import _match_soccer_fixture
        
        fixtures = [
            {
                "fixture": {"id": 2001, "status": {"short": "2H"}, "date": "2026-03-31T18:00:00+00:00"},
                "teams": {"home": {"name": "Real Madrid"}, "away": {"name": "Barcelona"}}
            }
        ]
        
        # Empty opponent name should still match live game
        result = _match_soccer_fixture(fixtures, "", "2026-03-31T17:00:00+00:00")
        assert result is not None, "Live game should match with empty opponent name"
        assert result["fixture"]["id"] == 2001
        print("PASS: Live soccer game matches with empty opponent name")
    
    def test_live_game_matches_with_wrong_opponent(self):
        """A live soccer game should match even if opponent name doesn't match teams"""
        from routes.picks import _match_soccer_fixture
        
        fixtures = [
            {
                "fixture": {"id": 3001, "status": {"short": "HT"}, "date": "2026-03-31T18:00:00+00:00"},
                "teams": {"home": {"name": "Bayern Munich"}, "away": {"name": "Dortmund"}}
            }
        ]
        
        # Wrong opponent name should still match live game (team can only play one game at a time)
        result = _match_soccer_fixture(fixtures, "Juventus", "2026-03-31T17:00:00+00:00")
        assert result is not None, "Live game should match even with wrong opponent name"
        assert result["fixture"]["id"] == 3001
        print("PASS: Live soccer game matches even with wrong opponent name")
    
    def test_all_live_statuses_match_immediately(self):
        """All live status codes should trigger immediate match"""
        from routes.picks import _match_soccer_fixture
        
        live_statuses = ["1H", "2H", "ET", "BT", "P", "LIVE", "HT"]
        
        for status in live_statuses:
            fixtures = [
                {
                    "fixture": {"id": 4000 + live_statuses.index(status), "status": {"short": status}, "date": "2026-03-31T18:00:00+00:00"},
                    "teams": {"home": {"name": "Team A"}, "away": {"name": "Team B"}}
                }
            ]
            result = _match_soccer_fixture(fixtures, "Unknown", "2026-03-31T17:00:00+00:00")
            assert result is not None, f"Status {status} should match immediately"
            assert result["fixture"]["status"]["short"] == status
            print(f"PASS: Soccer status '{status}' matches immediately without opponent check")
    
    def test_finished_game_requires_opponent_match(self):
        """Finished games should require opponent name matching"""
        from routes.picks import _match_soccer_fixture
        
        fixtures = [
            {
                "fixture": {"id": 5001, "status": {"short": "FT"}, "date": "2026-03-31T15:00:00+00:00"},
                "teams": {"home": {"name": "Manchester City"}, "away": {"name": "Tottenham"}}
            }
        ]
        
        # With correct opponent - should match
        result = _match_soccer_fixture(fixtures, "Tottenham", "2026-03-31T14:00:00+00:00")
        assert result is not None, "Finished game should match with correct opponent"
        assert result["fixture"]["id"] == 5001
        print("PASS: Finished soccer game matches with correct opponent name")
        
        # With wrong opponent - should NOT match
        result_wrong = _match_soccer_fixture(fixtures, "Liverpool", "2026-03-31T14:00:00+00:00")
        assert result_wrong is None, "Finished game should NOT match with wrong opponent"
        print("PASS: Finished soccer game does NOT match with wrong opponent name")
    
    def test_finished_game_with_unknown_opponent_still_matches(self):
        """Finished games with 'Unknown' or empty opponent should still match (fallback)"""
        from routes.picks import _match_soccer_fixture
        
        fixtures = [
            {
                "fixture": {"id": 6001, "status": {"short": "FT"}, "date": "2026-03-31T15:00:00+00:00"},
                "teams": {"home": {"name": "PSG"}, "away": {"name": "Lyon"}}
            }
        ]
        
        # With 'Unknown' opponent - should match (opponent check is skipped for unknown/empty)
        result = _match_soccer_fixture(fixtures, "Unknown", "2026-03-31T14:00:00+00:00")
        assert result is not None, "Finished game should match when opponent is 'Unknown'"
        print("PASS: Finished soccer game matches when opponent is 'Unknown'")
        
        # With empty opponent - should match
        result_empty = _match_soccer_fixture(fixtures, "", "2026-03-31T14:00:00+00:00")
        assert result_empty is not None, "Finished game should match when opponent is empty"
        print("PASS: Finished soccer game matches when opponent is empty")
    
    def test_finished_game_time_proximity_check(self):
        """Finished games should check time proximity (within 48 hours)"""
        from routes.picks import _match_soccer_fixture
        
        fixtures = [
            {
                "fixture": {"id": 7001, "status": {"short": "FT"}, "date": "2026-03-31T15:00:00+00:00"},
                "teams": {"home": {"name": "Inter Milan"}, "away": {"name": "AC Milan"}}
            }
        ]
        
        # Pick timestamp within 48 hours - should match
        result_close = _match_soccer_fixture(fixtures, "AC Milan", "2026-03-30T15:00:00+00:00")
        assert result_close is not None, "Finished game should match within 48 hours"
        print("PASS: Finished soccer game matches within 48 hour window")
        
        # Pick timestamp > 48 hours away - should NOT match
        result_far = _match_soccer_fixture(fixtures, "AC Milan", "2026-03-28T10:00:00+00:00")
        assert result_far is None, "Finished game should NOT match if > 48 hours away"
        print("PASS: Finished soccer game does NOT match if > 48 hours away")


class TestBasketballFixtureMatchingLogic:
    """Test _match_basketball_game logic directly"""
    
    def test_live_game_matches_without_opponent_check(self):
        """A live basketball game should match immediately regardless of opponent name"""
        from routes.picks import _match_basketball_game
        
        games = [
            {
                "id": 8001,
                "status": {"short": "Q2"},
                "date": "2026-03-31T19:00:00+00:00",
                "teams": {"home": {"id": 1, "name": "Lakers"}, "away": {"id": 2, "name": "Celtics"}}
            },
            {
                "id": 8002,
                "status": {"short": "FT"},
                "date": "2026-03-30T19:00:00+00:00",
                "teams": {"home": {"id": 1, "name": "Lakers"}, "away": {"id": 3, "name": "Warriors"}}
            }
        ]
        
        # Test with opponent_name='Unknown' - should STILL match the live game
        result = _match_basketball_game(games, "Unknown", 1, "2026-03-31T18:00:00+00:00")
        assert result is not None, "Live basketball game should match with opponent_name='Unknown'"
        assert result["id"] == 8001, "Should match the live game (Q2 status)"
        print("PASS: Live basketball game matches with opponent_name='Unknown'")
    
    def test_live_game_matches_with_empty_opponent(self):
        """A live basketball game should match even with empty opponent name"""
        from routes.picks import _match_basketball_game
        
        games = [
            {
                "id": 9001,
                "status": {"short": "Q3"},
                "date": "2026-03-31T19:00:00+00:00",
                "teams": {"home": {"id": 10, "name": "Heat"}, "away": {"id": 11, "name": "Knicks"}}
            }
        ]
        
        result = _match_basketball_game(games, "", 10, "2026-03-31T18:00:00+00:00")
        assert result is not None, "Live basketball game should match with empty opponent"
        assert result["id"] == 9001
        print("PASS: Live basketball game matches with empty opponent name")
    
    def test_all_basketball_live_statuses_match_immediately(self):
        """All basketball live status codes should trigger immediate match"""
        from routes.picks import _match_basketball_game
        
        live_statuses = ["Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"]
        
        for status in live_statuses:
            games = [
                {
                    "id": 10000 + live_statuses.index(status),
                    "status": {"short": status},
                    "date": "2026-03-31T19:00:00+00:00",
                    "teams": {"home": {"id": 20, "name": "Team X"}, "away": {"id": 21, "name": "Team Y"}}
                }
            ]
            result = _match_basketball_game(games, "Unknown", 20, "2026-03-31T18:00:00+00:00")
            assert result is not None, f"Basketball status {status} should match immediately"
            assert result["status"]["short"] == status
            print(f"PASS: Basketball status '{status}' matches immediately without opponent check")
    
    def test_finished_game_requires_opponent_match(self):
        """Finished basketball games should require opponent name matching"""
        from routes.picks import _match_basketball_game
        
        games = [
            {
                "id": 11001,
                "status": {"short": "FT"},
                "date": "2026-03-31T15:00:00+00:00",
                "teams": {"home": {"id": 30, "name": "Bulls"}, "away": {"id": 31, "name": "Pistons"}}
            }
        ]
        
        # With correct opponent - should match
        result = _match_basketball_game(games, "Pistons", 30, "2026-03-31T14:00:00+00:00")
        assert result is not None, "Finished basketball game should match with correct opponent"
        assert result["id"] == 11001
        print("PASS: Finished basketball game matches with correct opponent name")
        
        # With wrong opponent - should NOT match
        result_wrong = _match_basketball_game(games, "Rockets", 30, "2026-03-31T14:00:00+00:00")
        assert result_wrong is None, "Finished basketball game should NOT match with wrong opponent"
        print("PASS: Finished basketball game does NOT match with wrong opponent name")
    
    def test_finished_game_with_unknown_opponent_still_matches(self):
        """Finished basketball games with 'Unknown' or empty opponent should still match"""
        from routes.picks import _match_basketball_game
        
        games = [
            {
                "id": 12001,
                "status": {"short": "FT"},
                "date": "2026-03-31T15:00:00+00:00",
                "teams": {"home": {"id": 40, "name": "Nets"}, "away": {"id": 41, "name": "76ers"}}
            }
        ]
        
        # With 'Unknown' opponent - should match
        result = _match_basketball_game(games, "Unknown", 40, "2026-03-31T14:00:00+00:00")
        assert result is not None, "Finished basketball game should match when opponent is 'Unknown'"
        print("PASS: Finished basketball game matches when opponent is 'Unknown'")
        
        # With empty opponent - should match
        result_empty = _match_basketball_game(games, "", 40, "2026-03-31T14:00:00+00:00")
        assert result_empty is not None, "Finished basketball game should match when opponent is empty"
        print("PASS: Finished basketball game matches when opponent is empty")


class TestSoccerFixtureFetchingLogic:
    """Test that _process_soccer_live uses 3 parallel queries"""
    
    def test_process_soccer_live_code_structure(self):
        """Verify _process_soccer_live has the 3 parallel API calls"""
        import inspect
        from routes.picks import _process_soccer_live
        
        source = inspect.getsource(_process_soccer_live)
        
        # Check for date=today query
        assert 'date": today' in source or '"date": today' in source or "'date': today" in source, \
            "_process_soccer_live should query fixtures by date=today"
        print("PASS: _process_soccer_live queries fixtures by date=today")
        
        # Check for date=yesterday query
        assert 'yesterday' in source, \
            "_process_soccer_live should query fixtures by date=yesterday"
        print("PASS: _process_soccer_live queries fixtures by date=yesterday")
        
        # Check for last=3 query
        assert '"last": 3' in source or "'last': 3" in source or 'last": 3' in source, \
            "_process_soccer_live should query fixtures with last=3"
        print("PASS: _process_soccer_live queries fixtures with last=3")
        
        # Check for asyncio.gather (parallel execution)
        assert 'gather' in source, \
            "_process_soccer_live should use asyncio.gather for parallel queries"
        print("PASS: _process_soccer_live uses asyncio.gather for parallel queries")
        
        # Check for deduplication by fixture ID
        assert 'seen_ids' in source or 'fid' in source, \
            "_process_soccer_live should deduplicate fixtures by ID"
        print("PASS: _process_soccer_live deduplicates fixtures by ID")


class TestPredictionForceSetLogic:
    """Test that prediction routes force-set player and opponent from request"""
    
    def test_soccer_prediction_force_sets_fields(self):
        """Verify soccer prediction force-sets player and opponent"""
        import inspect
        
        # Read predict.py source
        with open('/app/backend/routes/predict.py', 'r') as f:
            source = f.read()
        
        # Check for force-set of opponent (not setdefault)
        assert "prediction['opponent'] = " in source or 'prediction["opponent"] = ' in source, \
            "Soccer prediction should force-set opponent (not setdefault)"
        print("PASS: Soccer prediction force-sets opponent from request")
        
        # Check for force-set of player
        assert "prediction['player'] = " in source or 'prediction["player"] = ' in source, \
            "Soccer prediction should force-set player (not setdefault)"
        print("PASS: Soccer prediction force-sets player from request")
    
    def test_basketball_prediction_force_sets_fields(self):
        """Verify basketball prediction force-sets player and opponent"""
        # Read basketball_predict.py source
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            source = f.read()
        
        # Check for force-set of opponent (not setdefault)
        assert "prediction['opponent'] = " in source or 'prediction["opponent"] = ' in source, \
            "Basketball prediction should force-set opponent (not setdefault)"
        print("PASS: Basketball prediction force-sets opponent from request")
        
        # Check for force-set of player
        assert "prediction['player'] = " in source or 'prediction["player"] = ' in source, \
            "Basketball prediction should force-set player (not setdefault)"
        print("PASS: Basketball prediction force-sets player from request")


class TestLiveStatusDefinitions:
    """Verify live and finished status definitions are correct"""
    
    def test_soccer_live_statuses_defined(self):
        """Verify soccer live statuses include all expected values"""
        from routes.picks import _match_soccer_fixture
        import inspect
        
        source = inspect.getsource(_match_soccer_fixture)
        
        expected_live = ["1H", "2H", "ET", "BT", "P", "LIVE", "HT"]
        for status in expected_live:
            assert f'"{status}"' in source or f"'{status}'" in source, \
                f"Soccer live_statuses should include {status}"
        print(f"PASS: Soccer live_statuses includes all expected: {expected_live}")
    
    def test_soccer_finished_statuses_defined(self):
        """Verify soccer finished statuses include all expected values"""
        from routes.picks import _match_soccer_fixture
        import inspect
        
        source = inspect.getsource(_match_soccer_fixture)
        
        expected_finished = ["FT", "AET", "PEN"]
        for status in expected_finished:
            assert f'"{status}"' in source or f"'{status}'" in source, \
                f"Soccer finished_statuses should include {status}"
        print(f"PASS: Soccer finished_statuses includes all expected: {expected_finished}")
    
    def test_basketball_live_statuses_defined(self):
        """Verify basketball live statuses include all expected values"""
        from routes.picks import _match_basketball_game
        import inspect
        
        source = inspect.getsource(_match_basketball_game)
        
        expected_live = ["Q1", "Q2", "Q3", "Q4", "OT", "BT", "HT"]
        for status in expected_live:
            assert f'"{status}"' in source or f"'{status}'" in source, \
                f"Basketball live_statuses should include {status}"
        print(f"PASS: Basketball live_statuses includes all expected: {expected_live}")
    
    def test_basketball_finished_statuses_defined(self):
        """Verify basketball finished statuses include all expected values"""
        from routes.picks import _match_basketball_game
        import inspect
        
        source = inspect.getsource(_match_basketball_game)
        
        expected_finished = ["FT", "AOT"]
        for status in expected_finished:
            assert f'"{status}"' in source or f"'{status}'" in source, \
                f"Basketball finished_statuses should include {status}"
        print(f"PASS: Basketball finished_statuses includes all expected: {expected_finished}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
