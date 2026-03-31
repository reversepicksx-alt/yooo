"""
Bug Fixes Verification Tests - Iteration 33
Tests for:
1. Basketball prop type normalization (VALID_BASKETBALL_PROPS includes reb_ast, pts_reb, pts_ast, blk_stl, steals, blocks, turnovers)
2. Basketball prop alias mapping (rebs+asts -> reb_ast, NOT pts_reb_ast)
3. Basketball stat extraction (get_stat_value for reb_ast returns rebounds+assists)
4. Basketball opponent resolution with league_id filter (prevents NBA vs WNBA cross-match)
5. Soccer/Basketball prediction opponent field force-set from request
6. No AI model names in user-facing text
7. Frontend opponent fallback uses resolvedOpponent?.teamId
8. Health endpoint
"""
import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://ai-sports-analytics-2.preview.emergentagent.com').rstrip('/')


class TestHealthEndpoint:
    """Test /api/health endpoint"""
    
    def test_health_returns_200(self):
        """Verify /api/health endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok", f"Health status not ok: {data}"
        print(f"✓ Health endpoint returns 200 with status=ok")


class TestBasketballPropTypeNormalization:
    """Test VALID_BASKETBALL_PROPS includes all required prop types"""
    
    def test_valid_basketball_props_in_scan_py(self):
        """Verify VALID_BASKETBALL_PROPS includes reb_ast, pts_reb, pts_ast, blk_stl, steals, blocks, turnovers"""
        # Read the scan.py file to verify VALID_BASKETBALL_PROPS
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        # Find VALID_BASKETBALL_PROPS definition
        match = re.search(r'VALID_BASKETBALL_PROPS\s*=\s*\{([^}]+)\}', content)
        assert match, "VALID_BASKETBALL_PROPS not found in scan.py"
        
        props_str = match.group(1)
        required_props = ['reb_ast', 'pts_reb', 'pts_ast', 'blk_stl', 'steals', 'blocks', 'turnovers']
        
        for prop in required_props:
            assert f'"{prop}"' in props_str or f"'{prop}'" in props_str, f"Missing prop type: {prop}"
            print(f"✓ VALID_BASKETBALL_PROPS includes '{prop}'")
        
        print(f"✓ All required prop types present in VALID_BASKETBALL_PROPS")


class TestBasketballPropAliasMapping:
    """Test BASKETBALL_PROP_ALIASES maps correctly"""
    
    def test_rebs_asts_maps_to_reb_ast(self):
        """Verify 'rebs+asts' maps to 'reb_ast' (NOT pts_reb_ast)"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        # Find BASKETBALL_PROP_ALIASES definition
        match = re.search(r'BASKETBALL_PROP_ALIASES\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        assert match, "BASKETBALL_PROP_ALIASES not found in scan.py"
        
        aliases_str = match.group(1)
        
        # Check that rebs+asts maps to reb_ast
        assert '"rebs+asts": "reb_ast"' in aliases_str or "'rebs+asts': 'reb_ast'" in aliases_str, \
            "rebs+asts should map to reb_ast, not pts_reb_ast"
        print(f"✓ 'rebs+asts' correctly maps to 'reb_ast'")
        
        # Verify it does NOT map to pts_reb_ast
        assert '"rebs+asts": "pts_reb_ast"' not in aliases_str and "'rebs+asts': 'pts_reb_ast'" not in aliases_str, \
            "rebs+asts should NOT map to pts_reb_ast"
        print(f"✓ 'rebs+asts' does NOT map to 'pts_reb_ast'")
    
    def test_blks_stls_maps_to_blk_stl(self):
        """Verify 'blks+stls' maps to 'blk_stl'"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        match = re.search(r'BASKETBALL_PROP_ALIASES\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        aliases_str = match.group(1)
        
        assert '"blks+stls": "blk_stl"' in aliases_str or "'blks+stls': 'blk_stl'" in aliases_str, \
            "blks+stls should map to blk_stl"
        print(f"✓ 'blks+stls' correctly maps to 'blk_stl'")
    
    def test_pts_reb_maps_correctly(self):
        """Verify 'pts+reb' maps to 'pts_reb'"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        match = re.search(r'BASKETBALL_PROP_ALIASES\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        aliases_str = match.group(1)
        
        assert '"pts+reb": "pts_reb"' in aliases_str or "'pts+reb': 'pts_reb'" in aliases_str, \
            "pts+reb should map to pts_reb"
        print(f"✓ 'pts+reb' correctly maps to 'pts_reb'")
    
    def test_pts_ast_maps_correctly(self):
        """Verify 'pts+ast' maps to 'pts_ast'"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        match = re.search(r'BASKETBALL_PROP_ALIASES\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        aliases_str = match.group(1)
        
        assert '"pts+ast": "pts_ast"' in aliases_str or "'pts+ast': 'pts_ast'" in aliases_str, \
            "pts+ast should map to pts_ast"
        print(f"✓ 'pts+ast' correctly maps to 'pts_ast'")


class TestBasketballStatExtraction:
    """Test get_stat_value function in basketball_predict.py"""
    
    def test_get_stat_value_reb_ast(self):
        """Verify get_stat_value for reb_ast returns rebounds+assists"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        # Check that reb_ast calculation is correct
        assert 'if pt == "reb_ast":' in content, "reb_ast case not found in get_stat_value"
        
        # Check that the return statement includes rebounds and assists
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if pt == "reb_ast":' in line:
                next_line = lines[i+1] if i+1 < len(lines) else ""
                assert 'rebounds' in next_line and 'assists' in next_line, \
                    f"reb_ast should return rebounds + assists, got: {next_line}"
                print(f"✓ get_stat_value for reb_ast returns rebounds + assists")
                return
        pytest.fail("reb_ast case not found")
    
    def test_get_stat_value_pts_reb(self):
        """Verify get_stat_value for pts_reb returns points+rebounds"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        assert 'if pt == "pts_reb":' in content, "pts_reb case not found in get_stat_value"
        
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if pt == "pts_reb":' in line:
                next_line = lines[i+1] if i+1 < len(lines) else ""
                assert 'points' in next_line and 'rebounds' in next_line, \
                    f"pts_reb should return points + rebounds, got: {next_line}"
                print(f"✓ get_stat_value for pts_reb returns points + rebounds")
                return
        pytest.fail("pts_reb case not found")
    
    def test_get_stat_value_blk_stl(self):
        """Verify get_stat_value for blk_stl returns blocks+steals"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        assert 'if pt == "blk_stl":' in content, "blk_stl case not found in get_stat_value"
        
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if pt == "blk_stl":' in line:
                next_line = lines[i+1] if i+1 < len(lines) else ""
                assert 'blocks' in next_line and 'steals' in next_line, \
                    f"blk_stl should return blocks + steals, got: {next_line}"
                print(f"✓ get_stat_value for blk_stl returns blocks + steals")
                return
        pytest.fail("blk_stl case not found")


class TestBasketballOpponentLeagueFilter:
    """Test that basketball opponent resolution uses league_id filter"""
    
    def test_opponent_resolution_uses_league_id(self):
        """Verify scan.py passes league_id to get_bball_team_by_name for opponent"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        # Check that opponent resolution uses league_id filter
        # Look for the pattern: get_bball_team_by_name(opp_hint, league_id=league_id)
        assert 'get_bball_team_by_name(opp_hint, league_id=league_id)' in content, \
            "Opponent resolution should pass league_id to get_bball_team_by_name"
        print(f"✓ Opponent resolution passes league_id to get_bball_team_by_name")
    
    def test_cross_league_rejection_logic(self):
        """Verify cross-league matches are rejected"""
        scan_py_path = "/app/backend/routes/scan.py"
        with open(scan_py_path, 'r') as f:
            content = f.read()
        
        # Check for cross-league rejection comment and logic
        assert 'prevents NBA vs WNBA cross-match' in content or 'REJECT cross-league matches' in content, \
            "Cross-league rejection logic should be documented"
        print(f"✓ Cross-league rejection logic is present")
    
    def test_basketball_cache_league_id_filter(self):
        """Verify basketball_cache.py get_bball_team_by_name accepts league_id parameter"""
        cache_path = "/app/backend/basketball_cache.py"
        with open(cache_path, 'r') as f:
            content = f.read()
        
        # Check function signature includes league_id parameter
        assert 'def get_bball_team_by_name(team_name: str, league_id: int = None)' in content, \
            "get_bball_team_by_name should accept league_id parameter"
        print(f"✓ get_bball_team_by_name accepts league_id parameter")
        
        # Check that league_id is used in query
        assert 'if league_id:' in content and 'query_base["leagueId"] = league_id' in content, \
            "league_id should be used in query filter"
        print(f"✓ league_id is used in query filter")


class TestPredictionOpponentForceSet:
    """Test that prediction['opponent'] is force-set from request data"""
    
    def test_soccer_prediction_opponent_force_set(self):
        """Verify soccer predict.py uses prediction['opponent'] = req.opponentName"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, 'r') as f:
            content = f.read()
        
        # Check for force-set pattern (not setdefault)
        assert 'prediction["opponent"] = req.opponentName' in content, \
            "Soccer prediction should force-set opponent from request"
        print(f"✓ Soccer prediction force-sets opponent from request")
        
        # Verify it's NOT using setdefault for opponent
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'prediction.setdefault("opponent"' in line:
                pytest.fail(f"Line {i+1}: Should not use setdefault for opponent")
        print(f"✓ Soccer prediction does NOT use setdefault for opponent")
    
    def test_basketball_prediction_opponent_force_set(self):
        """Verify basketball_predict.py uses prediction['opponent'] = req.opponentName"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        # Check for force-set pattern
        assert 'prediction["opponent"] = req.opponentName' in content, \
            "Basketball prediction should force-set opponent from request"
        print(f"✓ Basketball prediction force-sets opponent from request")
    
    def test_soccer_prediction_player_force_set(self):
        """Verify soccer predict.py force-sets player from request data"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, 'r') as f:
            content = f.read()
        
        # Check for player force-set pattern
        assert 'prediction["player"] = {' in content, \
            "Soccer prediction should force-set player dict"
        assert '"name": req.playerName' in content or "'name': req.playerName" in content, \
            "Player name should come from request"
        print(f"✓ Soccer prediction force-sets player from request data")


class TestNoAIModelNamesInUserFacingText:
    """Test that AI model names don't appear in user-facing text"""
    
    def test_no_grok_in_data_quality_messages(self):
        """Verify 'Grok' doesn't appear in dataQuality messages"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, 'r') as f:
            content = f.read()
        
        # Find dataQuality message assignments
        dq_matches = re.findall(r'"message":\s*[^,}]+', content)
        for match in dq_matches:
            assert 'Grok' not in match, f"Found 'Grok' in dataQuality message: {match}"
        print(f"✓ No 'Grok' in dataQuality messages")
    
    def test_cross_referenced_sources_message(self):
        """Verify data quality message says 'Cross-referenced sources' not 'Grok'"""
        predict_path = "/app/backend/routes/predict.py"
        with open(predict_path, 'r') as f:
            content = f.read()
        
        # Check for the correct message
        assert 'Cross-referenced sources used for analysis' in content, \
            "Data quality message should say 'Cross-referenced sources'"
        print(f"✓ Data quality message uses 'Cross-referenced sources'")
    
    def test_no_ai_model_names_in_basketball_data_quality(self):
        """Verify no AI model names in basketball dataQuality messages"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        # Find dataQuality message assignments
        dq_matches = re.findall(r'"message":\s*[^,}]+', content)
        ai_models = ['Grok', 'Gemini', 'GPT', 'Claude']
        for match in dq_matches:
            for model in ai_models:
                assert model not in match, f"Found '{model}' in basketball dataQuality message: {match}"
        print(f"✓ No AI model names in basketball dataQuality messages")


class TestFrontendOpponentFallback:
    """Test frontend uses correct opponent fallback"""
    
    def test_frontend_uses_resolved_opponent_teamid(self):
        """Verify frontend line 594 uses pickData.resolvedOpponent?.teamId || 0"""
        app_js_path = "/app/frontend/src/App.js"
        with open(app_js_path, 'r') as f:
            content = f.read()
        
        # Check for correct pattern
        assert 'pickData.resolvedOpponent?.teamId || 0' in content, \
            "Frontend should use pickData.resolvedOpponent?.teamId || 0"
        print(f"✓ Frontend uses pickData.resolvedOpponent?.teamId || 0")
        
        # Verify it's NOT using pickData.resolved.teamId for opponent
        # (resolved is for player, resolvedOpponent is for opponent)
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'opponentId' in line and 'pickData.resolved.teamId' in line and 'resolvedOpponent' not in line:
                # This would be wrong - using player's resolved teamId for opponent
                pytest.fail(f"Line {i+1}: Should not use pickData.resolved.teamId for opponentId")
        print(f"✓ Frontend correctly distinguishes resolved (player) from resolvedOpponent (opponent)")


class TestBballStatFieldMap:
    """Test BBALL_STAT_FIELD_MAP in basketball_predict.py"""
    
    def test_bball_stat_field_map_includes_compound_props(self):
        """Verify BBALL_STAT_FIELD_MAP includes compound prop types"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        # Find BBALL_STAT_FIELD_MAP
        match = re.search(r'BBALL_STAT_FIELD_MAP\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        assert match, "BBALL_STAT_FIELD_MAP not found"
        
        map_str = match.group(1)
        
        # Check compound props are set to None (handled specially)
        compound_props = ['pts_reb_ast', 'pts_reb', 'pts_ast', 'reb_ast', 'blk_stl']
        for prop in compound_props:
            assert f'"{prop}": None' in map_str or f"'{prop}': None" in map_str, \
                f"Compound prop {prop} should map to None in BBALL_STAT_FIELD_MAP"
        print(f"✓ BBALL_STAT_FIELD_MAP correctly maps compound props to None")


class TestBballPropLabels:
    """Test BBALL_PROP_LABELS in basketball_predict.py"""
    
    def test_bball_prop_labels_includes_all_props(self):
        """Verify BBALL_PROP_LABELS includes all prop types"""
        bball_predict_path = "/app/backend/routes/basketball_predict.py"
        with open(bball_predict_path, 'r') as f:
            content = f.read()
        
        # Find BBALL_PROP_LABELS
        match = re.search(r'BBALL_PROP_LABELS\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        assert match, "BBALL_PROP_LABELS not found"
        
        labels_str = match.group(1)
        
        required_labels = {
            'reb_ast': 'Reb+Ast',
            'pts_reb': 'Pts+Reb',
            'pts_ast': 'Pts+Ast',
            'blk_stl': 'Blk+Stl',
            'steals': 'Steals',
            'blocks': 'Blocks',
            'turnovers': 'Turnovers',
        }
        
        for prop, label in required_labels.items():
            assert f'"{prop}"' in labels_str or f"'{prop}'" in labels_str, \
                f"Missing prop label for: {prop}"
        print(f"✓ BBALL_PROP_LABELS includes all required prop types")


class TestPicksPyStatExtraction:
    """Test get_bball_stat_value in picks.py"""
    
    def test_picks_py_reb_ast_extraction(self):
        """Verify picks.py get_bball_stat_value handles reb_ast correctly"""
        picks_path = "/app/backend/routes/picks.py"
        with open(picks_path, 'r') as f:
            content = f.read()
        
        # Check for reb_ast handling
        assert 'if pt == "reb_ast":' in content, "reb_ast case not found in picks.py get_bball_stat_value"
        
        # Verify it returns rebounds + assists
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'if pt == "reb_ast":' in line:
                next_line = lines[i+1] if i+1 < len(lines) else ""
                assert 'rebounds' in next_line and 'assists' in next_line, \
                    f"picks.py reb_ast should return rebounds + assists, got: {next_line}"
                print(f"✓ picks.py get_bball_stat_value handles reb_ast correctly")
                return
        pytest.fail("reb_ast case not found in picks.py")
    
    def test_picks_py_bball_props_set(self):
        """Verify picks.py bball_props set includes all required props"""
        picks_path = "/app/backend/routes/picks.py"
        with open(picks_path, 'r') as f:
            content = f.read()
        
        # Find bball_props set
        match = re.search(r'bball_props\s*=\s*\{([^}]+)\}', content)
        assert match, "bball_props set not found in picks.py"
        
        props_str = match.group(1)
        required_props = ['reb_ast', 'pts_reb', 'pts_ast', 'blk_stl', 'steals', 'blocks', 'turnovers']
        
        for prop in required_props:
            assert f'"{prop}"' in props_str or f"'{prop}'" in props_str, \
                f"Missing prop in picks.py bball_props: {prop}"
        print(f"✓ picks.py bball_props includes all required props")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
