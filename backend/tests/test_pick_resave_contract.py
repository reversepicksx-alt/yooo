"""Regression coverage for saving revised prediction snapshots."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PICKS_SOURCE = (ROOT / "routes" / "picks.py").read_text()


def test_explicit_save_allows_another_snapshot_for_the_same_prediction():
    save_source = PICKS_SOURCE.split(
        "async def save_pick", 1
    )[1].split(
        "async def list_picks", 1
    )[0]

    assert "Duplicate-pick guard" not in save_source
    assert "Delete it first if you want to re-pick." not in save_source
    assert "Every explicit Save Pick creates a snapshot" in save_source
    assert '{"pickId": pick_id, "email": req.email.lower()}' in save_source


def test_delete_remains_a_soft_hide_and_cannot_block_a_later_resave():
    delete_source = PICKS_SOURCE.split(
        "async def delete_pick", 1
    )[1].split(
        "async def correct_pick", 1
    )[0]
    assert '"hiddenFromUser": True' in delete_source
    assert "Duplicate-pick guard" not in PICKS_SOURCE