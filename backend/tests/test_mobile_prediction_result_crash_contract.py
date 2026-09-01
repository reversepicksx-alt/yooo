from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN_SOURCE = (ROOT / "mobile/app/(tabs)/scan.tsx").read_text()
API_SOURCE = (ROOT / "mobile/lib/api.ts").read_text()


def test_prediction_result_does_not_start_a_native_reanimated_entering_animation():
    """The result screen must not invoke a native worklet at prediction completion."""
    assert "from 'react-native-reanimated'" not in SCAN_SOURCE
    assert "<Reanimated.View" not in SCAN_SOURCE
    assert "FadeInDown" not in SCAN_SOURCE


def test_prediction_result_treats_optional_log_payloads_as_arrays():
    """Provider shape drift must not turn result rendering into an uncaught .map/.filter."""
    assert "Array.isArray(prediction.gameLogs)" in SCAN_SOURCE
    assert "Array.isArray(raw.matchLogs)" in API_SOURCE
    assert API_SOURCE.count("Array.isArray(raw.gameLogs)") >= 5


def test_prediction_result_formats_malformed_numeric_values_safely():
    assert "function safeFixed(value: unknown, digits = 1): string" in SCAN_SOURCE
    assert "safeFixed(prediction.projection ?? prediction.bayesianProjection)" in SCAN_SOURCE
    assert "safeFixed(prediction.priorMean)" in SCAN_SOURCE