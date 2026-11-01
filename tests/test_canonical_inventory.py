"""Unit tests for canonical system inventory."""
from betting_engine.canonical_inventory import get_canonical_system_inventory


def test_canonical_system_inventory_structure(tmp_path):
    inv = get_canonical_system_inventory(data_dir=tmp_path)
    assert isinstance(inv, dict)
    assert inv["status"] == "healthy"
    assert "market_universe" in inv
    assert "stake_live_portfolio" in inv
    assert "engine_ledger" in inv
    assert "performance_and_directives" in inv
    assert (tmp_path / "canonical_system_inventory.json").exists()
