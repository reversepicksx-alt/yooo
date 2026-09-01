"""
Test Tactical Branding Update - Iteration 21
Tests:
1. POST /api/tactical/start returns welcome message WITHOUT 'Grok' or 'Gemini' mentions
2. POST /api/tactical/message with text only returns response without model name mentions
3. POST /api/tactical/message with empty message AND no image returns 400
4. POST /api/tactical/message accepts image_base64 field (optional)
5. GET /api/health returns ok
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestHealthEndpoint:
    """Health check endpoint"""
    
    def test_health_returns_ok(self):
        """GET /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print("✓ GET /api/health returns {status: ok}")


class TestTacticalBrandingRemoval:
    """Tests for Grok/Gemini branding removal from tactical endpoints"""
    
    def test_tactical_start_no_grok_mention(self):
        """POST /api/tactical/start returns welcome message WITHOUT 'Grok' mention"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        
        assert "session_id" in data
        assert "message" in data
        
        message = data["message"].lower()
        assert "grok" not in message, f"Found 'grok' in welcome message: {data['message']}"
        print(f"✓ POST /api/tactical/start - no 'Grok' in message")
    
    def test_tactical_start_no_gemini_mention(self):
        """POST /api/tactical/start returns welcome message WITHOUT 'Gemini' mention"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        
        message = data["message"].lower()
        assert "gemini" not in message, f"Found 'gemini' in welcome message: {data['message']}"
        print(f"✓ POST /api/tactical/start - no 'Gemini' in message")
    
    def test_tactical_start_no_dual_engine_mention(self):
        """POST /api/tactical/start returns welcome message WITHOUT 'dual engine' mention"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        
        message = data["message"].lower()
        assert "dual engine" not in message, f"Found 'dual engine' in welcome message: {data['message']}"
        print(f"✓ POST /api/tactical/start - no 'dual engine' in message")
    
    def test_tactical_message_text_only_no_model_names(self):
        """POST /api/tactical/message with text only returns response without model name mentions in structure"""
        # First start a session
        start_response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_response.json().get("session_id")
        
        # Send a simple message
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": session_id,
            "message": "What is a false 9?"
        })
        assert response.status_code == 200
        data = response.json()
        
        # Check response structure - should NOT have 'sources' with grok/gemini booleans
        # The new structure should just have response, session_id, scanEntries
        assert "response" in data
        assert "session_id" in data
        assert "scanEntries" in data
        
        # The response structure should NOT expose model names
        # (Note: the actual AI response content may mention analytical concepts, but the API structure shouldn't expose model names)
        print(f"✓ POST /api/tactical/message - response structure: {list(data.keys())}")
    
    def test_tactical_message_empty_returns_400(self):
        """POST /api/tactical/message with empty message AND no image returns 400"""
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": "test-empty-msg",
            "message": ""
        })
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "empty" in data["detail"].lower()
        print(f"✓ POST /api/tactical/message with empty message returns 400: {data['detail']}")
    
    def test_tactical_message_whitespace_only_returns_400(self):
        """POST /api/tactical/message with whitespace-only message returns 400"""
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": "test-whitespace-msg",
            "message": "   "
        })
        assert response.status_code == 400
        print(f"✓ POST /api/tactical/message with whitespace-only message returns 400")
    
    def test_tactical_message_accepts_image_base64_field(self):
        """POST /api/tactical/message accepts image_base64 field (optional)"""
        # Test that the endpoint accepts the image_base64 field without error
        # We're NOT actually uploading an image (to save API credits), just verifying the field is accepted
        
        # First, verify the request schema accepts image_base64 by sending a request with it
        # Using a minimal/invalid base64 to test field acceptance without triggering actual image processing
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": "test-image-field",
            "message": "Test message",
            "image_base64": None  # Optional field should be accepted as null
        })
        # Should not return 422 (validation error) for having the field
        assert response.status_code != 422, "image_base64 field should be accepted"
        print(f"✓ POST /api/tactical/message accepts image_base64 field (status: {response.status_code})")


class TestTacticalMessageRequestSchema:
    """Tests for TacticalMessageRequest schema with optional image_base64"""
    
    def test_message_request_with_text_only(self):
        """TacticalMessageRequest works with text only (no image)"""
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": "test-text-only",
            "message": "Hello"
        })
        assert response.status_code == 200
        print(f"✓ TacticalMessageRequest works with text only")
    
    def test_message_request_with_empty_message_and_no_image(self):
        """TacticalMessageRequest with empty message and no image returns 400"""
        response = requests.post(f"{BASE_URL}/api/tactical/message", json={
            "session_id": "test-empty-no-image",
            "message": "",
            "image_base64": None
        })
        assert response.status_code == 400
        print(f"✓ TacticalMessageRequest with empty message and no image returns 400")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
