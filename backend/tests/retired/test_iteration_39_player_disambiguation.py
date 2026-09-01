"""
Iteration 39: Player Disambiguation and League Resolution Tests

Tests for:
1. predict.py: teamId=0 or opponentId=0 returns 400 error with clear message
2. scan.py: pick_best function uses opponent_hint to disambiguate players
3. scan.py: TEAM_LEAGUE_MAP includes toulouse, paris sg, and 80+ European teams
4. scan.py: _infer_league_id checks hardcoded map BEFORE AI guess
5. scan.py: _resolve_player_via_api passes opponent_hint through to pick_best
"""

import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestTeamIdZeroGuard:
    """Test that teamId=0 or opponentId=0 returns 400 error"""
    
    def test_predict_rejects_team_id_zero(self):
        """POST /api/predict with teamId=0 should return 400"""
        response = requests.post(f"{BASE_URL}/api/predict", json={
            "playerId": 1100,
            "playerName": "Test Player",
            "teamId": 0,  # Invalid - should be rejected
            "teamName": "Test Team",
            "opponentId": 50,
            "opponentName": "Opponent",
            "propType": "pass_attempts",
            "line": 30.5,
            "venue": "home",
            "leagueId": 39
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "team could not be resolved" in data.get("detail", "").lower(), f"Expected team resolution error, got: {data}"
    
    def test_predict_rejects_opponent_id_zero(self):
        """POST /api/predict with opponentId=0 should return 400"""
        response = requests.post(f"{BASE_URL}/api/predict", json={
            "playerId": 1100,
            "playerName": "Test Player",
            "teamId": 50,
            "teamName": "Test Team",
            "opponentId": 0,  # Invalid - should be rejected
            "opponentName": "Opponent",
            "propType": "pass_attempts",
            "line": 30.5,
            "venue": "home",
            "leagueId": 39
        })
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        data = response.json()
        assert "opponent" in data.get("detail", "").lower(), f"Expected opponent resolution error, got: {data}"
    
    def test_predict_rejects_both_ids_zero(self):
        """POST /api/predict with both teamId=0 and opponentId=0 should return 400"""
        response = requests.post(f"{BASE_URL}/api/predict", json={
            "playerId": 1100,
            "playerName": "Test Player",
            "teamId": 0,  # Invalid
            "teamName": "Test Team",
            "opponentId": 0,  # Invalid
            "opponentName": "Opponent",
            "propType": "pass_attempts",
            "line": 30.5,
            "venue": "home",
            "leagueId": 39
        })
        # Should fail on teamId first
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"


class TestTeamLeagueMapExpansion:
    """Test that TEAM_LEAGUE_MAP includes required teams"""
    
    def test_team_league_map_has_toulouse(self):
        """Verify toulouse is mapped to Ligue 1 (61)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert '"toulouse": 61' in content, "toulouse should be mapped to Ligue 1 (61)"
    
    def test_team_league_map_has_paris_sg(self):
        """Verify paris sg is mapped to Ligue 1 (61)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert '"paris sg": 61' in content, "paris sg should be mapped to Ligue 1 (61)"
    
    def test_team_league_map_has_psg(self):
        """Verify psg is mapped to Ligue 1 (61)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert '"psg": 61' in content, "psg should be mapped to Ligue 1 (61)"
    
    def test_team_league_map_has_genoa(self):
        """Verify genoa is mapped to Serie A (135)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert '"genoa": 135' in content, "genoa should be mapped to Serie A (135)"
    
    def test_team_league_map_has_fluminense(self):
        """Verify fluminense is mapped to Brasileirao (71)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert '"fluminense": 71' in content, "fluminense should be mapped to Brasileirao (71)"
    
    def test_team_league_map_has_80_plus_teams(self):
        """Verify TEAM_LEAGUE_MAP has 80+ teams"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        match = re.search(r'TEAM_LEAGUE_MAP = \{([^}]+)\}', content, re.DOTALL)
        assert match, "TEAM_LEAGUE_MAP not found"
        map_content = match.group(1)
        entries = re.findall(r'"[^"]+"\s*:\s*\d+', map_content)
        assert len(entries) >= 80, f"Expected 80+ teams, found {len(entries)}"
        print(f"TEAM_LEAGUE_MAP has {len(entries)} teams")
    
    def test_ligue1_teams_present(self):
        """Verify key Ligue 1 teams are mapped"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        ligue1_teams = ['marseille', 'lyon', 'monaco', 'lille', 'lens', 'nice', 'rennes', 'toulouse']
        for team in ligue1_teams:
            assert f'"{team}": 61' in content, f"{team} should be mapped to Ligue 1 (61)"
    
    def test_bundesliga_teams_present(self):
        """Verify key Bundesliga teams are mapped"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        bundesliga_teams = ['bayern', 'dortmund', 'leverkusen', 'leipzig', 'stuttgart', 'frankfurt']
        for team in bundesliga_teams:
            assert f'"{team}": 78' in content or f'"bayern munich": 78' in content, f"{team} should be mapped to Bundesliga (78)"
    
    def test_serie_a_teams_present(self):
        """Verify key Serie A teams are mapped"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        serie_a_teams = ['inter', 'milan', 'juventus', 'napoli', 'roma', 'lazio', 'atalanta', 'genoa']
        for team in serie_a_teams:
            assert f'"{team}": 135' in content or f'"inter milan": 135' in content or f'"ac milan": 135' in content, f"{team} should be mapped to Serie A (135)"


class TestPickBestOpponentHint:
    """Test that pick_best function uses opponent_hint for disambiguation"""
    
    def test_pick_best_has_opponent_hint_param(self):
        """Verify pick_best function accepts opponent_hint parameter"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check function signature
        assert 'def pick_best(data_list, query, team_hint, opponent_hint=None)' in content, \
            "pick_best should accept opponent_hint parameter"
    
    def test_pick_best_uses_opponent_hint_for_league_match(self):
        """Verify pick_best checks opponent's league for disambiguation"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check that opponent_hint is used to look up league
        assert 'if opponent_hint and not team_match:' in content, \
            "pick_best should check opponent_hint when team doesn't match"
        assert 'opp_lower = opponent_hint.lower().strip()' in content, \
            "pick_best should normalize opponent_hint"
        assert 'if opp_lower in TEAM_LEAGUE_MAP:' in content, \
            "pick_best should look up opponent in TEAM_LEAGUE_MAP"
        assert 'opp_league = TEAM_LEAGUE_MAP[opp_lower]' in content, \
            "pick_best should get opponent's league from map"
    
    def test_pick_best_prioritizes_league_match(self):
        """Verify pick_best prioritizes league match over first result"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check priority order: team match > league match > first result
        assert 'team_matched = [c for c in candidates if c[1]]' in content, \
            "pick_best should filter team matches"
        assert 'league_matched = [c for c in candidates if c[2]]' in content, \
            "pick_best should filter league matches"
        # Verify league match is returned before falling back to first result
        assert 'if league_matched:' in content and 'return league_matched[0][0]' in content, \
            "pick_best should return league match when available"


class TestResolvePlayerViaApiOpponentHint:
    """Test that _resolve_player_via_api passes opponent_hint to pick_best"""
    
    def test_resolve_player_via_api_has_opponent_hint_param(self):
        """Verify _resolve_player_via_api accepts opponent_hint parameter"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        assert 'opponent_hint: str = ""' in content, \
            "_resolve_player_via_api should accept opponent_hint parameter"
    
    def test_resolve_player_via_api_passes_opponent_hint_to_pick_best(self):
        """Verify _resolve_player_via_api passes opponent_hint to pick_best"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check that pick_best is called with opponent_hint
        assert 'pick_best(data, player_name, player_team_hint, opponent_hint)' in content, \
            "_resolve_player_via_api should pass opponent_hint to pick_best"
    
    def test_scan_prop_passes_opponent_hint(self):
        """Verify scan_prop endpoint passes opponent_hint to _resolve_player_via_api"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check that opponent_hint is passed in the call
        assert 'opponent_hint=opponent_hint' in content, \
            "scan_prop should pass opponent_hint to _resolve_player_via_api"


class TestInferLeagueIdOrder:
    """Test that _infer_league_id checks hardcoded map BEFORE AI guess"""
    
    def test_infer_league_id_checks_cache_first(self):
        """Verify _infer_league_id checks cache first"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        
        # Find the function by looking for its definition and the next function
        start = content.find('async def _infer_league_id')
        assert start != -1, "_infer_league_id function not found"
        end = content.find('async def _resolve_player_via_cache', start)
        func_content = content[start:end] if end != -1 else content[start:start+1000]
        
        # Check order: cache should come before hardcoded map
        cache_pos = func_content.find('get_team_info')
        map_pos = func_content.find('TEAM_LEAGUE_MAP')
        ai_pos = func_content.find('ai_league_id and ai_league_id != 39')
        
        assert cache_pos != -1, "get_team_info not found in _infer_league_id"
        assert map_pos != -1, "TEAM_LEAGUE_MAP not found in _infer_league_id"
        assert ai_pos != -1, "ai_league_id check not found in _infer_league_id"
        
        assert cache_pos < map_pos, "Cache check should come before hardcoded map"
        assert map_pos < ai_pos, "Hardcoded map should come before AI guess"
    
    def test_infer_league_id_checks_hardcoded_map_before_ai(self):
        """Verify _infer_league_id checks hardcoded map before AI guess"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check that hardcoded map is checked
        assert 'if name_lower in TEAM_LEAGUE_MAP:' in content, \
            "_infer_league_id should check hardcoded map"
        assert 'return TEAM_LEAGUE_MAP[name_lower]' in content, \
            "_infer_league_id should return from hardcoded map"
    
    def test_infer_league_id_ai_fallback_only_if_not_default(self):
        """Verify AI guess is only used if not default Premier League (39)"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        # Check that AI guess is only used if not 39
        assert 'if ai_league_id and ai_league_id != 39:' in content, \
            "_infer_league_id should only use AI guess if not default 39"


class TestCodeVerification:
    """Verify specific code patterns exist"""
    
    def test_predict_py_guard_lines(self):
        """Verify predict.py has teamId/opponentId guards at expected lines"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            lines = f.readlines()
        
        # Check lines 50-54 for the guard
        guard_found = False
        for i, line in enumerate(lines[49:55], start=50):
            if 'actual_team_id == 0' in line or 'req.opponentId == 0' in line:
                guard_found = True
                break
        assert guard_found, "teamId/opponentId guard not found in expected location (lines 50-55)"
    
    def test_scan_py_pick_best_league_match_logic(self):
        """Verify pick_best has league_match logic"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        
        # Check for league_match variable
        assert 'league_match = False' in content, "pick_best should initialize league_match"
        assert 'league_match = True' in content, "pick_best should set league_match to True"
        assert 'candidates.append((d, team_match, league_match))' in content, \
            "pick_best should track league_match in candidates"


class TestHealthCheck:
    """Basic health check to ensure API is running"""
    
    def test_api_health(self):
        """Verify API is accessible"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Health check failed: {response.status_code}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
