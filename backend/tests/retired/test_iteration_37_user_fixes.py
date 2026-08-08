"""
Test iteration 37 - 4 specific user-reported issues from production:
1. GP model says OVER with 58.2 when line is 60.5 — recommendation contradicts projected value
   FIX: Each model's recommendation is enforced to match projected value vs line BEFORE consensus
2. Position comparison shows irrelevant players (Alex Sandro as CB when he's LB)
   FIX: Position comparison accepts target_specific_pos parameter and filters out mismatches
3. Same team appears 4 times in comparison (4 Flamengo players)
   FIX: Position comparison dedup limits to max 1 player per team
4. Not enough diverse data in comparisons
   FIX: Position comparison searches up to 10 fixtures (increased from 5)
"""
import pytest
import os
import re
import ast

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestRecommendationEnforcement:
    """Issue 1: Recommendation must match projected value vs line"""
    
    def test_soccer_recommendation_enforcement_in_valid_preds_loop(self):
        """Verify predict.py enforces recommendation in valid_preds collection loop"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Find the valid_preds loop section
        # Should have: r["recommendation"] = "over" if pv > req.line else "under"
        assert 'r["recommendation"] = "over" if pv > req.line else "under"' in content, \
            "predict.py should enforce recommendation in valid_preds loop"
        
        # Verify it's inside the for loop that processes ai_results
        lines = content.split('\n')
        found_enforcement = False
        in_valid_preds_loop = False
        
        for i, line in enumerate(lines):
            if 'for i, r in enumerate(ai_results)' in line:
                in_valid_preds_loop = True
            if in_valid_preds_loop and 'r["recommendation"] = "over" if pv > req.line else "under"' in line:
                found_enforcement = True
                # Check there's a comment explaining the enforcement
                prev_line = lines[i-1] if i > 0 else ""
                assert 'ENFORCE' in prev_line or 'ENFORCE' in line, \
                    "Enforcement should have ENFORCE comment"
                break
            if in_valid_preds_loop and 'valid_preds = []' in line and found_enforcement:
                break  # Exited the loop
        
        assert found_enforcement, "Recommendation enforcement should be inside valid_preds loop"
        print("PASS: Soccer predict.py enforces recommendation in valid_preds loop")
    
    def test_soccer_consensus_recommendation_enforcement(self):
        """Verify predict.py also enforces recommendation after averaging projectedValue"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # After averaging, should also enforce: prediction["recommendation"] = "over" if avg_proj > req.line else "under"
        assert 'prediction["recommendation"] = "over" if avg_proj > req.line else "under"' in content, \
            "predict.py should enforce recommendation after averaging projectedValue"
        
        print("PASS: Soccer predict.py enforces recommendation after consensus averaging")
    
    def test_basketball_recommendation_enforcement_in_valid_preds_loop(self):
        """Verify basketball_predict.py enforces recommendation in valid_preds collection loop"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Should have: r["recommendation"] = "over" if pv > req.line else "under"
        assert 'r["recommendation"] = "over" if pv > req.line else "under"' in content, \
            "basketball_predict.py should enforce recommendation in valid_preds loop"
        
        # Verify it's inside the for loop
        lines = content.split('\n')
        found_enforcement = False
        in_valid_preds_loop = False
        
        for i, line in enumerate(lines):
            if 'for i, r in enumerate(ai_results)' in line:
                in_valid_preds_loop = True
            if in_valid_preds_loop and 'r["recommendation"] = "over" if pv > req.line else "under"' in line:
                found_enforcement = True
                prev_line = lines[i-1] if i > 0 else ""
                assert 'ENFORCE' in prev_line or 'ENFORCE' in line, \
                    "Basketball enforcement should have ENFORCE comment"
                break
        
        assert found_enforcement, "Basketball recommendation enforcement should be inside valid_preds loop"
        print("PASS: Basketball predict.py enforces recommendation in valid_preds loop")
    
    def test_basketball_consensus_recommendation_enforcement(self):
        """Verify basketball_predict.py also enforces recommendation after averaging"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # After averaging: prediction["recommendation"] = "over" if avg_proj > req.line else "under"
        assert 'prediction["recommendation"] = "over" if avg_proj > req.line else "under"' in content, \
            "basketball_predict.py should enforce recommendation after averaging"
        
        print("PASS: Basketball predict.py enforces recommendation after consensus averaging")


class TestPositionComparisonSpecificPosFilter:
    """Issue 2: Position comparison should filter by specific position (CB vs LB)"""
    
    def test_fetch_position_comparison_accepts_target_specific_pos(self):
        """Verify fetch_position_comparison function accepts target_specific_pos parameter"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Check function signature includes target_specific_pos
        func_pattern = r'async def fetch_position_comparison\([^)]*target_specific_pos[^)]*\)'
        assert re.search(func_pattern, content), \
            "fetch_position_comparison should accept target_specific_pos parameter"
        
        print("PASS: fetch_position_comparison accepts target_specific_pos parameter")
    
    def test_position_comparison_filters_by_specific_position(self):
        """Verify position comparison filters out players with mismatched specific positions"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should have filter logic: if target_specific_pos and spec_pos and spec_pos != target_specific_pos: continue
        assert 'if target_specific_pos and spec_pos and spec_pos != target_specific_pos:' in content, \
            "Should filter by specific position when target has one"
        
        # Should have continue statement after the filter
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if target_specific_pos and spec_pos and spec_pos != target_specific_pos:' in line:
                next_line = lines[i+1] if i+1 < len(lines) else ""
                assert 'continue' in next_line, "Should skip players with mismatched position"
                break
        
        print("PASS: Position comparison filters by specific position")
    
    def test_position_comparison_call_passes_specific_position(self):
        """Verify the call site passes target_specific_pos=specific_position"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should call with target_specific_pos=specific_position
        assert 'target_specific_pos=specific_position' in content, \
            "Call site should pass target_specific_pos=specific_position"
        
        print("PASS: Position comparison call passes specific_position")


class TestPositionComparisonTeamDedup:
    """Issue 3: Same team appearing multiple times (4 Flamengo players)"""
    
    def test_position_comparison_max_one_per_team(self):
        """Verify position comparison limits to max 1 player per team"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should have: if team and seen_teams.get(team, 0) >= 1: continue
        assert 'seen_teams.get(team, 0) >= 1' in content, \
            "Should check if team already has 1 player"
        
        # Should have comment about max 1 per team
        assert 'Max 1 player per team' in content or 'max 1 per team' in content.lower(), \
            "Should have comment about max 1 per team"
        
        print("PASS: Position comparison limits to max 1 player per team")
    
    def test_position_comparison_tracks_seen_teams(self):
        """Verify position comparison tracks seen teams with counter"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should have seen_teams dict
        assert 'seen_teams = {}' in content, "Should initialize seen_teams dict"
        
        # Should increment team counter
        assert 'seen_teams[team] = seen_teams.get(team, 0) + 1' in content, \
            "Should increment team counter"
        
        print("PASS: Position comparison tracks seen teams")


class TestPositionComparisonFixtureLimit:
    """Issue 4: Not enough diverse data - fixture limit increased"""
    
    def test_position_comparison_limit_is_10(self):
        """Verify fetch_position_comparison default limit is 10 (not 5)"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Check function signature has limit=10
        func_pattern = r'async def fetch_position_comparison\([^)]*limit=10[^)]*\)'
        assert re.search(func_pattern, content), \
            "fetch_position_comparison should have limit=10 as default"
        
        print("PASS: fetch_position_comparison default limit is 10")
    
    def test_position_comparison_call_uses_limit_10(self):
        """Verify the call site passes limit=10 or uses default"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Find the call to fetch_position_comparison
        # Should either explicitly pass 10 or rely on default
        call_pattern = r'fetch_position_comparison\([^)]+\)'
        matches = re.findall(call_pattern, content)
        
        found_call = False
        for match in matches:
            if 'opponent_fixture_list' in match:
                found_call = True
                # Either has limit=10 or no limit (uses default)
                if 'limit=' in match:
                    assert 'limit=10' in match or ', 10,' in match, \
                        f"Call should use limit=10, found: {match}"
                break
        
        assert found_call, "Should have call to fetch_position_comparison"
        print("PASS: Position comparison call uses limit 10")
    
    def test_tasks_slice_uses_limit_parameter(self):
        """Verify tasks are created using the limit parameter"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should have: tasks = [fetch_pos_from_fixture(f) for f in opp_fixtures[:limit]]
        assert 'opp_fixtures[:limit]' in content, \
            "Tasks should slice opp_fixtures using limit parameter"
        
        print("PASS: Tasks slice uses limit parameter")


class TestCodeQuality:
    """Additional code quality checks for the fixes"""
    
    def test_no_hardcoded_fixture_limit_5(self):
        """Verify no hardcoded limit of 5 for position comparison"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Find fetch_position_comparison function
        func_start = content.find('async def fetch_position_comparison')
        func_end = content.find('async def', func_start + 1)
        if func_end == -1:
            func_end = len(content)
        func_content = content[func_start:func_end]
        
        # Should not have hardcoded [:5] for fixtures
        # Allow [:5] for other things like game logs
        lines = func_content.split('\n')
        for line in lines:
            if 'opp_fixtures[:5]' in line or 'fixtures[:5]' in line:
                pytest.fail(f"Found hardcoded [:5] in position comparison: {line.strip()}")
        
        print("PASS: No hardcoded fixture limit of 5 in position comparison")
    
    def test_recommendation_logic_is_consistent(self):
        """Verify recommendation logic is consistent: over if proj > line, under otherwise"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # All recommendation assignments should use the same logic pattern
        # Pattern: "over" if <var> > req.line else "under"
        rec_patterns = re.findall(r'\["recommendation"\]\s*=\s*"over"\s+if\s+\w+\s*>\s*req\.line\s+else\s+"under"', content)
        
        # Should have at least 2 (one in loop, one after averaging)
        assert len(rec_patterns) >= 2, f"Should have at least 2 recommendation enforcements, found {len(rec_patterns)}"
        
        print(f"PASS: Found {len(rec_patterns)} consistent recommendation enforcements in predict.py")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
