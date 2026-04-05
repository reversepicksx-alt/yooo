"""
Test Square Integration + Claude Removal (Iteration 35)
Tests:
1. AI Engine: verify only 3 AI models (no claude/haiku)
2. AI Engine: MIN_RESULTS = 2
3. Square plans API: GET /api/square/plans returns 3 plans
4. Square subscribe API: POST /api/square/subscribe endpoint exists
5. Square status API: GET /api/square/status/{email} returns active:false for unknown
6. Square cancel API: POST /api/square/cancel returns 404 for no subscription
7. Auth check_access: checks square_subscriptions before Whop
8. Health check: /api/health returns 200
"""
import pytest
import requests
import os
import re

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestHealthCheck:
    """Health check endpoint test"""
    
    def test_health_returns_200(self):
        """GET /api/health returns 200"""
        response = requests.get(f"{BASE_URL}/api/health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get("status") == "ok", f"Expected status=ok, got {data}"
        print("✅ Health check passed: /api/health returns 200 with status=ok")


class TestAIEngineClaudeRemoval:
    """Verify Claude/Haiku removed from AI engine"""
    
    def test_predict_py_has_only_3_models(self):
        """predict.py ai_tasks should have exactly 3 models (no claude)"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        # Find ai_tasks block
        ai_tasks_match = re.search(r'ai_tasks\s*=\s*\[(.*?)\]', content, re.DOTALL)
        assert ai_tasks_match, "ai_tasks not found in predict.py"
        ai_tasks_block = ai_tasks_match.group(1)
        
        # Count ensure_future calls (each model)
        model_count = ai_tasks_block.count('ensure_future')
        assert model_count == 3, f"Expected 3 models, found {model_count}"
        
        # Verify no claude/haiku
        assert 'claude' not in ai_tasks_block.lower(), "Claude found in ai_tasks"
        assert 'haiku' not in ai_tasks_block.lower(), "Haiku found in ai_tasks"
        
        # Verify expected models present
        assert 'gemini-2.0-flash' in ai_tasks_block, "gemini-2.0-flash not found"
        assert 'gpt-5.2' in ai_tasks_block, "gpt-5.2 not found"
        assert 'grok' in ai_tasks_block, "grok not found"
        
        print("✅ predict.py has exactly 3 AI models (no claude/haiku)")
    
    def test_basketball_predict_py_has_only_3_models(self):
        """basketball_predict.py ai_tasks should have exactly 3 models (no claude)"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        # Find ai_tasks block
        ai_tasks_match = re.search(r'ai_tasks\s*=\s*\[(.*?)\]', content, re.DOTALL)
        assert ai_tasks_match, "ai_tasks not found in basketball_predict.py"
        ai_tasks_block = ai_tasks_match.group(1)
        
        # Count ensure_future calls (each model)
        model_count = ai_tasks_block.count('ensure_future')
        assert model_count == 3, f"Expected 3 models, found {model_count}"
        
        # Verify no claude/haiku
        assert 'claude' not in ai_tasks_block.lower(), "Claude found in ai_tasks"
        assert 'haiku' not in ai_tasks_block.lower(), "Haiku found in ai_tasks"
        
        print("✅ basketball_predict.py has exactly 3 AI models (no claude/haiku)")
    
    def test_predict_py_min_results_is_2(self):
        """predict.py MIN_RESULTS should be 2 (not 3)"""
        with open('/app/backend/routes/predict.py', 'r') as f:
            content = f.read()
        
        min_results_match = re.search(r'MIN_RESULTS\s*=\s*(\d+)', content)
        assert min_results_match, "MIN_RESULTS not found in predict.py"
        min_results = int(min_results_match.group(1))
        assert min_results == 2, f"Expected MIN_RESULTS=2, got {min_results}"
        
        print("✅ predict.py MIN_RESULTS = 2")
    
    def test_basketball_predict_py_min_results_is_2(self):
        """basketball_predict.py MIN_RESULTS should be 2 (not 3)"""
        with open('/app/backend/routes/basketball_predict.py', 'r') as f:
            content = f.read()
        
        min_results_match = re.search(r'MIN_RESULTS\s*=\s*(\d+)', content)
        assert min_results_match, "MIN_RESULTS not found in basketball_predict.py"
        min_results = int(min_results_match.group(1))
        assert min_results == 2, f"Expected MIN_RESULTS=2, got {min_results}"
        
        print("✅ basketball_predict.py MIN_RESULTS = 2")


class TestSquarePlansAPI:
    """Test Square plans endpoint"""
    
    def test_get_plans_returns_3_plans(self):
        """GET /api/square/plans returns 3 plans with correct amounts"""
        response = requests.get(f"{BASE_URL}/api/square/plans")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        plans = data.get("plans", [])
        assert len(plans) >= 3, f"Expected at least 3 plans, got {len(plans)}"
        
        # Verify plan keys exist
        plan_keys = [p.get("key") for p in plans]
        assert "weekly" in plan_keys, "weekly plan not found"
        assert "monthly" in plan_keys, "monthly plan not found"
        assert "quarterly" in plan_keys, "quarterly plan not found"
        
        # Verify amounts (in cents)
        plan_amounts = {p.get("key"): p.get("amount") for p in plans}
        assert plan_amounts.get("weekly") == 1100, f"Weekly should be 1100 cents, got {plan_amounts.get('weekly')}"
        assert plan_amounts.get("monthly") == 3999, f"Monthly should be 3999 cents, got {plan_amounts.get('monthly')}"
        assert plan_amounts.get("quarterly") == 9999, f"Quarterly should be 9999 cents, got {plan_amounts.get('quarterly')}"
        
        print(f"✅ GET /api/square/plans returns 3 plans with correct amounts: {plan_amounts}")


class TestSquareSubscribeAPI:
    """Test Square subscribe endpoint"""
    
    def test_subscribe_endpoint_exists(self):
        """POST /api/square/subscribe endpoint exists and validates input"""
        # Send invalid request to verify endpoint exists
        response = requests.post(
            f"{BASE_URL}/api/square/subscribe",
            json={"email": "test@test.com"}  # Missing required fields
        )
        # Should return 422 (validation error) not 404
        assert response.status_code == 422, f"Expected 422 validation error, got {response.status_code}"
        print("✅ POST /api/square/subscribe endpoint exists and validates input")
    
    def test_subscribe_validates_plan_key(self):
        """POST /api/square/subscribe validates planKey"""
        response = requests.post(
            f"{BASE_URL}/api/square/subscribe",
            json={
                "email": "test@test.com",
                "firstName": "Test",
                "lastName": "User",
                "sourceId": "fake-token",
                "planKey": "invalid_plan",
                "password": "test123"
            }
        )
        # Should return 400 for invalid plan
        assert response.status_code == 400, f"Expected 400 for invalid plan, got {response.status_code}"
        data = response.json()
        assert "invalid plan" in data.get("detail", "").lower(), f"Expected invalid plan error, got {data}"
        print("✅ POST /api/square/subscribe validates planKey")


class TestSquareStatusAPI:
    """Test Square status endpoint"""
    
    def test_status_returns_inactive_for_unknown_email(self):
        """GET /api/square/status/{email} returns active:false for unknown email"""
        test_email = "nonexistent_user_12345@test.com"
        response = requests.get(f"{BASE_URL}/api/square/status/{test_email}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        assert data.get("active") == False, f"Expected active=false, got {data}"
        print(f"✅ GET /api/square/status/{test_email} returns active:false")


class TestSquareCancelAPI:
    """Test Square cancel endpoint"""
    
    def test_cancel_returns_404_for_no_subscription(self):
        """POST /api/square/cancel returns 404 for email with no subscription"""
        test_email = "nonexistent_user_12345@test.com"
        response = requests.post(
            f"{BASE_URL}/api/square/cancel",
            json={"email": test_email}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        
        data = response.json()
        assert "no active subscription" in data.get("detail", "").lower(), f"Expected no subscription error, got {data}"
        print(f"✅ POST /api/square/cancel returns 404 for email with no subscription")


class TestAuthCheckAccess:
    """Test auth check_access function checks Square before Whop"""
    
    def test_auth_py_checks_square_before_whop(self):
        """check_access function checks square_subscriptions before Whop"""
        with open('/app/backend/routes/auth.py', 'r') as f:
            content = f.read()
        
        # Find check_access function
        assert 'async def check_access' in content, "check_access function not found"
        
        # Find the function body
        func_start = content.find('async def check_access')
        func_end = content.find('\nasync def ', func_start + 1)
        if func_end == -1:
            func_end = content.find('\n@router', func_start + 1)
        func_body = content[func_start:func_end]
        
        # Verify square_subscriptions is checked
        assert 'square_subscriptions' in func_body, "square_subscriptions not checked in check_access"
        
        # Verify Square check comes before Whop check
        square_pos = func_body.find('square_subscriptions')
        whop_pos = func_body.find('fetch_whop_memberships')
        assert square_pos < whop_pos, f"Square check should come before Whop check (square_pos={square_pos}, whop_pos={whop_pos})"
        
        print("✅ check_access checks square_subscriptions before Whop")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
