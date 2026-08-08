"""
Iteration 47: Test Square Change Plan Feature
Tests:
- POST /api/square/change-plan endpoint
- GET /api/square/status/{email} returns planLabel and cadence
- Validation: same plan rejection, invalid plan rejection, non-subscriber rejection
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
OWNER_EMAIL = "josselj001@gmail.com"
SQUARE_SUBSCRIBER_EMAIL = "xaviersteverson@gmail.com"
SQUARE_SUBSCRIBER_PASSWORD = "test123456"
LIFETIME_USER_EMAIL = "its2famous@gmail.com"
NON_SUBSCRIBER_EMAIL = "nonexistent_test_user_12345@example.com"


class TestSquareStatusEndpoint:
    """Test GET /api/square/status/{email} returns planLabel and cadence"""
    
    def test_status_returns_plan_label_and_cadence_for_subscriber(self):
        """Verify status endpoint returns planLabel and cadence for active subscriber"""
        resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        print(f"Status response for {SQUARE_SUBSCRIBER_EMAIL}: {data}")
        
        # Verify active subscription
        assert data.get("active") == True, f"Expected active=True, got {data.get('active')}"
        
        # Verify planLabel is populated (should be like "$11/week" or "$39.99/month")
        plan_label = data.get("planLabel", "")
        assert plan_label, f"planLabel should be populated, got: '{plan_label}'"
        assert "$" in plan_label, f"planLabel should contain price, got: '{plan_label}'"
        
        # Verify cadence is populated (WEEKLY, MONTHLY, or QUARTERLY)
        cadence = data.get("cadence", "")
        assert cadence in ["WEEKLY", "MONTHLY", "QUARTERLY"], f"cadence should be WEEKLY/MONTHLY/QUARTERLY, got: '{cadence}'"
        
        # Verify planKey is present
        plan_key = data.get("planKey", "")
        assert plan_key in ["weekly", "monthly", "quarterly"], f"planKey should be weekly/monthly/quarterly, got: '{plan_key}'"
        
        print(f"PASS: Status returns planLabel='{plan_label}', cadence='{cadence}', planKey='{plan_key}'")
    
    def test_status_returns_inactive_for_non_subscriber(self):
        """Verify status endpoint returns active=False for non-subscriber"""
        resp = requests.get(f"{BASE_URL}/api/square/status/{NON_SUBSCRIBER_EMAIL}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert data.get("active") == False, f"Expected active=False for non-subscriber, got {data}"
        print(f"PASS: Non-subscriber returns active=False")


class TestChangePlanValidation:
    """Test POST /api/square/change-plan validation cases"""
    
    def test_change_plan_rejects_invalid_plan(self):
        """Verify change-plan rejects invalid plan key"""
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": "invalid_plan_xyz"}
        )
        assert resp.status_code == 400, f"Expected 400 for invalid plan, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "invalid" in data.get("detail", "").lower(), f"Expected 'invalid' in error message, got: {data}"
        print(f"PASS: Invalid plan rejected with 400")
    
    def test_change_plan_rejects_non_subscriber(self):
        """Verify change-plan rejects user without active subscription"""
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": NON_SUBSCRIBER_EMAIL, "new_plan_key": "monthly"}
        )
        assert resp.status_code == 404, f"Expected 404 for non-subscriber, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "no active subscription" in data.get("detail", "").lower(), f"Expected 'no active subscription' in error, got: {data}"
        print(f"PASS: Non-subscriber rejected with 404")
    
    def test_change_plan_rejects_same_plan(self):
        """Verify change-plan rejects switching to same plan"""
        # First get current plan
        status_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert status_resp.status_code == 200
        current_plan_key = status_resp.json().get("planKey", "weekly")
        
        # Try to switch to same plan
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": current_plan_key}
        )
        assert resp.status_code == 400, f"Expected 400 for same plan, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        assert "already" in data.get("detail", "").lower(), f"Expected 'already' in error message, got: {data}"
        print(f"PASS: Same plan rejected with 400 (current plan: {current_plan_key})")


class TestChangePlanSuccess:
    """Test POST /api/square/change-plan successful swap"""
    
    def test_change_plan_success_swap(self):
        """Test successful plan change via Square API"""
        # First get current plan
        status_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        current_plan_key = status_data.get("planKey", "weekly")
        
        # Determine target plan (different from current)
        if current_plan_key == "weekly":
            target_plan = "monthly"
        elif current_plan_key == "monthly":
            target_plan = "quarterly"
        else:
            target_plan = "weekly"
        
        print(f"Attempting to change plan from {current_plan_key} to {target_plan}")
        
        # Attempt plan change
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": target_plan}
        )
        
        # Note: This may fail with "same as current plan" or "already scheduled" if a swap is already pending
        # That's expected behavior per the agent context note - Square queues plan changes for end of billing cycle
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("success") == True, f"Expected success=True, got {data}"
            assert data.get("new_plan"), f"Expected new_plan in response, got {data}"
            assert data.get("message"), f"Expected message in response, got {data}"
            print(f"PASS: Plan changed successfully - {data.get('message')}")
        elif resp.status_code == 400:
            data = resp.json()
            detail = data.get("detail", "").lower()
            # Expected if swap is already pending or scheduled
            if "pending" in detail or "same as current" in detail or "scheduled" in detail:
                print(f"EXPECTED BEHAVIOR: Plan change already pending/scheduled - {data.get('detail')}")
                # This is valid behavior - the endpoint works correctly, just can't swap again until billing cycle
            else:
                pytest.fail(f"Unexpected 400 error: {data}")
        else:
            pytest.fail(f"Unexpected status code {resp.status_code}: {resp.text}")


class TestChangePlanEndpointStructure:
    """Test change-plan endpoint request/response structure"""
    
    def test_change_plan_requires_email(self):
        """Verify change-plan requires email field"""
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"new_plan_key": "monthly"}
        )
        assert resp.status_code == 422, f"Expected 422 for missing email, got {resp.status_code}"
        print("PASS: Missing email returns 422")
    
    def test_change_plan_requires_new_plan_key(self):
        """Verify change-plan requires new_plan_key field"""
        resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL}
        )
        assert resp.status_code == 422, f"Expected 422 for missing new_plan_key, got {resp.status_code}"
        print("PASS: Missing new_plan_key returns 422")


class TestSquarePlansEndpoint:
    """Test GET /api/square/plans returns all plan options"""
    
    def test_plans_returns_all_options(self):
        """Verify plans endpoint returns weekly, monthly, quarterly options"""
        resp = requests.get(f"{BASE_URL}/api/square/plans")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        
        data = resp.json()
        plans = data.get("plans", [])
        
        # Should have 3 plans
        assert len(plans) >= 3, f"Expected at least 3 plans, got {len(plans)}"
        
        # Verify plan keys
        plan_keys = [p.get("key") for p in plans]
        assert "weekly" in plan_keys, f"Missing 'weekly' plan in {plan_keys}"
        assert "monthly" in plan_keys, f"Missing 'monthly' plan in {plan_keys}"
        assert "quarterly" in plan_keys, f"Missing 'quarterly' plan in {plan_keys}"
        
        # Verify each plan has required fields
        for plan in plans:
            assert plan.get("key"), f"Plan missing 'key': {plan}"
            assert plan.get("name"), f"Plan missing 'name': {plan}"
            assert plan.get("label"), f"Plan missing 'label': {plan}"
            assert plan.get("amount"), f"Plan missing 'amount': {plan}"
        
        print(f"PASS: Plans endpoint returns {len(plans)} plans with all required fields")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
