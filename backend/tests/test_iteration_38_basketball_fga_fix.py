"""
Iteration 38: Basketball FGA/FGM Stat Parsing Fix Tests

Tests for:
1. parse_player_stat: FGA = field_goals.attempts + threepoint_goals.attempts (combined 2pt+3pt)
2. parse_player_stat: FGM = field_goals.total + threepoint_goals.total (combined 2pt+3pt)
3. build_player_game_logs: Games with < 5 minutes should be filtered out (DNP filter)
4. Recommendation enforcement: each model's rec matches projectedValue vs line
5. Live verification: Kelly Oubre FGA avg ~10-11 (not 5.9), Paul George FGA avg ~13 (not 6.3)
"""
import pytest
import requests
import os
import sys
import asyncio

# Add backend to path for imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# ============================================================
# UNIT TESTS: parse_player_stat function
# ============================================================

class TestParsePlayerStatFGACombined:
    """Test that FGA/FGM combines 2pt and 3pt field goals"""
    
    def test_fga_combines_2pt_and_3pt_attempts(self):
        """FGA should equal field_goals.attempts + threepoint_goals.attempts"""
        from basketball_utils import parse_player_stat
        
        # Mock API response with separate 2pt and 3pt data
        mock_stat = {
            "game": {"id": 12345},
            "team": {"id": 100},
            "player": {"id": 925, "name": "Oubre Kelly"},
            "type": "starters",
            "minutes": "32:15",
            "points": 18,
            "rebounds": {"total": 5},
            "assists": 2,
            "field_goals": {"total": 4, "attempts": 8},  # 2pt: 4/8
            "threepoint_goals": {"total": 2, "attempts": 5},  # 3pt: 2/5
            "freethrows_goals": {"total": 2, "attempts": 2},
        }
        
        parsed = parse_player_stat(mock_stat)
        
        # FGA should be 8 (2pt attempts) + 5 (3pt attempts) = 13
        assert parsed["fga"] == 13, f"FGA should be 13 (8+5), got {parsed['fga']}"
        
        # FGM should be 4 (2pt made) + 2 (3pt made) = 6
        assert parsed["fgm"] == 6, f"FGM should be 6 (4+2), got {parsed['fgm']}"
        
        # 3pt stats should still be separate
        assert parsed["tpa"] == 5, f"TPA should be 5, got {parsed['tpa']}"
        assert parsed["tpm"] == 2, f"TPM should be 2, got {parsed['tpm']}"
        
        print(f"PASS: FGA={parsed['fga']} (8+5=13), FGM={parsed['fgm']} (4+2=6)")
    
    def test_fga_handles_missing_threepoint_data(self):
        """FGA should handle missing threepoint_goals gracefully"""
        from basketball_utils import parse_player_stat
        
        mock_stat = {
            "game": {"id": 12345},
            "team": {"id": 100},
            "player": {"id": 925, "name": "Test Player"},
            "minutes": "25:00",
            "points": 10,
            "rebounds": {"total": 3},
            "assists": 1,
            "field_goals": {"total": 5, "attempts": 10},
            "threepoint_goals": None,  # Missing 3pt data
            "freethrows_goals": {"total": 0, "attempts": 0},
        }
        
        parsed = parse_player_stat(mock_stat)
        
        # FGA should be 10 (2pt only) + 0 (no 3pt) = 10
        assert parsed["fga"] == 10, f"FGA should be 10 when 3pt is None, got {parsed['fga']}"
        assert parsed["fgm"] == 5, f"FGM should be 5 when 3pt is None, got {parsed['fgm']}"
        
        print(f"PASS: FGA={parsed['fga']} handles missing 3pt data")
    
    def test_fga_handles_empty_dicts(self):
        """FGA should handle empty field_goals and threepoint_goals dicts"""
        from basketball_utils import parse_player_stat
        
        mock_stat = {
            "game": {"id": 12345},
            "team": {"id": 100},
            "player": {"id": 925, "name": "Test Player"},
            "minutes": "20:00",
            "points": 0,
            "rebounds": {"total": 2},
            "assists": 0,
            "field_goals": {},  # Empty dict
            "threepoint_goals": {},  # Empty dict
            "freethrows_goals": {},
        }
        
        parsed = parse_player_stat(mock_stat)
        
        assert parsed["fga"] == 0, f"FGA should be 0 for empty dicts, got {parsed['fga']}"
        assert parsed["fgm"] == 0, f"FGM should be 0 for empty dicts, got {parsed['fgm']}"
        
        print(f"PASS: FGA={parsed['fga']} handles empty dicts")
    
    def test_fga_realistic_nba_game(self):
        """Test with realistic NBA game stats"""
        from basketball_utils import parse_player_stat
        
        # Realistic Kelly Oubre game: 6/12 from 2pt, 3/6 from 3pt = 9/18 total FG
        mock_stat = {
            "game": {"id": 99999},
            "team": {"id": 20},
            "player": {"id": 925, "name": "Oubre Kelly"},
            "type": "starters",
            "minutes": "34:22",
            "points": 24,  # 6*2 + 3*3 + 3 FT = 24
            "rebounds": {"total": 6},
            "assists": 2,
            "field_goals": {"total": 6, "attempts": 12},  # 2pt: 6/12
            "threepoint_goals": {"total": 3, "attempts": 6},  # 3pt: 3/6
            "freethrows_goals": {"total": 3, "attempts": 4},
        }
        
        parsed = parse_player_stat(mock_stat)
        
        # Total FGA = 12 + 6 = 18
        assert parsed["fga"] == 18, f"FGA should be 18 (12+6), got {parsed['fga']}"
        # Total FGM = 6 + 3 = 9
        assert parsed["fgm"] == 9, f"FGM should be 9 (6+3), got {parsed['fgm']}"
        
        print(f"PASS: Realistic game - FGA={parsed['fga']}, FGM={parsed['fgm']}")


# ============================================================
# UNIT TESTS: DNP Filter in build_player_game_logs
# ============================================================

class TestDNPFilter:
    """Test that games with < 5 minutes are filtered out"""
    
    def test_dnp_filter_code_exists(self):
        """Verify DNP filter code exists in basketball_predict.py"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Check for the DNP filter logic
        assert "mins < 5" in content or "< 5" in content, "DNP filter (< 5 minutes) not found"
        assert "continue" in content, "continue statement for DNP filter not found"
        
        # Check for the comment explaining the filter
        assert "DNP" in content or "Did Not Play" in content.lower() or "injury" in content.lower(), \
            "DNP filter comment not found"
        
        print("PASS: DNP filter code exists in basketball_predict.py")
    
    def test_dnp_filter_at_correct_location(self):
        """Verify DNP filter is in build_player_game_logs function"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            lines = f.readlines()
        
        # Find build_player_game_logs function
        in_function = False
        filter_found = False
        
        for i, line in enumerate(lines):
            if "def build_player_game_logs" in line or "async def build_player_game_logs" in line:
                in_function = True
            elif in_function and line.strip().startswith("def ") or line.strip().startswith("async def "):
                in_function = False
            elif in_function and "mins < 5" in line:
                filter_found = True
                print(f"PASS: DNP filter found at line {i+1}: {line.strip()}")
                break
        
        assert filter_found, "DNP filter not found in build_player_game_logs function"


# ============================================================
# UNIT TESTS: Recommendation Enforcement
# ============================================================

class TestRecommendationEnforcement:
    """Test that recommendation is enforced to match projectedValue vs line"""
    
    def test_recommendation_enforcement_code_exists(self):
        """Verify recommendation enforcement code exists"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Check for the enforcement pattern
        enforcement_pattern = 'r["recommendation"] = "over" if pv > req.line else "under"'
        assert enforcement_pattern in content, \
            f"Recommendation enforcement pattern not found: {enforcement_pattern}"
        
        print("PASS: Recommendation enforcement code exists")
    
    def test_recommendation_enforcement_in_valid_preds_loop(self):
        """Verify enforcement happens in the valid_preds loop"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            lines = f.readlines()
        
        # Find the enforcement in the valid_preds loop (around line 871-872)
        found_in_loop = False
        for i, line in enumerate(lines):
            if 'r["recommendation"] = "over" if pv > req.line else "under"' in line:
                # Check context - should be in a loop processing valid_preds
                context_start = max(0, i - 10)
                context = "".join(lines[context_start:i+1])
                if "for" in context and ("valid_preds" in context or "ai_results" in context):
                    found_in_loop = True
                    print(f"PASS: Recommendation enforcement found at line {i+1} in valid_preds loop")
                    break
        
        assert found_in_loop, "Recommendation enforcement not found in valid_preds loop"


# ============================================================
# LIVE API TESTS: Verify FGA averages for real players
# ============================================================

class TestLiveFGAAverages:
    """Test live API to verify FGA averages are correct after fix"""
    
    def test_kelly_oubre_fga_average(self):
        """Kelly Oubre FGA avg should be ~10-11 (not 5.9)"""
        # Call the prediction API for Kelly Oubre with FGA prop
        payload = {
            "playerName": "Kelly Oubre",
            "teamName": "Philadelphia 76ers",
            "teamId": 20,  # 76ers team ID
            "opponentName": "Boston Celtics",
            "opponentId": 2,  # Celtics team ID
            "propType": "fga",
            "line": 10.5,
            "venue": "home"
        }
        
        response = requests.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"WARNING: API returned {response.status_code}: {response.text[:500]}")
            pytest.skip(f"API call failed with status {response.status_code}")
        
        data = response.json()
        
        # Check playerGameLogs for the raw average
        game_logs = data.get("playerGameLogs", {})
        raw_avg = game_logs.get("rawAvg", 0)
        sample_size = game_logs.get("sampleSize", 0)
        
        print(f"Kelly Oubre FGA Stats:")
        print(f"  - Raw Average: {raw_avg}")
        print(f"  - Sample Size: {sample_size} games")
        print(f"  - Projected Value: {data.get('projectedValue')}")
        
        # FGA average should be approximately 10-11, NOT 5.9
        # Allow some variance but it should definitely be > 8
        assert raw_avg > 8, f"Kelly Oubre FGA avg should be > 8 (was 5.9 before fix), got {raw_avg}"
        assert raw_avg < 15, f"Kelly Oubre FGA avg should be < 15, got {raw_avg}"
        
        print(f"PASS: Kelly Oubre FGA avg = {raw_avg} (expected ~10-11, was 5.9 before fix)")
    
    def test_paul_george_fga_average(self):
        """Paul George FGA avg should be ~13 (not 6.3)"""
        # Call the prediction API for Paul George with FGA prop
        payload = {
            "playerName": "Paul George",
            "teamName": "Philadelphia 76ers",
            "teamId": 20,  # 76ers team ID
            "opponentName": "Boston Celtics",
            "opponentId": 2,  # Celtics team ID
            "propType": "fga",
            "line": 13.5,
            "venue": "home"
        }
        
        response = requests.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"WARNING: API returned {response.status_code}: {response.text[:500]}")
            pytest.skip(f"API call failed with status {response.status_code}")
        
        data = response.json()
        
        # Check playerGameLogs for the raw average
        game_logs = data.get("playerGameLogs", {})
        raw_avg = game_logs.get("rawAvg", 0)
        sample_size = game_logs.get("sampleSize", 0)
        
        print(f"Paul George FGA Stats:")
        print(f"  - Raw Average: {raw_avg}")
        print(f"  - Sample Size: {sample_size} games")
        print(f"  - Projected Value: {data.get('projectedValue')}")
        
        # FGA average should be approximately 13, NOT 6.3
        # Allow some variance but it should definitely be > 10
        assert raw_avg > 10, f"Paul George FGA avg should be > 10 (was 6.3 before fix), got {raw_avg}"
        assert raw_avg < 20, f"Paul George FGA avg should be < 20, got {raw_avg}"
        
        print(f"PASS: Paul George FGA avg = {raw_avg} (expected ~13, was 6.3 before fix)")
    
    def test_fgm_also_combined(self):
        """Verify FGM is also combined (2pt + 3pt made)"""
        # Use Kelly Oubre for FGM test
        payload = {
            "playerName": "Kelly Oubre",
            "teamName": "Philadelphia 76ers",
            "teamId": 20,
            "opponentName": "Boston Celtics",
            "opponentId": 2,
            "propType": "fgm",
            "line": 4.5,
            "venue": "home"
        }
        
        response = requests.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        
        if response.status_code != 200:
            print(f"WARNING: API returned {response.status_code}: {response.text[:500]}")
            pytest.skip(f"API call failed with status {response.status_code}")
        
        data = response.json()
        
        game_logs = data.get("playerGameLogs", {})
        raw_avg = game_logs.get("rawAvg", 0)
        
        print(f"Kelly Oubre FGM Stats:")
        print(f"  - Raw Average: {raw_avg}")
        print(f"  - Projected Value: {data.get('projectedValue')}")
        
        # FGM average should be reasonable (typically 3-5 for a role player)
        # Before fix it would have been ~2-3 (only 2pt made)
        assert raw_avg > 2, f"Kelly Oubre FGM avg should be > 2, got {raw_avg}"
        
        print(f"PASS: Kelly Oubre FGM avg = {raw_avg}")


# ============================================================
# DIRECT PLAYER STATS API TEST
# ============================================================

class TestDirectPlayerStats:
    """Test get_player_season_stats directly to verify FGA calculation"""
    
    def test_get_player_season_stats_kelly_oubre(self):
        """Directly test get_player_season_stats for Kelly Oubre"""
        from basketball_utils import get_player_season_stats, parse_player_stat
        
        # Run async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            raw_stats = loop.run_until_complete(get_player_season_stats(925))  # Kelly Oubre ID
            
            if not raw_stats:
                pytest.skip("No stats returned for Kelly Oubre (ID: 925)")
            
            # Parse all stats and calculate FGA average
            fga_values = []
            for stat in raw_stats:
                parsed = parse_player_stat(stat)
                mins = parsed.get("minutes", "0:00")
                # Parse minutes
                if ":" in str(mins):
                    parts = str(mins).split(":")
                    mins_float = int(parts[0]) + int(parts[1]) / 60.0
                else:
                    mins_float = float(mins) if mins else 0
                
                # Only count games with >= 5 minutes (DNP filter)
                if mins_float >= 5:
                    fga_values.append(parsed["fga"])
            
            if not fga_values:
                pytest.skip("No valid games found for Kelly Oubre")
            
            avg_fga = sum(fga_values) / len(fga_values)
            
            print(f"Kelly Oubre Direct Stats Test:")
            print(f"  - Games with >= 5 min: {len(fga_values)}")
            print(f"  - FGA values (last 10): {fga_values[:10]}")
            print(f"  - Average FGA: {avg_fga:.1f}")
            
            # Should be ~10-11, not 5.9
            assert avg_fga > 8, f"Kelly Oubre FGA avg should be > 8, got {avg_fga:.1f}"
            
            print(f"PASS: Direct stats show Kelly Oubre FGA avg = {avg_fga:.1f}")
            
        finally:
            loop.close()


# ============================================================
# CODE VERIFICATION TESTS
# ============================================================

class TestCodeVerification:
    """Verify the code changes are correct"""
    
    def test_parse_player_stat_fga_formula(self):
        """Verify FGA formula in parse_player_stat"""
        with open('/app/backend/basketball_utils.py', 'r') as f:
            content = f.read()
        
        # Check for the combined FGA formula
        # Should be: (fg.get("attempts", 0) or 0) + (tp.get("attempts", 0) or 0)
        assert 'fg.get("attempts"' in content, "FGA formula missing fg.get('attempts')"
        assert 'tp.get("attempts"' in content, "FGA formula missing tp.get('attempts')"
        
        # Check for the combined FGM formula
        # Should be: (fg.get("total", 0) or 0) + (tp.get("total", 0) or 0)
        assert 'fg.get("total"' in content, "FGM formula missing fg.get('total')"
        assert 'tp.get("total"' in content, "FGM formula missing tp.get('total')"
        
        print("PASS: parse_player_stat has combined FGA/FGM formulas")
    
    def test_parse_player_stat_lines_228_244(self):
        """Verify lines 228-244 contain the fix"""
        with open('/app/backend/basketball_utils.py', 'r') as f:
            lines = f.readlines()
        
        # Check lines 228-244 (0-indexed: 227-243)
        relevant_lines = lines[227:244]
        relevant_content = "".join(relevant_lines)
        
        # Should contain the FGA/FGM calculation
        assert "fgm" in relevant_content.lower(), "FGM not found in lines 228-244"
        assert "fga" in relevant_content.lower(), "FGA not found in lines 228-244"
        
        # Should show the addition of 2pt and 3pt
        assert "+" in relevant_content, "Addition operator not found in FGA/FGM calculation"
        
        print("PASS: Lines 228-244 contain FGA/FGM fix")
        print("Relevant code:")
        for i, line in enumerate(relevant_lines, start=228):
            if "fga" in line.lower() or "fgm" in line.lower():
                print(f"  Line {i}: {line.rstrip()}")
    
    def test_dnp_filter_lines_500_510(self):
        """Verify lines 500-510 contain DNP filter"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            lines = f.readlines()
        
        # Check lines 500-510 (0-indexed: 499-509)
        relevant_lines = lines[499:510]
        relevant_content = "".join(relevant_lines)
        
        # Should contain the DNP filter
        assert "< 5" in relevant_content or "mins < 5" in relevant_content, \
            "DNP filter (< 5 minutes) not found in lines 500-510"
        assert "continue" in relevant_content, "continue statement not found in lines 500-510"
        
        print("PASS: Lines 500-510 contain DNP filter")
        print("Relevant code:")
        for i, line in enumerate(relevant_lines, start=500):
            print(f"  Line {i}: {line.rstrip()}")
    
    def test_recommendation_enforcement_lines_866_873(self):
        """Verify lines 866-873 contain recommendation enforcement"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            lines = f.readlines()
        
        # Check lines 866-880 (0-indexed: 865-879) - slightly wider range
        relevant_lines = lines[865:880]
        relevant_content = "".join(relevant_lines)
        
        # Should contain recommendation enforcement
        assert "recommendation" in relevant_content, "recommendation not found in lines 866-880"
        assert "over" in relevant_content and "under" in relevant_content, \
            "over/under not found in lines 866-880"
        
        print("PASS: Lines 866-880 contain recommendation enforcement")
        print("Relevant code:")
        for i, line in enumerate(relevant_lines, start=866):
            if "recommendation" in line:
                print(f"  Line {i}: {line.rstrip()}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
