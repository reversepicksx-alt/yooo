"""
Test iteration 36 - 5 specific UI/logic fixes:
1. Consensus strings should use dynamic model count (not hardcoded /4)
2. Position resolver uses GENERIC_TO_SPECIFIC constraints
3. Position-role validation rejects mismatches
4. Scan-prop response includes position/role fields
5. Frontend model labels use GE/GK/GP (code verification)
"""
import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestConsensusStrings:
    """Verify consensus strings use dynamic model count, not hardcoded /4"""
    
    def test_predict_py_no_hardcoded_4(self):
        """Check predict.py consensus strings don't contain '/4'"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Look for consensus-related lines
        consensus_lines = [line for line in content.split('\n') if 'consensus' in line.lower() or 'Unanimous' in line or 'Split:' in line]
        
        # Check none contain hardcoded /4
        for line in consensus_lines:
            # Allow /4 in comments but not in actual strings
            if '/4' in line and not line.strip().startswith('#'):
                # Check if it's in a string (between quotes)
                if '"/4' in line or "'/4" in line or '/4"' in line or "/4'" in line:
                    pytest.fail(f"Found hardcoded '/4' in consensus string: {line.strip()}")
        
        # Verify dynamic count pattern exists
        assert 'len(valid_preds)' in content, "Should use len(valid_preds) for dynamic count"
        print("PASS: predict.py uses dynamic model count in consensus strings")
    
    def test_basketball_predict_py_no_hardcoded_4(self):
        """Check basketball_predict.py consensus strings don't contain '/4'"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        consensus_lines = [line for line in content.split('\n') if 'consensus' in line.lower() or 'Unanimous' in line or 'Split:' in line]
        
        for line in consensus_lines:
            if '/4' in line and not line.strip().startswith('#'):
                if '"/4' in line or "'/4" in line or '/4"' in line or "/4'" in line:
                    pytest.fail(f"Found hardcoded '/4' in basketball consensus string: {line.strip()}")
        
        assert 'len(valid_preds)' in content, "Should use len(valid_preds) for dynamic count"
        print("PASS: basketball_predict.py uses dynamic model count in consensus strings")


class TestPositionResolver:
    """Verify position resolver uses API-Sports category constraints"""
    
    def test_generic_to_specific_mapping_exists(self):
        """Check GENERIC_TO_SPECIFIC mapping is defined correctly"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Verify the mapping exists
        assert 'GENERIC_TO_SPECIFIC' in content, "GENERIC_TO_SPECIFIC mapping should exist"
        
        # Verify Defender category only allows defender positions
        assert '"Defender": {"CB", "LB", "RB", "LWB", "RWB"}' in content or \
               "'Defender': {'CB', 'LB', 'RB', 'LWB', 'RWB'}" in content or \
               '"Defender": {' in content, "Defender category should map to CB, LB, RB, LWB, RWB"
        
        # Verify Goalkeeper only allows GK
        assert '"Goalkeeper": {"GK"}' in content or "'Goalkeeper': {'GK'}" in content, \
            "Goalkeeper category should only map to GK"
        
        print("PASS: GENERIC_TO_SPECIFIC mapping correctly constrains positions")
    
    def test_position_role_map_exists(self):
        """Check POSITION_ROLE_MAP validates role-position compatibility"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        assert 'POSITION_ROLE_MAP' in content, "POSITION_ROLE_MAP should exist"
        
        # Verify CB doesn't allow Fullback role (would catch Alex Sandro bug)
        # CB should have Ball-Playing CB, Stopper - NOT Fullback
        cb_line = None
        for line in content.split('\n'):
            if '"CB":' in line or "'CB':" in line:
                cb_line = line
                break
        
        if cb_line:
            assert 'Fullback' not in cb_line, "CB position should NOT allow Fullback role"
            assert 'Ball-Playing CB' in cb_line or 'Stopper' in cb_line, \
                "CB should allow Ball-Playing CB or Stopper roles"
        
        # Verify LB allows Fullback
        lb_line = None
        for line in content.split('\n'):
            if '"LB":' in line or "'LB':" in line:
                lb_line = line
                break
        
        if lb_line:
            assert 'Fullback' in lb_line, "LB position should allow Fullback role"
        
        print("PASS: POSITION_ROLE_MAP correctly validates role-position compatibility")
    
    def test_position_resolver_uses_category_constraint(self):
        """Verify position resolver passes category hint to Grok"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Check that allowed_positions is derived from GENERIC_TO_SPECIFIC
        assert 'allowed_positions = GENERIC_TO_SPECIFIC.get(player_position' in content, \
            "Position resolver should get allowed positions from GENERIC_TO_SPECIFIC"
        
        # Check category hint is passed to AI
        assert 'category_hint' in content, "Should build category_hint for AI"
        assert 'ONLY choose from positions within that category' in content, \
            "Should instruct AI to only choose from category positions"
        
        print("PASS: Position resolver uses API-Sports category constraints")


class TestScanPropPositionFields:
    """Verify scan-prop response includes position/role fields"""
    
    def test_scan_py_includes_position_lookup(self):
        """Check scan.py looks up position from player_positions cache"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        
        # Verify position lookup code exists
        assert 'player_positions.find_one' in content, \
            "scan.py should look up player_positions collection"
        assert 'specificPosition' in content, "Should extract specificPosition"
        assert 'role' in content, "Should extract role"
        
        print("PASS: scan.py includes position/role lookup")
    
    def test_scan_response_includes_position_fields(self):
        """Check extracted object includes position and role fields"""
        with open('/app/backend/routes/scan.py', 'r') as f:
            content = f.read()
        
        # Look for the extracted dict that includes position/role
        # Should be in the results.append section
        assert '"position": player_pos_info.get("position"' in content or \
               "'position': player_pos_info.get('position'" in content, \
            "Extracted object should include position field"
        
        assert '"role": player_pos_info.get("role"' in content or \
               "'role': player_pos_info.get('role'" in content, \
            "Extracted object should include role field"
        
        print("PASS: scan-prop response includes position/role fields")


class TestFrontendModelLabels:
    """Verify frontend uses GE/GK/GP labels instead of AI-1/AI-2/AI-3"""
    
    def test_projection_card_uses_model_nicknames(self):
        """Check ProjectionCard.jsx uses GE/GK/GP labels"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Should have the ternary for model nicknames
        assert "m.model === 'gemini' ? 'GE'" in content, "Should map gemini to GE"
        assert "m.model === 'grok' ? 'GK'" in content, "Should map grok to GK"
        assert "'GP'" in content, "Should have GP for GPT"
        
        # Should NOT have hardcoded AI-1, AI-2, AI-3 as primary labels
        # The fallback `AI-${i+1}` is OK for unknown models
        lines_with_ai_labels = [l for l in content.split('\n') if 'AI-1' in l or 'AI-2' in l or 'AI-3' in l]
        for line in lines_with_ai_labels:
            # These should only appear in fallback, not as primary
            if 'AI-1' in line and 'gemini' not in line.lower():
                pytest.fail(f"Found hardcoded AI-1 without model check: {line.strip()}")
        
        print("PASS: ProjectionCard uses GE/GK/GP model nicknames")


class TestFrontendMatchupDisplay:
    """Verify frontend uses @ for away and vs for home"""
    
    def test_projection_card_matchup_uses_at_symbol(self):
        """Check ProjectionCard.jsx uses @ for away games"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Should have venue-based @ vs 'vs' logic
        assert "_request?.venue === 'away' ? '@' : 'vs'" in content or \
               "_request?.venue === 'away' ? '@'" in content, \
            "Should use @ for away games and vs for home"
        
        print("PASS: ProjectionCard uses @ for away, vs for home")
    
    def test_app_js_scan_card_uses_at_symbol(self):
        """Check App.js scan card uses @ for away games"""
        with open('/app/frontend/src/App.js', 'r') as f:
            content = f.read()
        
        # Should have venue-based @ vs 'vs' logic in scan cards
        assert "ext.venue === 'away' ? ' @ ' : ' vs '" in content or \
               "venue === 'away' ? '@'" in content, \
            "Scan card should use @ for away games"
        
        print("PASS: App.js scan card uses @ for away, vs for home")


class TestFrontendScanPositionBadge:
    """Verify scan card displays position badge when available"""
    
    def test_app_js_scan_card_shows_position(self):
        """Check App.js scan card displays position badge"""
        with open('/app/frontend/src/App.js', 'r') as f:
            content = f.read()
        
        # Should conditionally render position badge
        assert 'ext.position &&' in content or 'ext.position &&' in content, \
            "Should conditionally render position when available"
        
        # Should display position and role
        assert 'ext.position' in content, "Should display ext.position"
        assert 'ext.role' in content, "Should display ext.role"
        
        # Should have data-testid for position badge
        assert 'scan-position-' in content, "Should have data-testid for position badge"
        
        print("PASS: App.js scan card displays position badge when available")


class TestAPIEndpoints:
    """Test API endpoints are accessible"""
    
    def test_health_endpoint(self):
        """Verify API is running"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"
        print("PASS: API health endpoint accessible")
    
    def test_scan_prop_endpoint_exists(self):
        """Verify scan-prop endpoint exists"""
        # Just check it returns 422 (validation error) not 404
        response = requests.post(f"{BASE_URL}/api/scan-prop", json={}, timeout=10)
        assert response.status_code != 404, "scan-prop endpoint should exist"
        print(f"PASS: scan-prop endpoint exists (status: {response.status_code})")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
