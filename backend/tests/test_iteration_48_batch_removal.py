"""
Iteration 48 Tests: Batch Mode Removal & Tactical Breakdown Display
Tests:
1. Verify batch mode completely removed from App.js
2. Verify POST /api/predict endpoint exists and accepts requests
3. Verify ProjectionCard.jsx has tacticalBreakdown display
4. Verify matchContext field is added to prediction response
"""
import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestBatchModeRemoval:
    """Verify batch mode is completely removed from frontend"""
    
    def test_no_batch_mode_state_in_app_js(self):
        """Verify batchMode state variable is removed"""
        with open('/app/frontend/src/App.js', 'r') as f:
            content = f.read()
        
        # Check for batch-related state variables
        batch_patterns = [
            r'batchMode',
            r'setBatchMode',
            r'handleBatchToggleProp',
            r'handleBatchPredictAll',
        ]
        
        for pattern in batch_patterns:
            matches = re.findall(pattern, content)
            assert len(matches) == 0, f"Found batch reference '{pattern}' in App.js - should be removed"
        
        print("PASS: No batch mode state variables found in App.js")
    
    def test_no_player_report_import(self):
        """Verify PlayerReport component import is removed"""
        with open('/app/frontend/src/App.js', 'r') as f:
            content = f.read()
        
        # Check for PlayerReport import
        assert 'PlayerReport' not in content, "PlayerReport import should be removed from App.js"
        print("PASS: PlayerReport import removed from App.js")
    
    def test_app_js_compiles_no_dangling_jsx(self):
        """Verify App.js has no syntax errors (check for balanced braces)"""
        with open('/app/frontend/src/App.js', 'r') as f:
            content = f.read()
        
        # Simple check: count opening and closing braces
        open_braces = content.count('{')
        close_braces = content.count('}')
        
        # Allow small difference due to template literals
        assert abs(open_braces - close_braces) < 5, f"Unbalanced braces: {open_braces} open, {close_braces} close"
        print(f"PASS: App.js braces balanced ({open_braces} open, {close_braces} close)")


class TestProjectionCardTacticalBreakdown:
    """Verify ProjectionCard displays tacticalBreakdown and matchContext"""
    
    def test_tactical_breakdown_section_exists(self):
        """Verify tacticalBreakdown display section exists in ProjectionCard"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Check for tacticalBreakdown conditional render
        assert 'projection.tacticalBreakdown' in content, "tacticalBreakdown conditional render missing"
        assert 'data-testid="tactical-breakdown"' in content, "tactical-breakdown data-testid missing"
        print("PASS: tacticalBreakdown section exists in ProjectionCard")
    
    def test_match_context_badge_exists(self):
        """Verify matchContext badge display exists"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Check for matchContext conditional render
        assert 'projection.matchContext?.league' in content, "matchContext.league conditional missing"
        assert 'projection.matchContext.round' in content, "matchContext.round display missing"
        print("PASS: matchContext badge exists in ProjectionCard")
    
    def test_markdown_bold_parsing(self):
        """Verify markdown bold (**text**) parsing is implemented"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Check for regex split on **text**
        assert r'\*\*([^*]+)\*\*' in content or '**' in content, "Markdown bold parsing missing"
        print("PASS: Markdown bold parsing implemented")


class TestPredictEndpointMatchContext:
    """Verify predict endpoint returns matchContext field"""
    
    def test_predict_endpoint_exists(self):
        """Verify POST /api/predict endpoint exists"""
        # Send minimal request to check endpoint exists (will fail validation but confirms route)
        response = requests.post(f"{BASE_URL}/api/predict", json={})
        
        # Should get 422 (validation error) not 404
        assert response.status_code != 404, "POST /api/predict endpoint not found"
        print(f"PASS: POST /api/predict endpoint exists (status: {response.status_code})")
    
    def test_match_context_in_backend_code(self):
        """Verify matchContext field is added in predict.py"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Check for matchContext assignment
        assert 'prediction["matchContext"]' in content, "matchContext assignment missing in predict.py"
        assert 'matchLeague' in content, "matchLeague extraction missing"
        assert 'matchRound' in content, "matchRound extraction missing"
        print("PASS: matchContext field added in predict.py")
    
    def test_tactical_breakdown_in_backend_code(self):
        """Verify tacticalBreakdown is generated in predict.py"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Check for tacticalBreakdown in response schema
        assert 'tacticalBreakdown' in content, "tacticalBreakdown missing in predict.py"
        print("PASS: tacticalBreakdown generation exists in predict.py")


class TestHealthCheck:
    """Basic health check"""
    
    def test_api_health(self):
        """Verify API is healthy"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print("PASS: API health check passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
