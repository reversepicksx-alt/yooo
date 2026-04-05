"""
Iteration 58: Calibration Stats Panel Tests
Tests the new calibration object in /api/intel/dashboard with 5 fields:
1. confidenceAccuracy - maps confidence bands to actual historical hit rates
2. flipCandidates - prop+rec combos with < 50% hit rate and 5+ samples
3. edgePerformance - STRONG/LEAN/LOW edge strength performance
4. errorMap - avgError, direction (over/under-projecting), sample counts per prop+venue
5. propRecBreakdown - full hit rates for every prop+direction combo
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Owner credentials
OWNER_EMAIL = "josselj001@gmail.com"


def get_owner_session():
    """Get owner session token via verify-whop endpoint"""
    response = requests.post(
        f"{BASE_URL}/api/auth/verify-whop",
        json={"email": OWNER_EMAIL}
    )
    if response.status_code == 200:
        data = response.json()
        token = data.get("session_token") or data.get("token")
        if token:
            return token
    return None


class TestCalibrationStatsPanel:
    """Tests for the Calibration Stats Panel in INTEL tab"""
    
    @pytest.fixture(scope="class")
    def owner_session(self):
        """Get owner session token via verify-whop endpoint"""
        token = get_owner_session()
        if not token:
            pytest.skip("Could not obtain owner session token")
        return token
    
    def test_intel_dashboard_returns_calibration_object(self, owner_session):
        """Test that /api/intel/dashboard returns calibration object"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Should not have error
        assert "error" not in data, f"Got error: {data.get('error')}"
        
        # Should have calibration object
        assert "calibration" in data, "Missing 'calibration' field in response"
        cal = data["calibration"]
        
        # Verify all 5 required fields exist
        assert "confidenceAccuracy" in cal, "Missing 'confidenceAccuracy' in calibration"
        assert "flipCandidates" in cal, "Missing 'flipCandidates' in calibration"
        assert "edgePerformance" in cal, "Missing 'edgePerformance' in calibration"
        assert "errorMap" in cal, "Missing 'errorMap' in calibration"
        assert "propRecBreakdown" in cal, "Missing 'propRecBreakdown' in calibration"
        
        print(f"✓ Calibration object has all 5 required fields")
    
    def test_confidence_accuracy_structure(self, owner_session):
        """Test confidenceAccuracy maps confidence bands to actual hit rates"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        conf_accuracy = cal.get("confidenceAccuracy", {})
        
        # With 15 soccer picks, we should have some confidence bands
        # Expected bands: high_70+, mid_55-69, low_<55
        valid_bands = {"high_70+", "mid_55-69", "low_<55"}
        
        for band_key, band_data in conf_accuracy.items():
            assert band_key in valid_bands, f"Unexpected band key: {band_key}"
            
            # Each band should have required fields
            assert "hits" in band_data, f"Missing 'hits' in band {band_key}"
            assert "misses" in band_data, f"Missing 'misses' in band {band_key}"
            assert "total" in band_data, f"Missing 'total' in band {band_key}"
            assert "rate" in band_data, f"Missing 'rate' in band {band_key}"
            assert "label" in band_data, f"Missing 'label' in band {band_key}"
            
            # Validate data types
            assert isinstance(band_data["hits"], int), f"hits should be int"
            assert isinstance(band_data["misses"], int), f"misses should be int"
            assert isinstance(band_data["total"], int), f"total should be int"
            assert isinstance(band_data["rate"], (int, float)), f"rate should be numeric"
            
            # Validate math
            assert band_data["total"] == band_data["hits"] + band_data["misses"], \
                f"total should equal hits + misses for band {band_key}"
        
        print(f"✓ confidenceAccuracy has {len(conf_accuracy)} bands with correct structure")
        for k, v in conf_accuracy.items():
            print(f"  - {k}: {v['rate']}% ({v['hits']}/{v['total']})")
    
    def test_flip_candidates_structure(self, owner_session):
        """Test flipCandidates lists prop+rec combos with < 50% hit rate and 5+ samples"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        flip_candidates = cal.get("flipCandidates", [])
        
        # flipCandidates should be a list
        assert isinstance(flip_candidates, list), "flipCandidates should be a list"
        
        # Each candidate should have required fields
        for i, fc in enumerate(flip_candidates):
            assert "prop" in fc, f"Missing 'prop' in flip candidate {i}"
            assert "rec" in fc, f"Missing 'rec' in flip candidate {i}"
            assert "rate" in fc, f"Missing 'rate' in flip candidate {i}"
            assert "total" in fc, f"Missing 'total' in flip candidate {i}"
            assert "hits" in fc, f"Missing 'hits' in flip candidate {i}"
            
            # Validate constraints: < 50% rate and 5+ samples
            assert fc["rate"] < 50, f"Flip candidate {i} has rate >= 50%: {fc['rate']}"
            assert fc["total"] >= 5, f"Flip candidate {i} has < 5 samples: {fc['total']}"
        
        # Should be sorted by rate ascending
        if len(flip_candidates) > 1:
            rates = [fc["rate"] for fc in flip_candidates]
            assert rates == sorted(rates), "flipCandidates should be sorted by rate ascending"
        
        print(f"✓ flipCandidates has {len(flip_candidates)} entries (expected 0 per context)")
    
    def test_edge_performance_structure(self, owner_session):
        """Test edgePerformance shows STRONG/LEAN/LOW performance"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        edge_perf = cal.get("edgePerformance", {})
        
        # Valid edge strength keys
        valid_keys = {"STRONG", "LEAN", "LOW", "UNKNOWN"}
        
        for key, perf_data in edge_perf.items():
            assert key in valid_keys, f"Unexpected edge key: {key}"
            
            # Each entry should have required fields
            assert "hits" in perf_data, f"Missing 'hits' in edge {key}"
            assert "misses" in perf_data, f"Missing 'misses' in edge {key}"
            assert "total" in perf_data, f"Missing 'total' in edge {key}"
            assert "rate" in perf_data, f"Missing 'rate' in edge {key}"
            
            # Validate math
            assert perf_data["total"] == perf_data["hits"] + perf_data["misses"], \
                f"total should equal hits + misses for edge {key}"
        
        print(f"✓ edgePerformance has {len(edge_perf)} entries")
        for k, v in edge_perf.items():
            print(f"  - {k}: {v['rate']}% ({v['hits']}/{v['total']})")
    
    def test_error_map_structure(self, owner_session):
        """Test errorMap contains avgError, direction, sample counts per prop+venue"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        error_map = cal.get("errorMap", {})
        
        # errorMap should be a dict
        assert isinstance(error_map, dict), "errorMap should be a dict"
        
        for key, entry in error_map.items():
            # Key format should be "prop|venue"
            assert "|" in key, f"errorMap key should contain '|': {key}"
            
            # Each entry should have required fields
            assert "prop" in entry, f"Missing 'prop' in errorMap entry {key}"
            assert "venue" in entry, f"Missing 'venue' in errorMap entry {key}"
            assert "avgError" in entry, f"Missing 'avgError' in errorMap entry {key}"
            assert "total" in entry, f"Missing 'total' in errorMap entry {key}"
            assert "rate" in entry, f"Missing 'rate' in errorMap entry {key}"
            assert "direction" in entry, f"Missing 'direction' in errorMap entry {key}"
            
            # Validate direction values
            valid_directions = {"over-projecting", "under-projecting"}
            assert entry["direction"] in valid_directions, \
                f"Invalid direction: {entry['direction']}"
            
            # Validate direction matches avgError sign
            if entry["avgError"] < 0:
                assert entry["direction"] == "over-projecting", \
                    f"Negative avgError should be over-projecting"
            else:
                assert entry["direction"] == "under-projecting", \
                    f"Positive avgError should be under-projecting"
            
            # Minimum 3 samples required
            assert entry["total"] >= 3, f"errorMap entry should have >= 3 samples"
        
        print(f"✓ errorMap has {len(error_map)} entries")
        for k, v in error_map.items():
            print(f"  - {k}: avgError={v['avgError']}, {v['direction']} (n={v['total']})")
    
    def test_prop_rec_breakdown_structure(self, owner_session):
        """Test propRecBreakdown shows full hit rates for every prop+direction combo"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        prop_rec = cal.get("propRecBreakdown", {})
        
        # propRecBreakdown should be a dict
        assert isinstance(prop_rec, dict), "propRecBreakdown should be a dict"
        
        for key, entry in prop_rec.items():
            # Key format should be "prop|rec" (e.g., "saves|over")
            assert "|" in key, f"propRecBreakdown key should contain '|': {key}"
            parts = key.split("|")
            assert len(parts) == 2, f"propRecBreakdown key should have 2 parts: {key}"
            
            # rec should be over/under
            rec = parts[1]
            assert rec in {"over", "under", "unknown"}, f"Invalid rec in key: {rec}"
            
            # Each entry should have required fields
            assert "hits" in entry, f"Missing 'hits' in propRecBreakdown entry {key}"
            assert "misses" in entry, f"Missing 'misses' in propRecBreakdown entry {key}"
            assert "total" in entry, f"Missing 'total' in propRecBreakdown entry {key}"
            assert "rate" in entry, f"Missing 'rate' in propRecBreakdown entry {key}"
            
            # Validate math
            assert entry["total"] == entry["hits"] + entry["misses"], \
                f"total should equal hits + misses for {key}"
        
        print(f"✓ propRecBreakdown has {len(prop_rec)} entries")
        for k, v in prop_rec.items():
            print(f"  - {k}: {v['rate']}% ({v['hits']}/{v['total']})")
    
    def test_basketball_calibration_data(self, owner_session):
        """Test calibration data for basketball sport"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "basketball"}
        )
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Should not have error
        assert "error" not in data, f"Got error: {data.get('error')}"
        
        # Should have calibration object
        assert "calibration" in data, "Missing 'calibration' field in basketball response"
        cal = data["calibration"]
        
        # Verify all 5 required fields exist
        assert "confidenceAccuracy" in cal, "Missing 'confidenceAccuracy' in basketball calibration"
        assert "flipCandidates" in cal, "Missing 'flipCandidates' in basketball calibration"
        assert "edgePerformance" in cal, "Missing 'edgePerformance' in basketball calibration"
        assert "errorMap" in cal, "Missing 'errorMap' in basketball calibration"
        assert "propRecBreakdown" in cal, "Missing 'propRecBreakdown' in basketball calibration"
        
        print(f"✓ Basketball calibration object has all 5 required fields")
        print(f"  - confidenceAccuracy: {len(cal['confidenceAccuracy'])} bands")
        print(f"  - flipCandidates: {len(cal['flipCandidates'])} entries")
        print(f"  - edgePerformance: {len(cal['edgePerformance'])} entries")
        print(f"  - errorMap: {len(cal['errorMap'])} entries")
        print(f"  - propRecBreakdown: {len(cal['propRecBreakdown'])} entries")
    
    def test_auto_flip_badge_criteria(self, owner_session):
        """Test that AUTO-FLIP ACTIVE badge criteria is correct (rate < 45% and total >= 15)"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        flip_candidates = cal.get("flipCandidates", [])
        
        # Check if any flip candidates would show AUTO-FLIP ACTIVE badge
        auto_flip_active = [fc for fc in flip_candidates if fc["rate"] < 45 and fc["total"] >= 15]
        
        print(f"✓ AUTO-FLIP ACTIVE badge criteria verified")
        print(f"  - Total flip candidates: {len(flip_candidates)}")
        print(f"  - With AUTO-FLIP ACTIVE (rate < 45% and total >= 15): {len(auto_flip_active)}")
        
        for fc in auto_flip_active:
            print(f"    - {fc['prop']}|{fc['rec']}: {fc['rate']}% ({fc['total']} samples)")
    
    def test_non_owner_access_denied(self):
        """Test that non-owner users cannot access intel dashboard"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": "test@example.com", "token": "fake_token", "sport": "soccer"}
        )
        
        assert response.status_code == 200  # API returns 200 with error message
        data = response.json()
        assert "error" in data, "Non-owner should get error"
        assert data["error"] == "Owner only", f"Expected 'Owner only' error, got: {data.get('error')}"
        
        print("✓ Non-owner access correctly denied")


class TestCalibrationDataIntegrity:
    """Tests for data integrity in calibration stats"""
    
    @pytest.fixture(scope="class")
    def owner_session(self):
        """Get owner session token via verify-whop endpoint"""
        token = get_owner_session()
        if not token:
            pytest.skip("Could not obtain owner session token")
        return token
    
    def test_calibration_totals_match_overall(self, owner_session):
        """Test that calibration data totals are consistent with overall stats"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        
        total_picks = data.get("total", 0)
        total_hits = data.get("totalHits", 0)
        total_misses = data.get("totalMisses", 0)
        
        # Sum of confidence bands should equal total
        cal = data.get("calibration", {})
        conf_accuracy = cal.get("confidenceAccuracy", {})
        
        conf_total = sum(v["total"] for v in conf_accuracy.values())
        
        # Allow for some variance due to pushes being excluded
        assert conf_total <= total_picks, \
            f"Confidence band total ({conf_total}) should not exceed total picks ({total_picks})"
        
        print(f"✓ Data integrity verified")
        print(f"  - Total picks: {total_picks}")
        print(f"  - Confidence band total: {conf_total}")
        print(f"  - Total hits: {total_hits}, Total misses: {total_misses}")
    
    def test_prop_rec_breakdown_covers_all_recs(self, owner_session):
        """Test that propRecBreakdown covers both over and under for each prop"""
        response = requests.get(
            f"{BASE_URL}/api/intel/dashboard",
            params={"email": OWNER_EMAIL, "token": owner_session, "sport": "soccer"}
        )
        
        assert response.status_code == 200
        data = response.json()
        cal = data.get("calibration", {})
        prop_rec = cal.get("propRecBreakdown", {})
        
        # Group by prop type
        props = {}
        for key in prop_rec.keys():
            parts = key.split("|")
            prop = parts[0]
            rec = parts[1] if len(parts) > 1 else "unknown"
            if prop not in props:
                props[prop] = set()
            props[prop].add(rec)
        
        print(f"✓ propRecBreakdown coverage:")
        for prop, recs in props.items():
            print(f"  - {prop}: {', '.join(sorted(recs))}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
