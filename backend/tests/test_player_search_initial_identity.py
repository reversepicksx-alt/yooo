from routes.players import (
    _initial_has_full_first_name_evidence,
    _verified_identity_search_override,
)


def test_full_query_rejects_initial_only_same_surname_candidate():
    assert not _initial_has_full_first_name_evidence(
        {
            "name": "A. Gudmundsson",
            "fullName": "A. Gudmundsson",
            "firstname": "",
        },
        "Albert",
    )


def test_full_query_accepts_provider_backed_abbreviated_name():
    assert _initial_has_full_first_name_evidence(
        {
            "name": "J. David",
            "fullName": "J. David",
            "firstname": "Jonathan",
        },
        "Jonathan",
    )


def test_explicit_initial_query_remains_supported():
    assert _initial_has_full_first_name_evidence(
        {"name": "A. Gudmundsson", "fullName": "A. Gudmundsson"},
        "A",
    )


def test_albert_gudmundsson_uses_verified_fiorentina_identity():
    result = _verified_identity_search_override("Albert Gudmundsson")
    assert len(result) == 1
    assert result[0]["id"] == 2799
    assert result[0]["teamId"] == 502
    assert result[0]["teamName"] == "Fiorentina"
    assert result[0]["position"] == "Attacker"