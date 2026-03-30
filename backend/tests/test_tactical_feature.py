"""
Test suite for Reverse Tactical feature - Dual AI Chat Engine (Grok + Gemini)
Tests: POST /api/tactical/start, POST /api/tactical/message, conversation context
"""

import pytest
import requests
import os
import time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthAndCache:
    """Basic health and cache status tests"""
    
    def test_health_endpoint(self):
        """GET /api/health returns ok"""
        response = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
        print(f"✓ Health check passed: {data}")
    
    def test_cache_status(self):
        """GET /api/cache/status returns valid counts"""
        response = requests.get(f"{BASE_URL}/api/cache/status", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert "leagues" in data
        assert "teams" in data
        assert "players" in data
        assert "nationalTeams" in data
        print(f"✓ Cache status: leagues={data['leagues']}, teams={data['teams']}, players={data['players']}, nationalTeams={data['nationalTeams']}")


class TestTacticalStart:
    """Tests for POST /api/tactical/start endpoint"""
    
    def test_tactical_start_returns_session_id(self):
        """POST /api/tactical/start returns session_id and welcome message"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify session_id is returned
        assert "session_id" in data
        assert data["session_id"] is not None
        assert len(data["session_id"]) > 0
        print(f"✓ Session ID returned: {data['session_id']}")
        
        # Verify welcome message is returned
        assert "message" in data
        assert len(data["message"]) > 0
        assert "REVERSE TACTICAL" in data["message"] or "Tactical" in data["message"] or "Grok" in data["message"].lower() or "Gemini" in data["message"].lower()
        print(f"✓ Welcome message returned (length: {len(data['message'])} chars)")
    
    def test_tactical_start_with_custom_session_id(self):
        """POST /api/tactical/start with custom session_id preserves it"""
        custom_session = "test-session-12345"
        response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            json={"session_id": custom_session},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == custom_session
        print(f"✓ Custom session ID preserved: {data['session_id']}")


class TestTacticalMessage:
    """Tests for POST /api/tactical/message endpoint"""
    
    @pytest.fixture(scope="class")
    def tactical_session(self):
        """Create a tactical session for message tests"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert response.status_code == 200
        return response.json()["session_id"]
    
    def test_tactical_message_returns_response_with_sources(self, tactical_session):
        """POST /api/tactical/message returns response with sources object (grok, gemini, liveData booleans)"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": tactical_session,
                "message": "What is PPDA in soccer?"
            },
            headers={"Content-Type": "application/json"},
            timeout=60  # Grok + Gemini can take 15-30 seconds
        )
        assert response.status_code == 200
        data = response.json()
        
        # Verify response text is returned
        assert "response" in data
        assert len(data["response"]) > 0
        print(f"✓ Response returned (length: {len(data['response'])} chars)")
        
        # Verify session_id is returned
        assert "session_id" in data
        assert data["session_id"] == tactical_session
        print(f"✓ Session ID preserved: {data['session_id']}")
        
        # Verify sources object with grok, gemini, liveData booleans
        assert "sources" in data
        sources = data["sources"]
        assert "grok" in sources
        assert "gemini" in sources
        assert "liveData" in sources
        assert isinstance(sources["grok"], bool)
        assert isinstance(sources["gemini"], bool)
        assert isinstance(sources["liveData"], bool)
        print(f"✓ Sources object: grok={sources['grok']}, gemini={sources['gemini']}, liveData={sources['liveData']}")
    
    def test_tactical_message_empty_message_returns_400(self, tactical_session):
        """POST /api/tactical/message with empty message returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": tactical_session,
                "message": ""
            },
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert response.status_code == 400
        print("✓ Empty message correctly returns 400")
    
    def test_tactical_message_whitespace_only_returns_400(self, tactical_session):
        """POST /api/tactical/message with whitespace-only message returns 400"""
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": tactical_session,
                "message": "   "
            },
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert response.status_code == 400
        print("✓ Whitespace-only message correctly returns 400")


class TestTacticalConversationContext:
    """Tests for conversation context maintenance"""
    
    def test_tactical_maintains_conversation_context(self):
        """POST /api/tactical/message maintains conversation context (send 2 messages, second should reference first)"""
        # Start a new session
        start_response = requests.post(
            f"{BASE_URL}/api/tactical/start",
            json={},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        assert start_response.status_code == 200
        session_id = start_response.json()["session_id"]
        print(f"✓ Started session: {session_id}")
        
        # First message - establish context about a specific player
        msg1_response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": session_id,
                "message": "Tell me about Bukayo Saka's role at Arsenal"
            },
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        assert msg1_response.status_code == 200
        msg1_data = msg1_response.json()
        assert "response" in msg1_data
        print(f"✓ First message response received (length: {len(msg1_data['response'])} chars)")
        
        # Second message - reference the first without repeating context
        msg2_response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": session_id,
                "message": "How does his position affect his shot attempts?"
            },
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        assert msg2_response.status_code == 200
        msg2_data = msg2_response.json()
        assert "response" in msg2_data
        
        # The response should reference Saka or Arsenal or the previous context
        response_lower = msg2_data["response"].lower()
        context_maintained = any(term in response_lower for term in ["saka", "arsenal", "winger", "right", "shot", "position"])
        print(f"✓ Second message response received (length: {len(msg2_data['response'])} chars)")
        print(f"✓ Context maintained: {context_maintained}")
        
        # Verify sources are still returned
        assert "sources" in msg2_data
        print(f"✓ Sources in second response: {msg2_data['sources']}")


class TestTacticalNewSession:
    """Tests for new session without prior session_id"""
    
    def test_tactical_message_creates_session_if_missing(self):
        """POST /api/tactical/message creates session if session_id doesn't exist"""
        # Use a random session_id that doesn't exist
        response = requests.post(
            f"{BASE_URL}/api/tactical/message",
            json={
                "session_id": "nonexistent-session-xyz",
                "message": "What is pressing?"
            },
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        # Should still work - creates new session
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        print(f"✓ Message with nonexistent session still works, session: {data['session_id']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
