import json

from fotmob_client import _player_stats_from_page


def test_fotmob_player_pass_attempts_are_the_fraction_denominator():
    page = {
        "props": {
            "pageProps": {
                "content": {
                    "playerStats": {
                        "907723": {
                            "name": "Sergio Barreto",
                            "id": 907723,
                            "teamName": "Pachuca",
                            "stats": [
                                {
                                    "title": "Top stats",
                                    "stats": {
                                        "Minutes played": {
                                            "key": "minutes_played",
                                            "stat": {"value": 90},
                                        },
                                        "Accurate passes": {
                                            "key": "accurate_passes",
                                            "stat": {
                                                "value": 45,
                                                "total": 53,
                                                "type": "fractionWithPercentage",
                                            },
                                        },
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        }
    }
    page_html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(page)
        + "</script>"
    )

    row = _player_stats_from_page(
        page_html,
        "Sergio Damián Barreto",
        "Pachuca",
    )

    assert row is not None
    assert row["providerPlayerId"] == 907723
    player = row["player"]
    accurate_passes = next(
        entry["stats"]["Accurate passes"]["stat"]
        for entry in player["stats"]
        if entry["title"] == "Top stats"
    )
    assert accurate_passes["value"] == 45
    assert accurate_passes["total"] == 53