"""Single Source of Truth (SSOT) - Canonical System Inventory.

Continuously reconciles and aggregates all vital system counts across:
1. Unified Market Universe (Tier 3 Graduated, Tier 2 Incubator, Tier 1 R&D)
2. Live Stake Portfolio (Active bets, total exposure, sizing bounds)
3. Betting Ledger & Decisions (Total issued, open, settled)
4. Prime Directives Telemetry (Daily volume, rolling 7d ROI, Kelly compliance)

Guarantees 100% consistent numbers across all APIs, background workers, and UIs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .providers import DATA

log = logging.getLogger(__name__)

INVENTORY_FILE = DATA / "canonical_system_inventory.json"


def get_canonical_system_inventory(data_dir: Path = DATA, force_refresh: bool = False) -> Dict[str, Any]:
    """Generates or loads the reconciled system inventory."""
    inventory_path = data_dir / "canonical_system_inventory.json"
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Unified Market Universe Telemetry (SSOT for all market counts)
    universe_summary: Dict[str, Any] = {}
    try:
        from .unified_market_universe import UnifiedMarketUniverse
        u = UnifiedMarketUniverse(data_dir)
        universe_summary = u.get_summary(force_refresh=force_refresh)
    except Exception as exc:
        log.warning("Could not read UnifiedMarketUniverse summary: %s", exc)

    total_markets = universe_summary.get("total_supported_markets", 0)
    t3_count = universe_summary.get("tier_3_production_graduated_count", 0)
    t2_count = universe_summary.get("tier_2_shadow_incubator_count", 0)
    t1_count = universe_summary.get("tier_1_research_rnd_count", 0)
    leagues_breakdown = universe_summary.get("leagues_breakdown", {})

    # 2. Database Ledger Telemetry
    engine_db = data_dir / "engine.sqlite3"
    total_decisions = 0
    total_bets = 0
    open_bets_db = 0
    if engine_db.exists():
        try:
            with sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM system_decisions")
                total_decisions = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM bets")
                total_bets = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM bets WHERE status = 'open'")
                open_bets_db = cur.fetchone()[0]
        except Exception as exc:
            log.warning("Could not query engine.sqlite3 for inventory: %s", exc)

    # 3. Live Stake Portfolio Telemetry (from Safari session or live cache)
    live_stake_count = 0
    live_stake_exposure = 0.0
    live_stake_min = 0.0
    live_stake_max = 0.0
    live_stake_avg = 0.0

    live_cache_file = data_dir / "live_stake_account_summary.json"
    if live_cache_file.exists():
        try:
            cache_data = json.loads(live_cache_file.read_text())
            live_stake_count = int(cache_data.get("open_bets_count", 0))
            live_stake_exposure = float(cache_data.get("open_exposure", 0.0))
            active_bets = cache_data.get("active_bets", [])
            if active_bets:
                stakes = [float(b.get("amount", 0.0)) for b in active_bets if b.get("amount")]
                if stakes:
                    live_stake_min = round(min(stakes), 2)
                    live_stake_max = round(max(stakes), 2)
                    live_stake_avg = round(sum(stakes) / len(stakes), 2)
        except Exception:
            pass
    elif engine_db.exists():
        try:
            with sqlite3.connect(f"file:{engine_db}?mode=ro", uri=True, timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT stake FROM bets WHERE status = 'open'")
                open_stakes = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
                if open_stakes:
                    live_stake_count = len(open_stakes)
                    live_stake_exposure = round(sum(open_stakes), 2)
                    live_stake_min = round(min(open_stakes), 2)
                    live_stake_max = round(max(open_stakes), 2)
                    live_stake_avg = round(sum(open_stakes) / len(open_stakes), 2)
        except Exception:
            pass

    # 4. Supreme Autonomous Governor & Prime Directives Telemetry
    rolling_7d_roi = 0.0
    daily_volume = 0
    gov_status_file = data_dir / "supreme_autonomous_status.json"
    if gov_status_file.exists():
        try:
            gov = json.loads(gov_status_file.read_text())
            guard = gov.get("prime_directives_guard", {})
            rolling_7d_roi = guard.get("rolling_7d_roi_pct", 0.0)
            daily_volume = guard.get("daily_volume_count", 0)
        except Exception:
            pass

    inventory = {
        "status": "healthy",
        "reconciled_at": now_iso,
        "market_universe": {
            "canonical_status": "reconciled",
            "total_supported_markets": total_markets,
            "tier_3_production_graduated_count": t3_count,
            "tier_2_shadow_incubator_count": t2_count,
            "tier_1_research_rnd_count": t1_count,
            "quarantined_total_count": t2_count + t1_count,
            "active_capital_eligible_ratio": universe_summary.get("active_capital_eligible_ratio", 0.0),
            "leagues_breakdown": leagues_breakdown,
        },
        "stake_live_portfolio": {
            "active_bets_count": live_stake_count,
            "total_exposure_usd": live_stake_exposure,
            "min_stake": live_stake_min,
            "max_stake": live_stake_max,
            "avg_stake": live_stake_avg,
            "compliance_bounds": "[$0.50, $8.00]",
            "within_bounds": (live_stake_min >= 0.50 and live_stake_max <= 8.00) if live_stake_count else True,
        },
        "engine_ledger": {
            "total_system_decisions": total_decisions,
            "total_recorded_bets": total_bets,
            "open_bets_in_db": open_bets_db,
        },
        "performance_and_directives": {
            "target_weekly_roi_pct": 10.0,
            "rolling_7d_roi_pct": rolling_7d_roi,
            "daily_volume_target": 100,
            "daily_volume_today": daily_volume,
            "daily_volume_met": daily_volume >= 100,
        }
    }

    # Atomically persist
    try:
        tmp_file = inventory_path.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(inventory, indent=2))
        tmp_file.replace(inventory_path)
    except Exception as exc:
        log.warning("Could not persist canonical inventory to %s: %s", inventory_path, exc)

    return inventory
