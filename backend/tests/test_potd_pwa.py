"""
Test suite for Pick of the Day (POTD) and PWA features
Tests the new POTD endpoint and PWA manifest/service worker
"""
import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPickOfTheDay:
    """Tests for the Pick of the Day feature"""
    
    def test_potd_endpoint_returns_valid_response(self):
        """Test that GET /api/pick-of-the-day returns a valid pick"""
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day", timeout=30)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        # Check required top-level fields
        assert "date" in data, "Response missing 'date' field"
        assert "available" in data, "Response missing 'available' field"
        
        if data["available"]:
            assert "pick" in data, "Response missing 'pick' field when available=true"
            assert "generatedAt" in data, "Response missing 'generatedAt' field"
            
            pick = data["pick"]
            # Verify pick object structure
            assert "playerName" in pick, "Pick missing 'playerName'"
            assert "teamName" in pick, "Pick missing 'teamName'"
            assert "opponentName" in pick, "Pick missing 'opponentName'"
            assert "propType" in pick, "Pick missing 'propType'"
            assert "suggestedLine" in pick, "Pick missing 'suggestedLine'"
            assert "recommendation" in pick, "Pick missing 'recommendation'"
            assert "confidenceScore" in pick, "Pick missing 'confidenceScore'"
            assert "sharpSummary" in pick, "Pick missing 'sharpSummary'"
            
            # Verify data types and values
            assert isinstance(pick["playerName"], str) and len(pick["playerName"]) > 0
            assert isinstance(pick["confidenceScore"], (int, float))
            assert 0 <= pick["confidenceScore"] <= 100
            assert pick["recommendation"] in ["over", "under"]
            
            print(f"POTD: {pick['playerName']} - {pick['propType']} {pick['suggestedLine']} ({pick['recommendation']}) - {pick['confidenceScore']}% confidence")
        else:
            print(f"POTD not available: {data.get('message', 'No message')}")
    
    def test_potd_caching_returns_same_result(self):
        """Test that calling POTD twice returns the same cached result"""
        # First call
        response1 = requests.get(f"{BASE_URL}/api/pick-of-the-day", timeout=30)
        assert response1.status_code == 200
        data1 = response1.json()
        
        # Wait a moment
        time.sleep(1)
        
        # Second call
        response2 = requests.get(f"{BASE_URL}/api/pick-of-the-day", timeout=30)
        assert response2.status_code == 200
        data2 = response2.json()
        
        # Verify same generatedAt timestamp (cached)
        if data1.get("available") and data2.get("available"):
            assert data1["generatedAt"] == data2["generatedAt"], \
                f"Cache not working: {data1['generatedAt']} != {data2['generatedAt']}"
            assert data1["pick"]["playerName"] == data2["pick"]["playerName"], \
                "Cached pick player name mismatch"
            print(f"Caching verified: Both calls returned generatedAt={data1['generatedAt']}")
        else:
            print("POTD not available, skipping cache verification")
    
    def test_potd_pick_has_valid_prop_type(self):
        """Test that POTD pick has a valid prop type"""
        valid_prop_types = [
            "pass_attempts", "shots", "shots_on_target", "tackles", 
            "key_passes", "saves", "interceptions", "blocks", 
            "dribbles", "fouls_drawn"
        ]
        
        response = requests.get(f"{BASE_URL}/api/pick-of-the-day", timeout=30)
        assert response.status_code == 200
        data = response.json()
        
        if data.get("available"):
            prop_type = data["pick"]["propType"]
            assert prop_type in valid_prop_types, \
                f"Invalid prop type: {prop_type}. Expected one of {valid_prop_types}"
            print(f"Valid prop type: {prop_type}")
        else:
            pytest.skip("POTD not available")


class TestPWAManifest:
    """Tests for PWA manifest.json"""
    
    def test_manifest_accessible(self):
        """Test that manifest.json is accessible"""
        response = requests.get(f"{BASE_URL}/manifest.json", timeout=10)
        assert response.status_code == 200, f"Manifest not accessible: {response.status_code}"
        print("manifest.json is accessible")
    
    def test_manifest_valid_json(self):
        """Test that manifest.json is valid JSON with required fields"""
        response = requests.get(f"{BASE_URL}/manifest.json", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        
        # Required PWA manifest fields
        assert "name" in data, "Manifest missing 'name'"
        assert "short_name" in data, "Manifest missing 'short_name'"
        assert "icons" in data, "Manifest missing 'icons'"
        assert "theme_color" in data, "Manifest missing 'theme_color'"
        assert "display" in data, "Manifest missing 'display'"
        
        # Verify display mode
        assert data["display"] == "standalone", \
            f"Expected display='standalone', got '{data['display']}'"
        
        # Verify icons array
        assert isinstance(data["icons"], list) and len(data["icons"]) > 0, \
            "Manifest icons should be a non-empty array"
        
        print(f"Manifest valid: {data['name']} ({data['short_name']})")
        print(f"Display: {data['display']}, Theme: {data['theme_color']}")
        print(f"Icons: {len(data['icons'])} defined")


class TestPWAServiceWorker:
    """Tests for PWA service worker"""
    
    def test_service_worker_accessible(self):
        """Test that service-worker.js is accessible"""
        response = requests.get(f"{BASE_URL}/service-worker.js", timeout=10)
        assert response.status_code == 200, f"Service worker not accessible: {response.status_code}"
        
        # Verify it's JavaScript
        content_type = response.headers.get('content-type', '')
        assert 'javascript' in content_type.lower(), \
            f"Expected JavaScript content type, got: {content_type}"
        
        print("service-worker.js is accessible and returns JavaScript")
    
    def test_service_worker_has_cache_logic(self):
        """Test that service worker contains caching logic"""
        response = requests.get(f"{BASE_URL}/service-worker.js", timeout=10)
        assert response.status_code == 200
        
        content = response.text
        
        # Check for essential service worker patterns
        assert "addEventListener" in content, "Service worker missing event listeners"
        assert "fetch" in content, "Service worker missing fetch handler"
        assert "cache" in content.lower(), "Service worker missing cache logic"
        
        print("Service worker contains required caching logic")


class TestPWAMetaTags:
    """Tests for PWA meta tags in index.html"""
    
    def test_index_html_has_pwa_meta_tags(self):
        """Test that index.html contains required PWA meta tags"""
        response = requests.get(f"{BASE_URL}/", timeout=10)
        assert response.status_code == 200
        
        html = response.text
        
        # Check for PWA meta tags
        assert 'apple-mobile-web-app-capable' in html, \
            "Missing apple-mobile-web-app-capable meta tag"
        assert 'apple-touch-icon' in html, \
            "Missing apple-touch-icon link"
        assert 'manifest' in html, \
            "Missing manifest link"
        
        print("index.html contains required PWA meta tags")


class TestAuthStillWorks:
    """Verify auth still works after changes"""
    
    def test_owner_auto_login(self):
        """Test that owner email auto-login still works"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"},
            timeout=10
        )
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("verified") == True, "Owner should be auto-verified"
        assert data.get("access_type") == "Owner", "Owner should have 'Owner' access type"
        assert "session_token" in data, "Owner should receive session token"
        
        print(f"Owner auto-login works: {data['email']} - {data['access_type']}")
    
    def test_health_endpoint(self):
        """Test that health endpoint still works"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        
        data = response.json()
        assert data.get("status") == "ok"
        print(f"Health check passed: {data}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
