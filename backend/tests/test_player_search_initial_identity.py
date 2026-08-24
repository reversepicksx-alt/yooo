from routes.players import _initial_has_full_first_name_evidence


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