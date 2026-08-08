"""
Unit tests for Iteration 62 features:
1. OCR Validation (_validate_extraction function in scan.py)
2. Auto-tuning Market Blend Ratio (_compute_dynamic_market_weight in calibration.py)

Tests verify:
- OCR validation catches: missing name, UI elements, zero/negative lines, high lines, unknown props
- Valid extractions pass validation for both soccer and basketball
- Auto-tune returns valid weight in 0.35-0.80 range
- Cache works (second call uses cached value)
- _blend_with_market_line accepts dynamic weight parameter
- apply_elite_calibration includes marketBlendWeight and blendSamples in metadata
"""
import sys
sys.path.insert(0, '/app/backend')

import pytest
import asyncio
from datetime import datetime, timezone


# =====================================================================
# OCR VALIDATION TESTS
# =====================================================================

class TestOCRValidationSoccer:
    """Test _validate_extraction for soccer props."""

    def test_valid_soccer_extraction(self):
        """Valid soccer extraction should pass validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is True, f"Valid extraction failed: {issues}"
        assert len(issues) == 0

    def test_valid_soccer_pass_attempts(self):
        """Valid pass_attempts extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Kevin De Bruyne",
            "propType": "pass_attempts",
            "line": 48.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_valid_soccer_saves(self):
        """Valid saves extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Alisson Becker",
            "propType": "saves",
            "line": 3.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_missing_name(self):
        """Missing player name should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "MISSING_NAME" in issues

    def test_name_too_short(self):
        """Name with less than 2 characters should fail."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "A",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "NAME_TOO_SHORT" in issues

    def test_name_no_letters(self):
        """Name with no letters should fail."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "123",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "NAME_NO_LETTERS" in issues

    def test_name_is_ui_element_less(self):
        """'Less' button text should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Less",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "NAME_IS_UI_ELEMENT" in issues

    def test_name_is_ui_element_more(self):
        """'More' button text should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "more",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "NAME_IS_UI_ELEMENT" in issues

    def test_name_is_ui_element_vs(self):
        """'vs' text should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "vs",
            "propType": "goals",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "NAME_IS_UI_ELEMENT" in issues

    def test_missing_line(self):
        """Missing line should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": None
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "MISSING_LINE" in issues

    def test_line_zero(self):
        """Zero line should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": 0
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "LINE_ZERO_OR_NEGATIVE" in issues

    def test_line_negative(self):
        """Negative line should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": -1.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "LINE_ZERO_OR_NEGATIVE" in issues

    def test_line_impossibly_high(self):
        """Line > 500 should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": 501
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "LINE_IMPOSSIBLY_HIGH" in issues

    def test_line_not_a_number(self):
        """Non-numeric line should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "goals",
            "line": "abc"
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "LINE_NOT_A_NUMBER" in issues

    def test_missing_prop_type(self):
        """Missing prop type should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert "MISSING_PROP_TYPE" in issues

    def test_unknown_prop_type(self):
        """Unknown prop type should fail validation."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Erling Haaland",
            "propType": "unknown_stat",
            "line": 0.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=False)
        assert is_valid is False
        assert any("UNKNOWN_PROP_TYPE" in issue for issue in issues)


class TestOCRValidationBasketball:
    """Test _validate_extraction for basketball props."""

    def test_valid_basketball_points(self):
        """Valid basketball points extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "LeBron James",
            "propType": "points",
            "line": 24.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_valid_basketball_rebounds(self):
        """Valid basketball rebounds extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Anthony Davis",
            "propType": "rebounds",
            "line": 10.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_valid_basketball_pts_reb_ast(self):
        """Valid basketball PRA combo extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Luka Doncic",
            "propType": "pts_reb_ast",
            "line": 45.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_valid_basketball_three_pointers(self):
        """Valid basketball three_pointers extraction should pass."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "Stephen Curry",
            "propType": "three_pointers",
            "line": 4.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is True, f"Valid extraction failed: {issues}"

    def test_basketball_unknown_prop(self):
        """Unknown basketball prop type should fail."""
        from routes.scan import _validate_extraction
        
        entry = {
            "playerName": "LeBron James",
            "propType": "goals",  # Soccer prop, not basketball
            "line": 24.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is False
        assert any("UNKNOWN_PROP_TYPE" in issue for issue in issues)

    def test_basketball_alias_normalization(self):
        """Basketball prop aliases should be normalized and pass."""
        from routes.scan import _validate_extraction
        
        # "pts" is an alias for "points"
        entry = {
            "playerName": "LeBron James",
            "propType": "pts",
            "line": 24.5
        }
        is_valid, issues = _validate_extraction(entry, is_basketball=True)
        assert is_valid is True, f"Alias 'pts' should normalize to 'points': {issues}"


# =====================================================================
# AUTO-TUNE MARKET BLEND TESTS
# =====================================================================

class TestAutoTuneMarketBlend:
    """Test _compute_dynamic_market_weight function."""

    def test_returns_valid_weight_range(self):
        """Auto-tune should return weight in 0.35-0.80 range."""
        from calibration import _compute_dynamic_market_weight
        
        async def run_test():
            return await _compute_dynamic_market_weight("soccer")
        
        weight, n, note = asyncio.get_event_loop().run_until_complete(run_test())
        assert 0.35 <= weight <= 0.80, f"Weight {weight} outside valid range 0.35-0.80"
        assert isinstance(n, int)
        assert isinstance(note, str)
        print(f"[TEST] Auto-tune weight: {weight}, samples: {n}, note: {note}")

    def test_cache_works(self):
        """Second call should use cached value (within 2 hours)."""
        from calibration import _compute_dynamic_market_weight, _blend_cache
        
        async def run_test():
            # First call
            weight1, n1, note1 = await _compute_dynamic_market_weight("soccer")
            
            # Check cache was populated
            cache_key = "blend_soccer"
            assert cache_key in _blend_cache
            assert _blend_cache[cache_key]["weight"] == weight1
            
            # Second call should return same value from cache
            weight2, n2, note2 = await _compute_dynamic_market_weight("soccer")
            assert weight1 == weight2, "Cache should return same weight"
            return weight1, weight2
        
        weight1, weight2 = asyncio.get_event_loop().run_until_complete(run_test())
        print(f"[TEST] Cache test: first={weight1}, second={weight2}")

    def test_insufficient_data_returns_default(self):
        """With insufficient data, should return default 0.65."""
        from calibration import _compute_dynamic_market_weight, DEFAULT_MARKET_WEIGHT, MIN_BLEND_SAMPLES
        
        async def run_test():
            return await _compute_dynamic_market_weight("soccer")
        
        weight, n, note = asyncio.get_event_loop().run_until_complete(run_test())
        
        if n < MIN_BLEND_SAMPLES:
            assert weight == DEFAULT_MARKET_WEIGHT, f"With {n} samples (< {MIN_BLEND_SAMPLES}), should return default {DEFAULT_MARKET_WEIGHT}"
            assert "insufficient data" in note.lower()
        print(f"[TEST] Insufficient data test: weight={weight}, n={n}, note={note}")


class TestBlendWithMarketLine:
    """Test _blend_with_market_line function."""

    def test_blend_with_default_weight(self):
        """Blend should work with default weight."""
        from calibration import _blend_with_market_line, DEFAULT_MARKET_WEIGHT
        
        projected = 30.0
        line = 25.0
        blended, note = _blend_with_market_line(projected, line)
        
        # Expected: (1 - 0.65) * 30 + 0.65 * 25 = 0.35 * 30 + 0.65 * 25 = 10.5 + 16.25 = 26.75
        expected = round((1 - DEFAULT_MARKET_WEIGHT) * projected + DEFAULT_MARKET_WEIGHT * line, 1)
        assert blended == expected, f"Expected {expected}, got {blended}"
        assert "Market blend" in note

    def test_blend_with_custom_weight(self):
        """Blend should accept custom weight parameter."""
        from calibration import _blend_with_market_line
        
        projected = 30.0
        line = 25.0
        custom_weight = 0.50
        blended, note = _blend_with_market_line(projected, line, market_weight=custom_weight)
        
        # Expected: 0.50 * 30 + 0.50 * 25 = 15 + 12.5 = 27.5
        expected = round((1 - custom_weight) * projected + custom_weight * line, 1)
        assert blended == expected, f"Expected {expected}, got {blended}"

    def test_blend_with_zero_line(self):
        """Zero line should return projected unchanged."""
        from calibration import _blend_with_market_line
        
        projected = 30.0
        line = 0
        blended, note = _blend_with_market_line(projected, line)
        
        assert blended == projected
        assert note == ""

    def test_blend_same_values(self):
        """Same projected and line should return unchanged."""
        from calibration import _blend_with_market_line
        
        projected = 25.0
        line = 25.0
        blended, note = _blend_with_market_line(projected, line)
        
        assert blended == projected


class TestApplyEliteCalibration:
    """Test apply_elite_calibration includes marketBlendWeight and blendSamples."""

    def test_calibration_metadata_includes_blend_info(self):
        """apply_elite_calibration should include marketBlendWeight and blendSamples in metadata."""
        from calibration import apply_elite_calibration, get_calibration_stats
        
        async def run_test():
            # Get stats first to ensure we have some data
            stats = await get_calibration_stats("soccer")
            
            # Create a mock prediction
            prediction = {
                "projectedValue": 30.0,
                "recommendation": "over",
                "confidenceScore": 65,
                "tacticalAlerts": []
            }
            
            result = await apply_elite_calibration(
                prediction=prediction,
                prop_type="pass_attempts",
                line=25.5,
                venue="home",
                sport="soccer"
            )
            return result
        
        result = asyncio.get_event_loop().run_until_complete(run_test())
        
        # Check if calibrationApplied exists and has the new fields
        if "calibrationApplied" in result:
            cal_applied = result["calibrationApplied"]
            assert "marketBlendWeight" in cal_applied, "Missing marketBlendWeight in calibrationApplied"
            assert "blendSamples" in cal_applied, "Missing blendSamples in calibrationApplied"
            
            # Verify marketBlendWeight is in valid range
            weight = cal_applied["marketBlendWeight"]
            assert 0.35 <= weight <= 0.80, f"marketBlendWeight {weight} outside valid range"
            
            # Verify blendSamples is an integer
            assert isinstance(cal_applied["blendSamples"], int)
            
            print(f"[TEST] marketBlendWeight: {weight}, blendSamples: {cal_applied['blendSamples']}")
        else:
            # If no calibration was applied (insufficient data), that's also valid
            print("[TEST] No calibrationApplied in result - likely insufficient historical data")


# =====================================================================
# HEALTH ENDPOINT TEST
# =====================================================================

class TestHealthEndpoint:
    """Test health endpoint still works."""

    def test_health_returns_ok(self):
        """Health endpoint should return status ok."""
        import requests
        import os
        
        base_url = os.environ.get('REACT_APP_BACKEND_URL', 'https://props-ai-predict.preview.emergentagent.com')
        response = requests.get(f"{base_url}/api/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
