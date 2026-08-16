from pathlib import Path


SCAN_SOURCE = (
    Path(__file__).resolve().parents[2] / "mobile" / "app" / "(tabs)" / "scan.tsx"
).read_text()


def test_each_section_has_an_immediate_deterministic_fallback():
    assert "function buildImmediateSectionExplanation(" in SCAN_SOURCE
    assert "const [sectionNarratives" in SCAN_SOURCE
    assert "current[tab] ? current : { ...current, [tab]: immediate }" in SCAN_SOURCE
    assert "{label} EXPLANATION" in SCAN_SOURCE
    assert "section === 'read' ? 'READ'" in SCAN_SOURCE
    assert "section === 'form' ? 'FORM'" in SCAN_SOURCE


def test_first_written_read_is_committed_and_not_replaced_async():
    assert "The first grounded read is the final read for this prediction." in SCAN_SOURCE
    assert "sectionNarrativeRef.current[tab] = immediate" in SCAN_SOURCE
    assert "asynchronous analyst generation" in SCAN_SOURCE
    assert "requestPredictionSectionExplanation(" not in SCAN_SOURCE
    assert "generatedText" not in SCAN_SOURCE


def test_sections_share_the_same_stable_prediction_identity():
    assert "const predictionExplanationIdentity = prediction" in SCAN_SOURCE
    assert "prediction.fixtureId ?? ''" in SCAN_SOURCE
    assert "prediction.playerId ?? ''" in SCAN_SOURCE
    assert "void requestSectionExplanation('read', predictionState);" in SCAN_SOURCE
    assert "void requestSectionExplanation(tab, predictionState);" in SCAN_SOURCE
    assert "predictionState, requestSectionExplanation" not in SCAN_SOURCE