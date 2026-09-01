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


def test_liga_mx_board_players_use_verified_identities_with_or_without_accents():
    andres_ascii = _verified_identity_search_override("Andres Sanchez")
    andres_accented = _verified_identity_search_override("Andrés Sánchez")
    luis_ascii = _verified_identity_search_override("Luis Cardenas")
    luis_accented = _verified_identity_search_override("Luis Cárdenas")

    assert andres_ascii[0]["id"] == andres_accented[0]["id"] == 182656
    assert andres_ascii[0]["teamId"] == 2314
    assert luis_ascii[0]["id"] == luis_accented[0]["id"] == 35536
    assert luis_ascii[0]["teamId"] == 2282


def test_rodri_does_not_resolve_to_a_rodriguez_substring():
    result = _verified_identity_search_override("Rodri")
    assert len(result) == 1
    assert result[0]["id"] == 44
    assert result[0]["teamId"] == 50