"""
Tests for /api/admin/list-grants request shape.

Verifies that the endpoint accepts only the owner authentication fields
(email + token) and does not require the unused key/value fields that
AdminSettingsRequest carries.
"""
import pytest
from pydantic import ValidationError

from models import ListGrantsRequest, AdminSettingsRequest


class TestListGrantsRequest:
    """ListGrantsRequest must only require email and token."""

    def test_valid_with_email_and_token_only(self):
        req = ListGrantsRequest(email="owner@example.com", token="abc123")
        assert req.email == "owner@example.com"
        assert req.token == "abc123"

    def test_missing_email_raises(self):
        with pytest.raises(ValidationError):
            ListGrantsRequest(token="abc123")

    def test_missing_token_raises(self):
        with pytest.raises(ValidationError):
            ListGrantsRequest(email="owner@example.com")

    def test_no_key_field(self):
        """ListGrantsRequest must not expose a 'key' field."""
        req = ListGrantsRequest(email="owner@example.com", token="abc123")
        assert not hasattr(req, "key"), "key field should not be present"

    def test_no_value_field(self):
        """ListGrantsRequest must not expose a 'value' field."""
        req = ListGrantsRequest(email="owner@example.com", token="abc123")
        assert not hasattr(req, "value"), "value field should not be present"

    def test_admin_settings_request_still_requires_key_and_value(self):
        """AdminSettingsRequest contract is unchanged."""
        with pytest.raises(ValidationError):
            AdminSettingsRequest(email="owner@example.com", token="abc123")

    def test_admin_settings_request_accepts_all_four_fields(self):
        req = AdminSettingsRequest(
            email="owner@example.com",
            token="abc123",
            key="SOME_KEY",
            value="some_value",
        )
        assert req.key == "SOME_KEY"
        assert req.value == "some_value"
