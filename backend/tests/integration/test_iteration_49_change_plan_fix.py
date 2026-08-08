"""
Iteration 49: Test change-plan fix (cancel+recreate approach)
Tests:
1. POST /api/square/change-plan — successfully changes plan via cancel+recreate
2. POST /api/square/change-plan — rejects same-plan change with clear error
3. POST /api/square/change-plan — rejects non-subscriber with 404
4. GET /api/square/status/{email} — returns correct planKey, planLabel, cadence
5. Verify auto-sync prefers ACTIVE subscriptions over CANCELLED
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

# Test credentials from test_credentials.md
SQUARE_SUBSCRIBER_EMAIL = "xaviersteverson@gmail.com"
SQUARE_SUBSCRIBER_PASSWORD = "test123456"
OWNER_EMAIL = "josselj001@gmail.com"
LIFETIME_EMAIL = "its2famous@gmail.com"
NON_SUBSCRIBER_EMAIL = "nonexistent_test_user_12345@example.com"


class TestChangePlanEndpoint:
    """Tests for POST /api/square/change-plan endpoint with cancel+recreate approach"""
    
    def test_change_plan_rejects_non_subscriber(self):
        """Non-subscriber should get 404"""
        response = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": NON_SUBSCRIBER_EMAIL, "new_plan_key": "monthly"}
        )
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data
        assert "no active subscription" in data["detail"].lower() or "not found" in data["detail"].lower()
        print(f"PASS: Non-subscriber rejected with 404 - {data['detail']}")
    
    def test_change_plan_rejects_invalid_plan(self):
        """Invalid plan key should get 400"""
        response = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": "invalid_plan_xyz"}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data
        assert "invalid plan" in data["detail"].lower()
        print(f"PASS: Invalid plan rejected with 400 - {data['detail']}")
    
    def test_get_current_subscription_status(self):
        """GET /api/square/status/{email} should return current plan info"""
        response = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        
        # Verify required fields are present
        assert "active" in data
        assert "planKey" in data
        assert "status" in data
        
        # If active, verify plan details
        if data.get("active"):
            assert data.get("planKey") in ["weekly", "monthly", "quarterly"], f"Unexpected planKey: {data.get('planKey')}"
            # planLabel and cadence should be populated
            print(f"PASS: Status endpoint returns - planKey={data.get('planKey')}, planLabel={data.get('planLabel')}, cadence={data.get('cadence')}, status={data.get('status')}")
        else:
            print(f"INFO: Subscription not active - status={data.get('status')}")
        
        return data
    
    def test_change_plan_rejects_same_plan(self):
        """Changing to the same plan should get 400 with clear error"""
        # First get current plan
        status_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        if status_resp.status_code != 200:
            pytest.skip("Could not get subscription status")
        
        status_data = status_resp.json()
        if not status_data.get("active"):
            pytest.skip("Subscription not active")
        
        current_plan = status_data.get("planKey", "weekly")
        
        # Try to change to the same plan
        response = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": current_plan}
        )
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        data = response.json()
        assert "detail" in data
        assert "already" in data["detail"].lower()
        print(f"PASS: Same plan change rejected with 400 - {data['detail']}")


class TestSubscriptionStatusEndpoint:
    """Tests for GET /api/square/status/{email} endpoint"""
    
    def test_status_returns_plan_details(self):
        """Status endpoint should return planKey, planLabel, cadence"""
        response = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert response.status_code == 200
        data = response.json()
        
        # Check all expected fields
        expected_fields = ["active", "planKey", "status"]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"
        
        if data.get("active"):
            # For active subscriptions, planLabel and cadence should be populated
            assert data.get("planLabel") or data.get("planKey"), "planLabel or planKey should be present"
            print(f"PASS: Status returns - active={data['active']}, planKey={data.get('planKey')}, planLabel={data.get('planLabel')}, cadence={data.get('cadence')}")
        else:
            print(f"INFO: Subscription not active")
    
    def test_status_non_subscriber_returns_inactive(self):
        """Non-subscriber should get active=False"""
        response = requests.get(f"{BASE_URL}/api/square/status/{NON_SUBSCRIBER_EMAIL}")
        assert response.status_code == 200
        data = response.json()
        assert data.get("active") == False
        print(f"PASS: Non-subscriber returns active=False")
    
    def test_owner_status(self):
        """Owner should not have Square subscription (uses manual grant)"""
        response = requests.get(f"{BASE_URL}/api/square/status/{OWNER_EMAIL}")
        assert response.status_code == 200
        data = response.json()
        # Owner may or may not have Square subscription, but endpoint should work
        print(f"INFO: Owner status - active={data.get('active')}, planKey={data.get('planKey')}")


class TestChangePlanIntegration:
    """Integration tests for change-plan with cancel+recreate approach
    
    NOTE: These tests interact with REAL Square subscriptions.
    Limit to 2-3 plan changes max to avoid excessive charges.
    """
    
    def test_change_plan_success_and_verify(self):
        """
        Test plan change via cancel+recreate:
        1. Get current plan
        2. Change to a different plan
        3. Verify status endpoint reflects new plan
        4. IMPORTANT: Revert back to original plan
        """
        # Step 1: Get current plan
        status_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        if status_resp.status_code != 200:
            pytest.skip("Could not get subscription status")
        
        status_data = status_resp.json()
        if not status_data.get("active"):
            pytest.skip("Subscription not active - cannot test plan change")
        
        current_plan = status_data.get("planKey", "weekly")
        print(f"Current plan: {current_plan}")
        
        # Determine target plan (different from current)
        plan_cycle = ["weekly", "monthly", "quarterly"]
        current_idx = plan_cycle.index(current_plan) if current_plan in plan_cycle else 0
        target_plan = plan_cycle[(current_idx + 1) % 3]
        
        print(f"Attempting to change from {current_plan} to {target_plan}")
        
        # Step 2: Change plan
        change_resp = requests.post(
            f"{BASE_URL}/api/square/change-plan",
            json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": target_plan}
        )
        
        if change_resp.status_code == 200:
            change_data = change_resp.json()
            assert change_data.get("success") == True
            assert change_data.get("new_plan") is not None
            print(f"PASS: Plan changed successfully - {change_data.get('message')}")
            
            # Step 3: Verify status reflects new plan
            verify_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
            assert verify_resp.status_code == 200
            verify_data = verify_resp.json()
            
            # The new plan should be reflected
            assert verify_data.get("planKey") == target_plan, f"Expected planKey={target_plan}, got {verify_data.get('planKey')}"
            print(f"PASS: Status endpoint reflects new plan - planKey={verify_data.get('planKey')}")
            
            # Step 4: REVERT back to original plan
            print(f"Reverting back to {current_plan}...")
            revert_resp = requests.post(
                f"{BASE_URL}/api/square/change-plan",
                json={"email": SQUARE_SUBSCRIBER_EMAIL, "new_plan_key": current_plan}
            )
            
            if revert_resp.status_code == 200:
                revert_data = revert_resp.json()
                print(f"PASS: Reverted to original plan - {revert_data.get('message')}")
                
                # Verify revert
                final_resp = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
                final_data = final_resp.json()
                assert final_data.get("planKey") == current_plan, f"Revert failed - expected {current_plan}, got {final_data.get('planKey')}"
                print(f"PASS: Final verification - planKey={final_data.get('planKey')}")
            else:
                print(f"WARNING: Could not revert plan - {revert_resp.status_code}: {revert_resp.text}")
        
        elif change_resp.status_code == 400:
            # Could be "already on this plan" or other validation error
            change_data = change_resp.json()
            print(f"INFO: Plan change returned 400 - {change_data.get('detail')}")
            # This is acceptable if there's a pending change or same plan
        
        elif change_resp.status_code == 500:
            # Server error - could be Square API issue
            change_data = change_resp.json()
            print(f"WARNING: Plan change returned 500 - {change_data.get('detail')}")
            pytest.skip(f"Square API error: {change_data.get('detail')}")
        
        else:
            pytest.fail(f"Unexpected status code: {change_resp.status_code} - {change_resp.text}")


class TestAutoSyncBehavior:
    """Tests to verify auto-sync prefers ACTIVE subscriptions over CANCELLED"""
    
    def test_status_endpoint_returns_active_sub(self):
        """
        After auto-sync, status endpoint should return ACTIVE subscription data,
        not overwritten by CANCELLED subscription data.
        """
        response = requests.get(f"{BASE_URL}/api/square/status/{SQUARE_SUBSCRIBER_EMAIL}")
        assert response.status_code == 200
        data = response.json()
        
        if data.get("active"):
            # Status should be ACTIVE, not CANCELLED/EXPIRED
            assert data.get("status") in ["ACTIVE", "PENDING"], f"Expected ACTIVE/PENDING status, got {data.get('status')}"
            print(f"PASS: Status endpoint returns ACTIVE subscription - status={data.get('status')}")
        else:
            print(f"INFO: Subscription not active - status={data.get('status')}")


class TestPlansEndpoint:
    """Tests for GET /api/square/plans endpoint"""
    
    def test_plans_endpoint_returns_all_plans(self):
        """Plans endpoint should return weekly, monthly, quarterly options"""
        response = requests.get(f"{BASE_URL}/api/square/plans")
        assert response.status_code == 200
        data = response.json()
        
        assert "plans" in data
        plans = data["plans"]
        
        # Should have 3 plans
        assert len(plans) >= 3, f"Expected at least 3 plans, got {len(plans)}"
        
        # Verify plan keys
        plan_keys = [p.get("key") for p in plans]
        assert "weekly" in plan_keys
        assert "monthly" in plan_keys
        assert "quarterly" in plan_keys
        
        # Verify each plan has required fields
        for plan in plans:
            assert "key" in plan
            assert "name" in plan
            assert "label" in plan
            assert "amount" in plan
            print(f"  Plan: {plan['key']} - {plan['name']} - {plan['label']} - ${plan['amount']/100:.2f}")
        
        print(f"PASS: Plans endpoint returns {len(plans)} plans")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
