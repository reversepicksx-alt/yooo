"""
Iteration 52 Tests: GPT-5.2 Upgrade + Auto-Analyze Removal + Calibration Engine
Tests:
1. Backend health check
2. Verify auto_analyze_miss_background import removed from picks.py
3. Verify GPT-5.2 model string in predict.py, basketball_predict.py, miss_analysis.py
4. Verify calibration engine loads and generates prompts
5. Verify basketball prediction pipeline includes calibration context
6. Verify POST /api/picks/correct no longer triggers auto-analyze
7. Verify POST /api/picks/live-update no longer triggers auto-analyze for settled misses
"""
import pytest
import requests
import os
import ast
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthCheck:
    """Basic health check to ensure backend is running"""
    
    def test_health_endpoint(self):
        """Test /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"Health check passed: {data}")


class TestAutoAnalyzeRemoval:
    """Verify auto_analyze_miss_background triggers have been removed"""
    
    def test_picks_py_no_auto_analyze_import(self):
        """Verify auto_analyze_miss_background is NOT imported in picks.py"""
        with open('/app/backend/routes/picks.py', 'r') as f:
            content = f.read()
        
        # Check that the import is commented out or removed
        # The comment should exist but not an actual import
        assert "from routes.miss_analysis import auto_analyze_miss_background" not in content, \
            "auto_analyze_miss_background should NOT be imported in picks.py"
        
        # Verify the comment exists explaining removal
        assert "auto_analyze_miss_background REMOVED" in content, \
            "Comment explaining removal should exist"
        print("PASS: auto_analyze_miss_background import removed from picks.py")
    
    def test_picks_py_no_auto_analyze_calls(self):
        """Verify no calls to auto_analyze_miss_background in picks.py"""
        with open('/app/backend/routes/picks.py', 'r') as f:
            content = f.read()
        
        # Check that there are no actual function calls (not in comments)
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith('#'):
                continue
            # Check for function call
            if 'auto_analyze_miss_background(' in stripped:
                pytest.fail(f"Found auto_analyze_miss_background call at line {i}: {stripped}")
        
        print("PASS: No auto_analyze_miss_background calls in picks.py")
    
    def test_miss_analysis_auto_trigger_removed(self):
        """Verify auto-trigger loop removed from miss_analysis.py get_misses endpoint"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        
        # The comment should indicate removal
        assert "no auto-trigger" in content.lower() or "removed to save AI tokens" in content.lower(), \
            "Comment about auto-trigger removal should exist"
        
        # The function should still exist (for manual calls)
        assert "async def auto_analyze_miss_background" in content, \
            "auto_analyze_miss_background function should still exist for manual API calls"
        
        print("PASS: Auto-trigger removed from miss_analysis.py, function still exists for manual use")


class TestGPT52ModelUpgrade:
    """Verify GPT-5.2 model string is used everywhere gpt-4.1-mini was"""
    
    def test_predict_py_uses_gpt52(self):
        """Verify predict.py uses gpt-5.2 model"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Should have gpt-5.2
        assert 'gpt-5.2' in content, "predict.py should use gpt-5.2 model"
        
        # Should NOT have gpt-4.1-mini
        assert 'gpt-4.1-mini' not in content, "predict.py should NOT use gpt-4.1-mini"
        
        # Count occurrences
        gpt52_count = content.count('gpt-5.2')
        print(f"PASS: predict.py uses gpt-5.2 ({gpt52_count} occurrences)")
    
    def test_basketball_predict_py_uses_gpt52(self):
        """Verify basketball_predict.py uses gpt-5.2 model"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Should have gpt-5.2
        assert 'gpt-5.2' in content, "basketball_predict.py should use gpt-5.2 model"
        
        # Should NOT have gpt-4.1-mini
        assert 'gpt-4.1-mini' not in content, "basketball_predict.py should NOT use gpt-4.1-mini"
        
        gpt52_count = content.count('gpt-5.2')
        print(f"PASS: basketball_predict.py uses gpt-5.2 ({gpt52_count} occurrences)")
    
    def test_miss_analysis_py_uses_gpt52(self):
        """Verify miss_analysis.py uses gpt-5.2 model"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        
        # Should have gpt-5.2
        assert 'gpt-5.2' in content, "miss_analysis.py should use gpt-5.2 model"
        
        # Should NOT have gpt-4.1-mini
        assert 'gpt-4.1-mini' not in content, "miss_analysis.py should NOT use gpt-4.1-mini"
        
        gpt52_count = content.count('gpt-5.2')
        print(f"PASS: miss_analysis.py uses gpt-5.2 ({gpt52_count} occurrences)")


class TestCalibrationEngine:
    """Verify calibration engine is properly implemented"""
    
    def test_calibration_module_exists(self):
        """Verify calibration.py exists and has required functions"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        # Check for required functions
        assert 'async def get_calibration_stats' in content, \
            "get_calibration_stats function should exist"
        assert 'def generate_calibration_prompt' in content, \
            "generate_calibration_prompt function should exist"
        assert 'async def apply_calibration_guards' in content, \
            "apply_calibration_guards function should exist"
        
        print("PASS: calibration.py has all required functions")
    
    def test_calibration_has_granular_tracking(self):
        """Verify calibration tracks position, game context, and league"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        # Check for granular tracking fields
        assert 'by_position' in content, "Should track by position"
        assert 'by_game_context' in content, "Should track by game context"
        assert 'by_league' in content, "Should track by league"
        assert 'by_prop_venue' in content, "Should track by prop+venue"
        assert 'by_prop_position' in content, "Should track by prop+position"
        assert 'by_prop_context' in content, "Should track by prop+context"
        
        print("PASS: calibration.py has granular tracking (position, context, league)")
    
    def test_calibration_position_inference(self):
        """Verify calibration infers position from prop type"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        # Check for position inference
        assert 'def _infer_position' in content, "Should have position inference function"
        assert 'SOCCER_GK_PROPS' in content, "Should have soccer GK props"
        assert 'SOCCER_DEF_PROPS' in content, "Should have soccer defender props"
        assert 'BBALL_BIG_PROPS' in content, "Should have basketball big props"
        assert 'BBALL_GUARD_PROPS' in content, "Should have basketball guard props"
        
        print("PASS: calibration.py has position inference from prop types")
    
    def test_calibration_game_context_classification(self):
        """Verify calibration classifies game context (blowout/close/normal)"""
        with open('/app/backend/calibration.py', 'r') as f:
            content = f.read()
        
        assert 'def _game_context' in content, "Should have game context function"
        assert 'blowout' in content, "Should classify blowout games"
        assert 'close' in content, "Should classify close games"
        assert 'normal' in content, "Should classify normal games"
        
        print("PASS: calibration.py classifies game context")


class TestBasketballCalibrationIntegration:
    """Verify basketball prediction pipeline includes calibration"""
    
    def test_basketball_predict_imports_calibration(self):
        """Verify basketball_predict.py imports calibration functions"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Check for calibration imports (may be inline)
        assert 'from calibration import' in content or 'calibration import' in content, \
            "basketball_predict.py should import from calibration"
        
        # Check for get_calibration_stats usage
        assert 'get_calibration_stats' in content, \
            "basketball_predict.py should use get_calibration_stats"
        
        # Check for generate_calibration_prompt usage
        assert 'generate_calibration_prompt' in content, \
            "basketball_predict.py should use generate_calibration_prompt"
        
        print("PASS: basketball_predict.py imports and uses calibration functions")
    
    def test_basketball_predict_injects_calibration_context(self):
        """Verify calibration_context is injected into basketball prediction prompt"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Check for calibration_context variable
        assert 'calibration_context' in content, \
            "basketball_predict.py should have calibration_context variable"
        
        # Check that it's used in the prompt
        assert '{calibration_context}' in content, \
            "calibration_context should be injected into the prompt"
        
        print("PASS: basketball_predict.py injects calibration_context into prompt")
    
    def test_basketball_predict_applies_calibration_guards(self):
        """Verify basketball_predict.py applies calibration guards"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        assert 'apply_calibration_guards' in content, \
            "basketball_predict.py should use apply_calibration_guards"
        
        print("PASS: basketball_predict.py applies calibration guards")


class TestFrontendGPT52Label:
    """Verify frontend handles gpt52 source label correctly"""
    
    def test_projection_card_handles_gpt52(self):
        """Verify ProjectionCard.jsx handles gpt52 model label"""
        with open('/app/frontend/src/components/app/ProjectionCard.jsx', 'r') as f:
            content = f.read()
        
        # Check that gpt52 is handled in model display
        assert 'gpt52' in content, "ProjectionCard should handle gpt52 label"
        
        # Check the specific mapping logic
        assert "m.model === 'gpt52'" in content, \
            "ProjectionCard should check for gpt52 model"
        
        # Verify it maps to 'GP' display
        assert "'GP'" in content, "gpt52 should map to GP display label"
        
        print("PASS: ProjectionCard.jsx handles gpt52 source label correctly")


class TestPicksEndpointsNoAutoAnalyze:
    """Verify picks endpoints don't trigger auto-analyze"""
    
    def test_correct_endpoint_no_auto_analyze(self):
        """Verify POST /api/picks/correct doesn't call auto_analyze_miss_background"""
        with open('/app/backend/routes/picks.py', 'r') as f:
            content = f.read()
        
        # Find the correct_pick function
        match = re.search(r'async def correct_pick\(.*?\):(.*?)(?=\n(?:async def|@router|class |$))', 
                         content, re.DOTALL)
        if match:
            func_body = match.group(1)
            assert 'auto_analyze_miss_background' not in func_body, \
                "correct_pick should not call auto_analyze_miss_background"
            print("PASS: correct_pick endpoint doesn't trigger auto-analyze")
        else:
            # Function exists but regex didn't match - check manually
            assert 'auto_analyze_miss_background' not in content.split('async def correct_pick')[1].split('async def')[0], \
                "correct_pick should not call auto_analyze_miss_background"
            print("PASS: correct_pick endpoint doesn't trigger auto-analyze")
    
    def test_live_update_endpoint_no_auto_analyze(self):
        """Verify POST /api/picks/live-update doesn't call auto_analyze_miss_background"""
        with open('/app/backend/routes/picks.py', 'r') as f:
            content = f.read()
        
        # The entire file should not have any active calls to auto_analyze_miss_background
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'auto_analyze_miss_background(' in stripped:
                pytest.fail(f"Found auto_analyze_miss_background call at line {i}")
        
        print("PASS: live_update endpoint doesn't trigger auto-analyze")


class TestCalibrationAPIEndpoint:
    """Test calibration API endpoint"""
    
    def test_calibration_insights_endpoint_exists(self):
        """Verify /api/calibration/insights endpoint exists"""
        with open('/app/backend/routes/miss_analysis.py', 'r') as f:
            content = f.read()
        
        assert '/calibration/insights' in content, \
            "calibration/insights endpoint should exist"
        
        print("PASS: /api/calibration/insights endpoint exists")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
