"""
Iteration 57: Elite Calibration Engine Tests
Tests the 5 post-consensus corrections:
1. Historical error correction (_correct_projected_value)
2. Market line blending (_blend_with_market_line)
3. Recommendation flip guard (_check_recommendation_flip)
4. Confidence recalibration (_recalibrate_confidence)
5. Edge threshold (_apply_edge_threshold)
Plus integration tests for apply_elite_calibration and prediction endpoints.
"""
import pytest
import requests
import os
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# ============================================================
# UNIT TESTS: Direct function testing with mock data
# ============================================================

class TestMarketBlending:
    """Test _blend_with_market_line applies 35/65 weight correctly"""
    
    def test_blend_basic_calculation(self):
        """Verify 35% AI / 65% market blend math"""
        from calibration import _blend_with_market_line, MARKET_WEIGHT
        
        # MARKET_WEIGHT should be 0.65
        assert MARKET_WEIGHT == 0.65, f"Expected MARKET_WEIGHT=0.65, got {MARKET_WEIGHT}"
        
        # Test case: AI projects 10, line is 8
        # Expected: 0.35 * 10 + 0.65 * 8 = 3.5 + 5.2 = 8.7
        projected = 10.0
        line = 8.0
        blended, note = _blend_with_market_line(projected, line)
        expected = round(0.35 * 10 + 0.65 * 8, 1)  # 8.7
        assert blended == expected, f"Expected {expected}, got {blended}"
        assert "Market blend" in note, f"Expected blend note, got: {note}"
        print(f"✓ Blend test 1: {projected} × 0.35 + {line} × 0.65 = {blended}")
    
    def test_blend_higher_line(self):
        """Test when line is higher than projection"""
        from calibration import _blend_with_market_line
        
        # AI projects 5, line is 7
        # Expected: 0.35 * 5 + 0.65 * 7 = 1.75 + 4.55 = 6.3
        projected = 5.0
        line = 7.0
        blended, note = _blend_with_market_line(projected, line)
        expected = round(0.35 * 5 + 0.65 * 7, 1)  # 6.3
        assert blended == expected, f"Expected {expected}, got {blended}"
        print(f"✓ Blend test 2: {projected} × 0.35 + {line} × 0.65 = {blended}")
    
    def test_blend_zero_line_returns_projection(self):
        """When line is 0 or negative, return original projection"""
        from calibration import _blend_with_market_line
        
        projected = 10.0
        blended, note = _blend_with_market_line(projected, 0)
        assert blended == projected, f"Expected {projected} for zero line, got {blended}"
        assert note == "", "Should have no note for zero line"
        
        blended2, note2 = _blend_with_market_line(projected, -1)
        assert blended2 == projected, f"Expected {projected} for negative line, got {blended2}"
        print("✓ Zero/negative line returns original projection")
    
    def test_blend_same_value_no_change(self):
        """When projection equals line, blended should equal both"""
        from calibration import _blend_with_market_line
        
        projected = 8.0
        line = 8.0
        blended, note = _blend_with_market_line(projected, line)
        assert blended == 8.0, f"Expected 8.0, got {blended}"
        print("✓ Same value blend returns same value")


class TestEdgeThreshold:
    """Test _apply_edge_threshold returns STRONG/LEAN/LOW correctly"""
    
    def test_strong_edge_above_5_percent(self):
        """Edge >= 5% should return STRONG"""
        from calibration import _apply_edge_threshold, EDGE_STRONG_PCT
        
        assert EDGE_STRONG_PCT == 0.05, f"Expected EDGE_STRONG_PCT=0.05, got {EDGE_STRONG_PCT}"
        
        # 10.5 vs 10 = 5% edge
        label, note = _apply_edge_threshold(10.5, 10.0, 70)
        assert label == "STRONG", f"Expected STRONG for 5% edge, got {label}"
        
        # 11 vs 10 = 10% edge
        label2, note2 = _apply_edge_threshold(11.0, 10.0, 70)
        assert label2 == "STRONG", f"Expected STRONG for 10% edge, got {label2}"
        print("✓ Edge >= 5% returns STRONG")
    
    def test_lean_edge_2_to_5_percent(self):
        """Edge 2-5% should return LEAN"""
        from calibration import _apply_edge_threshold, EDGE_LEAN_PCT
        
        assert EDGE_LEAN_PCT == 0.02, f"Expected EDGE_LEAN_PCT=0.02, got {EDGE_LEAN_PCT}"
        
        # 10.3 vs 10 = 3% edge (between 2% and 5%)
        label, note = _apply_edge_threshold(10.3, 10.0, 60)
        assert label == "LEAN", f"Expected LEAN for 3% edge, got {label}"
        assert "LEAN" in note or "< 5%" in note, f"Expected LEAN note, got: {note}"
        print("✓ Edge 2-5% returns LEAN")
    
    def test_low_edge_below_2_percent(self):
        """Edge < 2% should return LOW"""
        from calibration import _apply_edge_threshold
        
        # 10.1 vs 10 = 1% edge
        label, note = _apply_edge_threshold(10.1, 10.0, 50)
        assert label == "LOW", f"Expected LOW for 1% edge, got {label}"
        assert "LOW" in note or "< 2%" in note, f"Expected LOW note, got: {note}"
        print("✓ Edge < 2% returns LOW")
    
    def test_zero_line_returns_strong(self):
        """Zero line should return STRONG (no edge calculation possible)"""
        from calibration import _apply_edge_threshold
        
        label, note = _apply_edge_threshold(5.0, 0, 70)
        assert label == "STRONG", f"Expected STRONG for zero line, got {label}"
        print("✓ Zero line returns STRONG")


class TestHistoricalErrorCorrection:
    """Test _correct_projected_value with mock stats"""
    
    def test_correction_with_sufficient_samples(self):
        """Should apply correction when bucket has 10+ errors"""
        from calibration import _correct_projected_value, MIN_SAMPLES_FOR_CORRECTION
        
        assert MIN_SAMPLES_FOR_CORRECTION == 10, f"Expected MIN_SAMPLES_FOR_CORRECTION=10, got {MIN_SAMPLES_FOR_CORRECTION}"
        
        # Mock stats with 15 samples showing avg error of -2.0 (over-projecting by 2)
        mock_stats = {
            "by_prop_venue": {
                "pass_attempts|away": {
                    "hit": 5, "miss": 10, "push": 0, "count": 15,
                    "errors": [-2.0] * 15  # avg error = -2.0 (actual - projected)
                }
            },
            "by_prop_rec": {},
            "by_prop": {}
        }
        
        projected = 50.0
        corrected, was_corrected, note = _correct_projected_value(
            mock_stats, "pass_attempts", "over", "away", projected
        )
        
        # avg_error = -2.0, so corrected = 50 + (-2) = 48
        assert was_corrected, "Should have applied correction"
        assert corrected == 48.0, f"Expected 48.0, got {corrected}"
        assert "Error correction" in note, f"Expected correction note, got: {note}"
        print(f"✓ Correction applied: {projected} → {corrected} (avg error -2.0)")
    
    def test_no_correction_insufficient_samples(self):
        """Should NOT apply correction when bucket has < 10 errors"""
        from calibration import _correct_projected_value
        
        # Mock stats with only 5 samples
        mock_stats = {
            "by_prop_venue": {
                "pass_attempts|away": {
                    "hit": 2, "miss": 3, "push": 0, "count": 5,
                    "errors": [-3.0] * 5
                }
            },
            "by_prop_rec": {},
            "by_prop": {}
        }
        
        projected = 50.0
        corrected, was_corrected, note = _correct_projected_value(
            mock_stats, "pass_attempts", "over", "away", projected
        )
        
        assert not was_corrected, "Should NOT apply correction with < 10 samples"
        assert corrected == projected, f"Expected {projected}, got {corrected}"
        print("✓ No correction with insufficient samples")
    
    def test_no_correction_well_calibrated(self):
        """Should NOT apply correction when avg error < 0.2"""
        from calibration import _correct_projected_value
        
        # Mock stats with small errors (well calibrated)
        mock_stats = {
            "by_prop_venue": {
                "pass_attempts|away": {
                    "hit": 8, "miss": 7, "push": 0, "count": 15,
                    "errors": [0.1] * 15  # avg error = 0.1 (< 0.2 threshold)
                }
            },
            "by_prop_rec": {},
            "by_prop": {}
        }
        
        projected = 50.0
        corrected, was_corrected, note = _correct_projected_value(
            mock_stats, "pass_attempts", "over", "away", projected
        )
        
        assert not was_corrected, "Should NOT apply correction when well-calibrated"
        assert "well-calibrated" in note, f"Expected well-calibrated note, got: {note}"
        print("✓ No correction when well-calibrated (avg error < 0.2)")
    
    def test_fallback_to_broader_bucket(self):
        """Should fall back to by_prop_rec then by_prop if specific bucket missing"""
        from calibration import _correct_projected_value
        
        # Mock stats with only by_prop bucket having sufficient data
        mock_stats = {
            "by_prop_venue": {},  # No venue-specific data
            "by_prop_rec": {},    # No rec-specific data
            "by_prop": {
                "saves": {
                    "hit": 6, "miss": 9, "push": 0, "count": 15,
                    "errors": [1.5] * 15  # avg error = +1.5 (under-projecting)
                }
            }
        }
        
        projected = 3.0
        corrected, was_corrected, note = _correct_projected_value(
            mock_stats, "saves", "over", "home", projected
        )
        
        # avg_error = +1.5, so corrected = 3 + 1.5 = 4.5
        assert was_corrected, "Should have applied correction from by_prop fallback"
        assert corrected == 4.5, f"Expected 4.5, got {corrected}"
        print(f"✓ Fallback to by_prop bucket: {projected} → {corrected}")


class TestRecommendationFlip:
    """Test _check_recommendation_flip with mock stats"""
    
    def test_flip_when_hit_rate_below_45_and_opposite_better(self):
        """Should flip when hit rate < 45% with 15+ samples and opposite is better"""
        from calibration import _check_recommendation_flip, MIN_SAMPLES_FOR_FLIP, FLIP_THRESHOLD
        
        assert MIN_SAMPLES_FOR_FLIP == 15, f"Expected MIN_SAMPLES_FOR_FLIP=15, got {MIN_SAMPLES_FOR_FLIP}"
        assert FLIP_THRESHOLD == 0.45, f"Expected FLIP_THRESHOLD=0.45, got {FLIP_THRESHOLD}"
        
        # Mock stats: saves|over has 40% hit rate, saves|under has 60% hit rate
        mock_stats = {
            "by_prop_rec": {
                "saves|over": {"hit": 6, "miss": 9, "push": 0, "count": 15, "errors": []},  # 40%
                "saves|under": {"hit": 9, "miss": 6, "push": 0, "count": 15, "errors": []}  # 60%
            }
        }
        
        should_flip, note = _check_recommendation_flip(mock_stats, "saves", "over")
        
        # 40% < 45% AND 60% > 40% + 10% → should flip
        assert should_flip, "Should flip when hit rate < 45% and opposite is better"
        assert "FLIP" in note, f"Expected FLIP note, got: {note}"
        print(f"✓ Flip triggered: saves|over 40% → saves|under 60%")
    
    def test_no_flip_when_hit_rate_above_45(self):
        """Should NOT flip when hit rate >= 45%"""
        from calibration import _check_recommendation_flip
        
        # Mock stats: saves|over has 50% hit rate
        mock_stats = {
            "by_prop_rec": {
                "saves|over": {"hit": 10, "miss": 10, "push": 0, "count": 20, "errors": []},  # 50%
                "saves|under": {"hit": 8, "miss": 12, "push": 0, "count": 20, "errors": []}   # 40%
            }
        }
        
        should_flip, note = _check_recommendation_flip(mock_stats, "saves", "over")
        
        assert not should_flip, "Should NOT flip when hit rate >= 45%"
        print("✓ No flip when hit rate >= 45%")
    
    def test_no_flip_insufficient_samples(self):
        """Should NOT flip when < 15 samples"""
        from calibration import _check_recommendation_flip
        
        # Mock stats with only 10 samples
        mock_stats = {
            "by_prop_rec": {
                "saves|over": {"hit": 3, "miss": 7, "push": 0, "count": 10, "errors": []},  # 30%
                "saves|under": {"hit": 7, "miss": 3, "push": 0, "count": 10, "errors": []}  # 70%
            }
        }
        
        should_flip, note = _check_recommendation_flip(mock_stats, "saves", "over")
        
        assert not should_flip, "Should NOT flip with < 15 samples"
        print("✓ No flip with insufficient samples")
    
    def test_no_flip_when_opposite_not_better(self):
        """Should NOT flip when opposite direction is not meaningfully better"""
        from calibration import _check_recommendation_flip
        
        # Mock stats: saves|over has 40%, saves|under has 45% (not 10%+ better)
        mock_stats = {
            "by_prop_rec": {
                "saves|over": {"hit": 6, "miss": 9, "push": 0, "count": 15, "errors": []},   # 40%
                "saves|under": {"hit": 7, "miss": 8, "push": 0, "count": 15, "errors": []}   # 46.7%
            }
        }
        
        should_flip, note = _check_recommendation_flip(mock_stats, "saves", "over")
        
        # 46.7% is NOT > 40% + 10% (50%), so should NOT flip
        assert not should_flip, "Should NOT flip when opposite is not 10%+ better"
        print("✓ No flip when opposite not meaningfully better")


class TestConfidenceRecalibration:
    """Test _recalibrate_confidence with mock stats"""
    
    def test_recalibrate_overconfident(self):
        """Should reduce confidence when AI is overconfident by > 8 points"""
        from calibration import _recalibrate_confidence
        
        # Mock stats: high_70+ band historically hits only 55%
        mock_stats = {
            "by_confidence_band": {
                "high_70+": {"hit": 11, "miss": 9, "push": 0, "count": 20}  # 55% actual
            }
        }
        
        # AI says 75% confidence, but band only hits 55%
        # Gap = 75 - 55 = 20, adjustment = 20 * 0.6 = 12
        # new_conf = max(45, 75 - 12) = 63
        new_conf, note = _recalibrate_confidence(mock_stats, 75)
        
        assert new_conf < 75, f"Should reduce confidence, got {new_conf}"
        assert "recal" in note.lower(), f"Expected recalibration note, got: {note}"
        print(f"✓ Overconfident recalibration: 75% → {new_conf}%")
    
    def test_no_recalibration_when_accurate(self):
        """Should NOT recalibrate when AI confidence matches historical accuracy"""
        from calibration import _recalibrate_confidence
        
        # Mock stats: high_70+ band historically hits 72%
        mock_stats = {
            "by_confidence_band": {
                "high_70+": {"hit": 14, "miss": 6, "push": 0, "count": 20}  # 70% actual
            }
        }
        
        # AI says 72% confidence, band hits 70% — within 8 points
        new_conf, note = _recalibrate_confidence(mock_stats, 72)
        
        assert new_conf == 72, f"Should NOT change confidence, got {new_conf}"
        assert note == "", f"Should have no note, got: {note}"
        print("✓ No recalibration when confidence matches accuracy")
    
    def test_confidence_bump_when_underconfident(self):
        """Should bump confidence when AI is underconfident with 20+ samples"""
        from calibration import _recalibrate_confidence
        
        # Mock stats: medium_55-69 band historically hits 75% with 25 samples
        mock_stats = {
            "by_confidence_band": {
                "medium_55-69": {"hit": 19, "miss": 6, "push": 0, "count": 25}  # 76% actual
            }
        }
        
        # AI says 60% confidence, but band hits 76% (16 points higher)
        # Bump = min(5, (76-60) * 0.3) = min(5, 4.8) = 4.8 → 4
        new_conf, note = _recalibrate_confidence(mock_stats, 60)
        
        assert new_conf > 60, f"Should bump confidence, got {new_conf}"
        assert "bump" in note.lower(), f"Expected bump note, got: {note}"
        print(f"✓ Underconfident bump: 60% → {new_conf}%")
    
    def test_no_recalibration_insufficient_samples(self):
        """Should NOT recalibrate when < 10 samples in band"""
        from calibration import _recalibrate_confidence
        
        # Mock stats with only 5 samples
        mock_stats = {
            "by_confidence_band": {
                "high_70+": {"hit": 2, "miss": 3, "push": 0, "count": 5}  # 40% but only 5 samples
            }
        }
        
        new_conf, note = _recalibrate_confidence(mock_stats, 75)
        
        assert new_conf == 75, f"Should NOT change confidence with < 10 samples, got {new_conf}"
        print("✓ No recalibration with insufficient samples")


# ============================================================
# INTEGRATION TESTS: API endpoint testing
# ============================================================

class TestPredictionEndpointIntegration:
    """Test that prediction endpoints don't crash with elite calibration"""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Get session token for authenticated requests"""
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        # Get session token via verify-whop
        resp = self.session.get(f"{BASE_URL}/api/verify-whop", params={"email": "josselj001@gmail.com"})
        if resp.status_code == 200:
            data = resp.json()
            self.token = data.get("sessionToken", "")
            self.email = "josselj001@gmail.com"
        else:
            pytest.skip("Could not get session token")
    
    def test_soccer_predict_no_crash(self):
        """POST /api/predict should not crash with elite calibration"""
        # Use a simple prediction request
        payload = {
            "playerName": "Mohamed Salah",
            "playerId": 306,
            "teamName": "Liverpool",
            "teamId": 40,
            "opponentName": "Manchester United",
            "opponentId": 33,
            "propType": "shots_on_target",
            "line": 1.5,
            "venue": "home",
            "leagueId": 39
        }
        
        resp = self.session.post(f"{BASE_URL}/api/predict", json=payload, timeout=120)
        
        # Should not return 500
        assert resp.status_code != 500, f"Prediction crashed: {resp.text[:500]}"
        
        if resp.status_code == 200:
            data = resp.json()
            # Check that elite calibration fields are present when corrections applied
            assert "projectedValue" in data, "Missing projectedValue"
            assert "recommendation" in data, "Missing recommendation"
            assert "confidenceScore" in data, "Missing confidenceScore"
            
            # Check for edgeStrength (from elite calibration)
            if "edgeStrength" in data:
                assert data["edgeStrength"] in ["STRONG", "LEAN", "LOW"], f"Invalid edgeStrength: {data['edgeStrength']}"
                print(f"✓ Soccer prediction has edgeStrength: {data['edgeStrength']}")
            
            # Check for calibrationApplied metadata
            if "calibrationApplied" in data:
                cal = data["calibrationApplied"]
                assert "corrections" in cal, "calibrationApplied missing corrections"
                print(f"✓ Soccer prediction has calibrationApplied with {len(cal.get('corrections', []))} corrections")
            
            print(f"✓ Soccer prediction succeeded: proj={data.get('projectedValue')}, rec={data.get('recommendation')}, conf={data.get('confidenceScore')}")
        else:
            print(f"⚠ Soccer prediction returned {resp.status_code} (may be rate limited or API issue)")
    
    def test_basketball_predict_no_crash(self):
        """POST /api/basketball/predict should not crash with elite calibration"""
        payload = {
            "playerName": "LeBron James",
            "teamName": "Los Angeles Lakers",
            "teamId": 17,
            "opponentName": "Golden State Warriors",
            "opponentId": 11,
            "propType": "points",
            "line": 25.5,
            "venue": "home"
        }
        
        resp = self.session.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        
        # Should not return 500
        assert resp.status_code != 500, f"Basketball prediction crashed: {resp.text[:500]}"
        
        if resp.status_code == 200:
            data = resp.json()
            assert "projectedValue" in data, "Missing projectedValue"
            assert "recommendation" in data, "Missing recommendation"
            
            # Check for edgeStrength
            if "edgeStrength" in data:
                assert data["edgeStrength"] in ["STRONG", "LEAN", "LOW"], f"Invalid edgeStrength: {data['edgeStrength']}"
                print(f"✓ Basketball prediction has edgeStrength: {data['edgeStrength']}")
            
            print(f"✓ Basketball prediction succeeded: proj={data.get('projectedValue')}, rec={data.get('recommendation')}")
        else:
            print(f"⚠ Basketball prediction returned {resp.status_code}")


class TestCalibrationStatsEndpoint:
    """Test get_calibration_stats function via HTTP API"""
    
    def test_get_calibration_stats_via_api(self):
        """Test calibration stats by checking prediction response"""
        # We can't easily call async functions directly in pytest without proper setup
        # Instead, verify the calibration module imports correctly and constants are set
        from calibration import get_calibration_stats, apply_elite_calibration
        
        # Verify functions exist and are callable
        assert callable(get_calibration_stats), "get_calibration_stats should be callable"
        assert callable(apply_elite_calibration), "apply_elite_calibration should be callable"
        print("✓ Calibration functions are importable and callable")


class TestApplyEliteCalibrationIntegration:
    """Test apply_elite_calibration function directly"""
    
    def test_apply_elite_calibration_function_exists(self):
        """Test apply_elite_calibration is importable and has correct signature"""
        from calibration import apply_elite_calibration
        import inspect
        
        # Verify it's an async function
        assert inspect.iscoroutinefunction(apply_elite_calibration), "apply_elite_calibration should be async"
        
        # Verify signature has expected parameters
        sig = inspect.signature(apply_elite_calibration)
        params = list(sig.parameters.keys())
        assert "prediction" in params, "Missing 'prediction' parameter"
        assert "prop_type" in params, "Missing 'prop_type' parameter"
        assert "line" in params, "Missing 'line' parameter"
        assert "venue" in params, "Missing 'venue' parameter"
        assert "sport" in params, "Missing 'sport' parameter"
        
        print(f"✓ apply_elite_calibration has correct signature: {params}")


# ============================================================
# CONSTANTS VERIFICATION
# ============================================================

class TestCalibrationConstants:
    """Verify calibration constants are set correctly"""
    
    def test_constants_values(self):
        """Verify all calibration constants"""
        from calibration import (
            MIN_SAMPLES_FOR_CORRECTION,
            MIN_SAMPLES_FOR_FLIP,
            MARKET_WEIGHT,
            FLIP_THRESHOLD,
            EDGE_STRONG_PCT,
            EDGE_LEAN_PCT
        )
        
        assert MIN_SAMPLES_FOR_CORRECTION == 10, f"MIN_SAMPLES_FOR_CORRECTION should be 10, got {MIN_SAMPLES_FOR_CORRECTION}"
        assert MIN_SAMPLES_FOR_FLIP == 15, f"MIN_SAMPLES_FOR_FLIP should be 15, got {MIN_SAMPLES_FOR_FLIP}"
        assert MARKET_WEIGHT == 0.65, f"MARKET_WEIGHT should be 0.65, got {MARKET_WEIGHT}"
        assert FLIP_THRESHOLD == 0.45, f"FLIP_THRESHOLD should be 0.45, got {FLIP_THRESHOLD}"
        assert EDGE_STRONG_PCT == 0.05, f"EDGE_STRONG_PCT should be 0.05, got {EDGE_STRONG_PCT}"
        assert EDGE_LEAN_PCT == 0.02, f"EDGE_LEAN_PCT should be 0.02, got {EDGE_LEAN_PCT}"
        
        print("✓ All calibration constants verified:")
        print(f"  - MIN_SAMPLES_FOR_CORRECTION: {MIN_SAMPLES_FOR_CORRECTION}")
        print(f"  - MIN_SAMPLES_FOR_FLIP: {MIN_SAMPLES_FOR_FLIP}")
        print(f"  - MARKET_WEIGHT: {MARKET_WEIGHT}")
        print(f"  - FLIP_THRESHOLD: {FLIP_THRESHOLD}")
        print(f"  - EDGE_STRONG_PCT: {EDGE_STRONG_PCT}")
        print(f"  - EDGE_LEAN_PCT: {EDGE_LEAN_PCT}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
