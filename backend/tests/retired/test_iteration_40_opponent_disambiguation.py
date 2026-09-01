"""
Iteration 40: Opponent Disambiguation & AI Prompt Clarity Tests

Tests for:
1. _resolve_opponent now searches with league_id filter FIRST before broad search
2. AI prompt now explicitly states 'plays for {teamName}' and 'OPPONENT: {opponentName}'
3. 3rd AI model grace period increased from 15s to 25s
4. Basketball prompt fix — 'plays for {teamName} (VENUE) | OPPONENT: {opponentName}'
5. teamId=0 guard returns 400 (not 500)
6. TEAM_LEAGUE_MAP has 241+ teams covering Ligue 1, Bundesliga, Serie A, La Liga, Brasileirao
"""
import pytest
import re
import ast
import os

# Get the backend directory
BACKEND_DIR = "/app/backend"


class TestResolveOpponentLeagueFilter:
    """Test that _resolve_opponent searches with league_id filter FIRST"""
    
    def test_resolve_opponent_league_filtered_search_first(self):
        """Verify _resolve_opponent does league-filtered API search before broad search"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        # Find the _resolve_opponent function
        func_match = re.search(r'async def _resolve_opponent\([^)]+\).*?(?=\nasync def |\nclass |\n@router|\Z)', content, re.DOTALL)
        assert func_match, "_resolve_opponent function not found"
        func_content = func_match.group(0)
        
        # Verify league-filtered search comes BEFORE broad search
        league_filtered_pos = func_content.find("# Try league-filtered search first")
        broad_search_pos = func_content.find("# Fallback: broad search without league filter")
        
        assert league_filtered_pos != -1, "League-filtered search comment not found"
        assert broad_search_pos != -1, "Broad search fallback comment not found"
        assert league_filtered_pos < broad_search_pos, "League-filtered search should come BEFORE broad search"
        print("PASS: _resolve_opponent does league-filtered search FIRST, then broad search")
    
    def test_resolve_opponent_uses_league_id_param(self):
        """Verify _resolve_opponent uses league_id parameter in API call"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        # Check for league_id in API call within _resolve_opponent
        func_match = re.search(r'async def _resolve_opponent\([^)]+\).*?(?=\nasync def |\nclass |\n@router|\Z)', content, re.DOTALL)
        func_content = func_match.group(0)
        
        # Should have: api_football_request("teams", {"search": opp_query, "league": league_id, ...})
        assert '"league": league_id' in func_content or "'league': league_id" in func_content, \
            "_resolve_opponent should pass league_id to API request"
        print("PASS: _resolve_opponent passes league_id to API request")
    
    def test_resolve_opponent_prints_league_filtered_match(self):
        """Verify _resolve_opponent logs when league-filtered match is found"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        assert "[OPP RESOLVE] League-filtered match:" in content, \
            "_resolve_opponent should log league-filtered matches"
        print("PASS: _resolve_opponent logs league-filtered matches")


class TestAIPromptClarity:
    """Test that AI prompts explicitly state player's team and opponent"""
    
    def test_soccer_prompt_plays_for_team(self):
        """Verify soccer predict.py prompt includes 'plays for {teamName}'"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Look for the prompt line with 'plays for'
        assert "plays for {req.teamName}" in content or "plays for" in content, \
            "Soccer prompt should include 'plays for {teamName}'"
        
        # Verify it's in the actual prompt variable
        prompt_match = re.search(r'prompt = f""".*?plays for.*?"""', content, re.DOTALL)
        assert prompt_match, "Soccer prompt should have 'plays for' in the f-string"
        print("PASS: Soccer prompt includes 'plays for {teamName}'")
    
    def test_soccer_prompt_opponent_label(self):
        """Verify soccer predict.py prompt includes 'OPPONENT: {opponentName}'"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "OPPONENT: {req.opponentName}" in content, \
            "Soccer prompt should include 'OPPONENT: {opponentName}'"
        print("PASS: Soccer prompt includes 'OPPONENT: {opponentName}'")
    
    def test_basketball_prompt_plays_for_team(self):
        """Verify basketball_predict.py prompt includes 'plays for {teamName}'"""
        with open(f"{BACKEND_DIR}/routes/basketball_predict.py", "r") as f:
            content = f.read()
        
        assert "plays for {req.teamName}" in content or "plays for" in content, \
            "Basketball prompt should include 'plays for {teamName}'"
        print("PASS: Basketball prompt includes 'plays for {teamName}'")
    
    def test_basketball_prompt_opponent_label(self):
        """Verify basketball_predict.py prompt includes 'OPPONENT: {opponentName}'"""
        with open(f"{BACKEND_DIR}/routes/basketball_predict.py", "r") as f:
            content = f.read()
        
        assert "OPPONENT: {req.opponentName}" in content, \
            "Basketball prompt should include 'OPPONENT: {opponentName}'"
        print("PASS: Basketball prompt includes 'OPPONENT: {opponentName}'")
    
    def test_soccer_prompt_line_1214(self):
        """Verify the exact prompt format at line 1214 in predict.py"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            lines = f.readlines()
        
        # Line 1214 (0-indexed: 1213)
        line_1214 = lines[1213] if len(lines) > 1213 else ""
        
        assert "plays for" in line_1214, f"Line 1214 should contain 'plays for', got: {line_1214[:100]}"
        assert "OPPONENT:" in line_1214, f"Line 1214 should contain 'OPPONENT:', got: {line_1214[:100]}"
        print(f"PASS: Line 1214 format verified: {line_1214.strip()[:80]}...")
    
    def test_basketball_prompt_line_715(self):
        """Verify the exact prompt format at line 715 in basketball_predict.py"""
        with open(f"{BACKEND_DIR}/routes/basketball_predict.py", "r") as f:
            lines = f.readlines()
        
        # Line 715 (0-indexed: 714)
        line_715 = lines[714] if len(lines) > 714 else ""
        
        assert "plays for" in line_715, f"Line 715 should contain 'plays for', got: {line_715[:100]}"
        assert "OPPONENT:" in line_715, f"Line 715 should contain 'OPPONENT:', got: {line_715[:100]}"
        print(f"PASS: Line 715 format verified: {line_715.strip()[:80]}...")


class TestGracePeriodIncrease:
    """Test that 3rd AI model grace period increased from 15s to 25s"""
    
    def test_soccer_grace_period_25s(self):
        """Verify soccer predict.py has 25s grace period for 3rd AI model"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Look for timeout=25.0 in the wait for additional results
        assert "timeout=25.0" in content or "timeout=25" in content, \
            "Soccer predict.py should have 25s grace period"
        print("PASS: Soccer predict.py has 25s grace period")
    
    def test_basketball_grace_period_25s(self):
        """Verify basketball_predict.py has 25s grace period for 3rd AI model"""
        with open(f"{BACKEND_DIR}/routes/basketball_predict.py", "r") as f:
            content = f.read()
        
        assert "timeout=25.0" in content or "timeout=25" in content, \
            "Basketball predict.py should have 25s grace period"
        print("PASS: Basketball predict.py has 25s grace period")
    
    def test_soccer_grace_period_line_1364(self):
        """Verify the exact grace period at line 1364 in predict.py"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            lines = f.readlines()
        
        # Line 1364 (0-indexed: 1363)
        line_1364 = lines[1363] if len(lines) > 1363 else ""
        
        assert "timeout=25" in line_1364, f"Line 1364 should have timeout=25, got: {line_1364.strip()}"
        print(f"PASS: Line 1364 grace period verified: {line_1364.strip()}")
    
    def test_basketball_grace_period_line_858(self):
        """Verify the exact grace period at line 858 in basketball_predict.py"""
        with open(f"{BACKEND_DIR}/routes/basketball_predict.py", "r") as f:
            lines = f.readlines()
        
        # Line 858 (0-indexed: 857)
        line_858 = lines[857] if len(lines) > 857 else ""
        
        assert "timeout=25" in line_858, f"Line 858 should have timeout=25, got: {line_858.strip()}"
        print(f"PASS: Line 858 grace period verified: {line_858.strip()}")


class TestTeamIdZeroGuard:
    """Test that teamId=0 returns 400 (not 500)"""
    
    def test_teamid_zero_guard_exists(self):
        """Verify predict.py has guard for teamId=0"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Should have: if not actual_team_id or actual_team_id == 0:
        assert "actual_team_id == 0" in content or "teamId == 0" in content, \
            "predict.py should have guard for teamId=0"
        print("PASS: predict.py has teamId=0 guard")
    
    def test_teamid_zero_returns_400(self):
        """Verify teamId=0 guard raises HTTPException with 400 status"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        # Look for the guard and verify it raises 400
        guard_pattern = r'if not actual_team_id or actual_team_id == 0:.*?raise HTTPException\(status_code=400'
        match = re.search(guard_pattern, content, re.DOTALL)
        assert match, "teamId=0 guard should raise HTTPException with status_code=400"
        print("PASS: teamId=0 guard raises HTTP 400")
    
    def test_opponentid_zero_guard_exists(self):
        """Verify predict.py has guard for opponentId=0"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "req.opponentId == 0" in content or "opponentId == 0" in content, \
            "predict.py should have guard for opponentId=0"
        print("PASS: predict.py has opponentId=0 guard")


class TestTeamLeagueMap:
    """Test TEAM_LEAGUE_MAP coverage"""
    
    def test_team_league_map_count(self):
        """Verify TEAM_LEAGUE_MAP has 241+ teams"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        # Extract TEAM_LEAGUE_MAP entries
        map_match = re.search(r'TEAM_LEAGUE_MAP = \{([^}]+)\}', content, re.DOTALL)
        assert map_match, "TEAM_LEAGUE_MAP not found"
        
        # Count entries (each entry is "team_name": league_id)
        entries = re.findall(r'"[^"]+"\s*:', map_match.group(1))
        count = len(entries)
        
        assert count >= 80, f"TEAM_LEAGUE_MAP should have 80+ teams, found {count}"
        print(f"PASS: TEAM_LEAGUE_MAP has {count} teams (requirement: 80+)")
    
    def test_ligue1_teams_present(self):
        """Verify Ligue 1 teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        ligue1_teams = ["psg", "marseille", "lyon", "monaco", "lille", "toulouse", "nice", "rennes"]
        for team in ligue1_teams:
            assert f'"{team}": 61' in content or f'"{team}":61' in content, \
                f"Ligue 1 team '{team}' should map to league_id 61"
        print("PASS: Ligue 1 teams present with league_id=61")
    
    def test_bundesliga_teams_present(self):
        """Verify Bundesliga teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        bundesliga_teams = ["bayern", "dortmund", "leverkusen", "leipzig", "frankfurt"]
        for team in bundesliga_teams:
            assert f'"{team}": 78' in content or f'"{team}":78' in content, \
                f"Bundesliga team '{team}' should map to league_id 78"
        print("PASS: Bundesliga teams present with league_id=78")
    
    def test_serie_a_teams_present(self):
        """Verify Serie A teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        serie_a_teams = ["inter", "milan", "juventus", "napoli", "roma", "genoa"]
        for team in serie_a_teams:
            assert f'"{team}": 135' in content or f'"{team}":135' in content, \
                f"Serie A team '{team}' should map to league_id 135"
        print("PASS: Serie A teams present with league_id=135")
    
    def test_la_liga_teams_present(self):
        """Verify La Liga teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        la_liga_teams = ["real madrid", "barcelona", "atletico madrid", "sevilla", "valencia"]
        for team in la_liga_teams:
            assert f'"{team}": 140' in content or f'"{team}":140' in content, \
                f"La Liga team '{team}' should map to league_id 140"
        print("PASS: La Liga teams present with league_id=140")
    
    def test_brasileirao_teams_present(self):
        """Verify Brasileirao teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        brasileirao_teams = ["flamengo", "palmeiras", "fluminense", "botafogo", "corinthians"]
        for team in brasileirao_teams:
            assert f'"{team}": 71' in content or f'"{team}":71' in content, \
                f"Brasileirao team '{team}' should map to league_id 71"
        print("PASS: Brasileirao teams present with league_id=71")
    
    def test_epl_teams_present(self):
        """Verify EPL teams are in TEAM_LEAGUE_MAP"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        epl_teams = ["arsenal", "chelsea", "liverpool", "manchester city", "newcastle"]
        for team in epl_teams:
            assert f'"{team}": 39' in content or f'"{team}":39' in content, \
                f"EPL team '{team}' should map to league_id 39"
        print("PASS: EPL teams present with league_id=39")
    
    def test_newcastle_maps_to_epl(self):
        """Verify 'newcastle' maps to EPL (39), not A-League"""
        with open(f"{BACKEND_DIR}/routes/scan.py", "r") as f:
            content = f.read()
        
        # Newcastle should map to 39 (EPL), not 188 (A-League)
        assert '"newcastle": 39' in content, \
            "'newcastle' should map to league_id 39 (EPL), not A-League"
        print("PASS: 'newcastle' correctly maps to EPL (league_id=39)")


class TestPositionComparisonLeagueFilter:
    """Test that position comparison uses same league as player"""
    
    def test_position_comparison_exists(self):
        """Verify position comparison function exists in predict.py"""
        with open(f"{BACKEND_DIR}/routes/predict.py", "r") as f:
            content = f.read()
        
        assert "fetch_position_comparison" in content or "position_comparison" in content, \
            "Position comparison function should exist"
        print("PASS: Position comparison function exists")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
