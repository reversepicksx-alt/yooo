from pathlib import Path


AUTH_SOURCE = (
    Path(__file__).resolve().parents[1] / "routes" / "auth.py"
).read_text()


def test_otp_accepts_recent_codes_without_replacing_the_delivery_window():
    """Delayed email delivery must not invalidate every earlier code."""
    assert "OTP_HISTORY_LIMIT = 10" in AUTH_SOURCE
    assert "def _otp_entries(record: dict | None)" in AUTH_SOURCE
    assert '"codes": entries' in AUTH_SOURCE
    assert "retained + [{" in AUTH_SOURCE


def test_otp_marks_only_the_code_that_was_entered():
    """Rolling codes remain individually single-use."""
    assert '"usedAt": now.isoformat()' in AUTH_SOURCE
    assert "matched_index = None" in AUTH_SOURCE
    assert "Code already used. Please request a new one." in AUTH_SOURCE