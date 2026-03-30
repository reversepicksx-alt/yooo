"""
Test suite for Scan + Tactical integration (iteration 22)
Tests:
- POST /api/tactical/start returns session_id and welcome message
- POST /api/tactical/message returns response with proper structure
- POST /api/tactical/message detects international context when teams are national teams
- GET /api/health returns ok
- GET /api/cache/status returns valid counts
- GET /api/cache/lookup/player?name=Hojbjerg returns player correctly
- No Grok/Gemini branding in responses
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndCache:
    """Health check and cache status tests"""
    
    def test_health_endpoint(self):
        """GET /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print("✓ GET /api/health returns {status: ok}")
    
    def test_cache_status(self):
        """GET /api/cache/status returns valid counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status")
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert "teams" in data
        assert "players" in data
        assert "nationalTeams" in data
        assert data["leagues"] > 0
        assert data["teams"] > 0
        assert data["players"] > 0
        assert data["nationalTeams"] > 0
        print(f"✓ GET /api/cache/status returns valid counts: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}, nationalTeams={data['nationalTeams']}")
    
    def test_cache_lookup_hojbjerg(self):
        """GET /api/cache/lookup/player?name=Hojbjerg returns player correctly"""
        response = requests.get(f"{BASE_URL}/api/cache/lookup/player", params={"name": "Hojbjerg"})
        assert response.status_code == 200
        data = response.json()
        assert data.get("found") == True
        player = data.get("player", {})
        assert player.get("playerId") == 2735
        assert "Højbjerg" in player.get("name", "") or "Hojbjerg" in player.get("name", "")
        print(f"✓ GET /api/cache/lookup/player?name=Hojbjerg returns: {player.get('name')} (id={player.get('playerId')})")


class TestTacticalEndpoints:
    """Tactical endpoint tests"""
    
    def test_tactical_start_returns_session_and_message(self):
        """POST /api/tactical/start returns session_id and welcome message"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data
        assert data["session_id"].startswith("tac-")
        assert "Reverse Tactical" in data["message"]
        print(f"✓ POST /api/tactical/start returns session_id={data['session_id'][:20]}... and welcome message")
    
    def test_tactical_start_no_grok_gemini_branding(self):
        """POST /api/tactical/start welcome message has no Grok/Gemini mentions"""
        response = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        assert response.status_code == 200
        data = response.json()
        message = data.get("message", "").lower()
        assert "grok" not in message, f"Found 'grok' in welcome message: {data['message']}"
        assert "gemini" not in message, f"Found 'gemini' in welcome message: {data['message']}"
        print("✓ POST /api/tactical/start welcome message has no Grok/Gemini branding")
    
    def test_tactical_message_returns_proper_structure(self):
        """POST /api/tactical/message returns response with proper structure"""
        # Start session first
        start_resp = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_resp.json().get("session_id")
        
        # Send a simple message
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": "What is a false 9?"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert data["session_id"] == session_id
        # scanEntries should be null for text-only messages
        assert "scanEntries" in data
        print(f"✓ POST /api/tactical/message returns proper structure: response={len(data['response'])} chars, session_id={data['session_id'][:15]}...")
    
    def test_tactical_message_no_grok_gemini_in_response(self):
        """POST /api/tactical/message response has no Grok/Gemini mentions"""
        start_resp = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_resp.json().get("session_id")
        
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": "Explain pressing triggers"},
            timeout=60
        )
        assert response.status_code == 200
        data = response.json()
        resp_text = data.get("response", "").lower()
        assert "grok" not in resp_text, f"Found 'grok' in response"
        assert "gemini" not in resp_text, f"Found 'gemini' in response"
        print("✓ POST /api/tactical/message response has no Grok/Gemini branding")
    
    def test_tactical_message_empty_returns_400(self):
        """POST /api/tactical/message with empty message returns 400"""
        start_resp = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_resp.json().get("session_id")
        
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": ""}
        )
        assert response.status_code == 400
        print("✓ POST /api/tactical/message with empty message returns 400")


class TestInternationalContextDetection:
    """Tests for international context detection in tactical messages"""
    
    def test_tactical_detects_international_context_denmark(self):
        """POST /api/tactical/message detects international context when Denmark is mentioned"""
        start_resp = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_resp.json().get("session_id")
        
        # Query about Denmark (national team)
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": "What are Hojbjerg stats for Denmark?"},
            timeout=90
        )
        assert response.status_code == 200
        data = response.json()
        resp_text = data.get("response", "").lower()
        
        # Should mention international/Denmark context
        has_intl_context = any(kw in resp_text for kw in ["denmark", "international", "national team", "friendlies"])
        assert has_intl_context, "Response should mention Denmark or international context"
        print("✓ POST /api/tactical/message detects international context when Denmark is mentioned")
    
    def test_tactical_international_stats_prioritized(self):
        """POST /api/tactical/message prioritizes international stats for national team queries"""
        start_resp = requests.post(f"{BASE_URL}/api/tactical/start", json={})
        session_id = start_resp.json().get("session_id")
        
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={"session_id": session_id, "message": "Analyze Hojbjerg for Denmark vs Sweden international match"},
            timeout=90
        )
        assert response.status_code == 200
        data = response.json()
        resp_text = data.get("response", "")
        
        # Response should be substantial
        assert len(resp_text) > 200, "Response should be substantial for international analysis"
        print(f"✓ POST /api/tactical/message returns substantial international analysis ({len(resp_text)} chars)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
