from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREDICT_SOURCE = (ROOT / "backend/routes/predict.py").read_text()
API_SOURCE = (ROOT / "mobile/lib/api.ts").read_text()
SCAN_SOURCE = (ROOT / "mobile/app/(tabs)/scan.tsx").read_text()


def test_crosses_prediction_is_blocked_before_history_lookup():
    assert 'if req.sport == "soccer" and req.propType == "crosses":' in PREDICT_SOURCE
    assert "Crosses are not available in the verified soccer player-stat " in PREDICT_SOURCE
    assert "feed yet." in PREDICT_SOURCE
    assert "Choose Pass Attempts, Passes, or Key Passes instead." in PREDICT_SOURCE


def test_crosses_is_not_a_new_manual_prop_choice():
    assert "{ value: 'crosses'" not in API_SOURCE


def test_scanned_crosses_are_not_silently_remapped_to_pass_attempts():
    assert "const detectedUnsupportedProp = scanned.propType === 'crosses';" in SCAN_SOURCE
    assert "? 'crosses'" in SCAN_SOURCE
    assert "Crosses are unavailable — choose a supported prop below" in SCAN_SOURCE