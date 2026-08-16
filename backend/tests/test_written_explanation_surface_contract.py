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


def test_empty_or_failed_analyst_text_cannot_blank_the_fallback():
    assert "The analyst read returned no text." in SCAN_SOURCE
    assert "Refining the written read from the verified numbers…" in SCAN_SOURCE
    assert "onPress={onRetry}" in SCAN_SOURCE
    assert "TRY AGAIN" in SCAN_SOURCE


def test_section_requests_are_started_for_read_and_each_selected_tab():
    assert "void requestSectionExplanation('read', predictionState);" in SCAN_SOURCE
    assert "void requestSectionExplanation(tab, predictionState);" in SCAN_SOURCE