"""Polymarket Unified Outcome Tracking and Automatic Settlement Engine.

Supervises all executed Polymarket orders, correlates them with verified official
match results (ESPN, NHL API, MLB Stats API), evaluates binary contract payoff
rules ($1.00 per winning share, $0.00 per losing share, or refund on push/cancellation),
atomically records realized net PnL in SQLite, and emits telemetry events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .contracts import contract_value
from .domain import utcnow
from .providers import DATA

log = logging.getLogger(__name__)

ORDER_LOG_FILE = DATA / "logs" / "polymarket_orders.jsonl"


class PolymarketOutcomeSettler:
    """Supervises and settles executed Polymarket orders against verified match outcomes."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        log_path: Optional[Path] = None,
        proxy_address: Optional[str] = None,
    ):
        self.db_path = Path(db_path) if db_path else (DATA / "engine.sqlite3")
        self.log_path = Path(log_path) if log_path else ORDER_LOG_FILE
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.proxy_address = proxy_address or os.getenv("POLYMARKET_PROXY")

    def fetch_polymarket_positions(self) -> Dict[str, Dict[str, Any]]:
        """Fetches official live account positions directly from Polymarket Data API.
        
        Indexed by token ID ('asset'). Provides exact dollar amounts, cash PnL,
        redemption status, and oracle resolution directly from Polymarket.
        """
        if not self.proxy_address:
            return {}
        url = f"https://data-api.polymarket.com/positions?user={self.proxy_address}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                positions = json.loads(r.read().decode())
                return {p.get("asset"): p for p in positions if isinstance(p, dict) and p.get("asset")}
        except Exception as exc:
            log.warning(f"Could not fetch Polymarket positions from Data API: {exc}")
            return {}

    def fetch_recent_redemptions(self) -> List[Dict[str, Any]]:
        """Fetches recent REDEEM activities and on-chain payout transaction hashes."""
        if not self.proxy_address:
            return []
        url = f"https://data-api.polymarket.com/activity?user={self.proxy_address}&limit=20"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                activities = json.loads(r.read().decode())
                return [a for a in activities if isinstance(a, dict) and a.get("type") == "REDEEM"]
        except Exception as exc:
            log.warning(f"Could not fetch Polymarket redemptions: {exc}")
            return []

    def settle_orders(
        self,
        order_ids: Optional[List[str]] = None,
        mock_scores: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Any]:
        """Settles pending Polymarket orders.

        Dual-Pillar Resolution:
        1. Primary: Direct Polymarket Data API & On-Chain Oracle resolution
           (Reads exact cash PnL, redemption status and payout straight from Polymarket).
        2. Fallback: Verified official sports feeds (ESPN, NHL API) for immediate settlement.
        """
        if not self.db_path.exists():
            return {
                "settled_count": 0,
                "won_count": 0,
                "lost_count": 0,
                "push_count": 0,
                "total_pnl": 0.0,
                "total_turnover": 0.0,
                "roi": 0.0,
            }

        settled_results: List[Dict[str, Any]] = []
        now_iso = utcnow().isoformat()

        # Step 0: Sync live on-chain positions from Polymarket Data API
        poly_positions = self.fetch_polymarket_positions() if not mock_scores else {}

        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                query = "SELECT * FROM polymarket_orders WHERE status IN ('filled', 'open') AND (settled_at IS NULL OR pnl IS NULL)"
                params: List[Any] = []
                if order_ids:
                    placeholders = ",".join("?" for _ in order_ids)
                    query += f" AND (order_id IN ({placeholders}) OR client_order_id IN ({placeholders}))"
                    params.extend(order_ids)
                    params.extend(order_ids)

                rows = cur.execute(query, params).fetchall()

                for row in rows:
                    oid = row["order_id"] or row["client_order_id"]
                    token_id = row["token_id"]
                    game_title = row["game_title"]
                    market_fam = row["market_family"]
                    side = row["side"]
                    line = row["line"]
                    stake = float(row["stake_usd"])
                    shares = float(row["shares_count"])

                    new_status: Optional[str] = None
                    pnl: Optional[float] = None
                    settlement_source = "unknown"

                    # ---------------------------------------------------------
                    # Pillar 1: Direct Polymarket Data API & On-Chain Oracle
                    # ---------------------------------------------------------
                    if token_id in poly_positions:
                        pos = poly_positions[token_id]
                        cur_price = float(pos.get("curPrice", -1.0))
                        is_redeemable = bool(pos.get("redeemable"))
                        pct_pnl = float(pos.get("percentPnl", 0.0))
                        official_cash_pnl = pos.get("cashPnl")

                        # If market resolved on Polymarket:
                        if cur_price == 1.0 or (cur_price == 0.0 and is_redeemable and pct_pnl > -50):
                            new_status = "won"
                            pnl = round(float(official_cash_pnl), 2) if official_cash_pnl is not None else round(shares * 1.0 - stake, 2)
                            settlement_source = "polymarket_data_api_oracle"
                        elif cur_price == 0.0 and pct_pnl <= -99.0 and not is_redeemable:
                            new_status = "lost"
                            pnl = round(float(official_cash_pnl), 2) if official_cash_pnl is not None else -round(stake, 2)
                            settlement_source = "polymarket_data_api_oracle"

                    # ---------------------------------------------------------
                    # Pillar 2: Official Sports Scores Fallback
                    # ---------------------------------------------------------
                    if new_status is None:
                        home_score: Optional[float] = None
                        away_score: Optional[float] = None

                        # Check mock_scores first if supplied
                        if mock_scores:
                            for title_key, scores in mock_scores.items():
                                if title_key.lower() in game_title.lower() or game_title.lower() in title_key.lower():
                                    home_score, away_score = scores
                                    break

                        # Check bet_outcomes table for matching event
                        if home_score is None:
                            try:
                                bo_rows = cur.execute(
                                    "SELECT evidence FROM bet_outcomes WHERE evidence LIKE ? LIMIT 1",
                                    (f"%{game_title}%",),
                                ).fetchall()
                                for bo in bo_rows:
                                    ev_data = json.loads(bo["evidence"])
                                    if "home_score" in ev_data and "away_score" in ev_data:
                                        home_score = float(ev_data["home_score"])
                                        away_score = float(ev_data["away_score"])
                                        break
                            except Exception:
                                pass

                        # If score is not available yet, cannot settle this order yet
                        if home_score is None or away_score is None:
                            continue

                        # Evaluate payoff condition using contracts.py contract_value
                        norm_market = "totals" if market_fam in ("totals", "Total") else "spreads" if market_fam in ("spreads", "Spread") else "moneyline"
                        norm_side = side.lower()
                        if norm_market == "moneyline" and norm_side not in ("home", "away"):
                            norm_side = "home" if "home" in norm_side else "away"

                        try:
                            c_val = contract_value(
                                kind=norm_market,
                                side=norm_side,
                                line=line,
                                home=home_score,
                                away=away_score,
                            )
                        except Exception as err:
                            log.warning(f"Error evaluating contract value for order {oid}: {err}")
                            continue

                        if c_val > 0:
                            new_status = "won"
                            gross_payout = round(shares * 1.00, 2)
                            pnl = round(gross_payout - stake, 2)
                            settlement_source = "official_match_scores"
                        elif c_val < 0:
                            new_status = "lost"
                            pnl = -round(stake, 2)
                            settlement_source = "official_match_scores"
                        else:
                            new_status = "pushed"
                            pnl = 0.00
                            settlement_source = "official_match_scores"

                    if new_status is None:
                        continue

                    # Atomically update SQLite record
                    cur.execute(
                        """
                        UPDATE polymarket_orders SET
                            status = ?,
                            pnl = ?,
                            settled_at = ?,
                            updated_at = ?
                        WHERE client_order_id = ? OR order_id = ?;
                        """,
                        (new_status, pnl, now_iso, now_iso, row["client_order_id"], oid),
                    )

                    res_item = {
                        "order_id": oid,
                        "client_order_id": row["client_order_id"],
                        "game_title": game_title,
                        "market_family": market_fam,
                        "selection": row["selection"],
                        "stake": stake,
                        "shares": shares,
                        "status": new_status,
                        "pnl": pnl,
                        "settlement_source": settlement_source,
                        "settled_at": now_iso,
                    }
                    settled_results.append(res_item)

                    # Stream settlement event to JSONL
                    log_entry = {
                        "source": "polymarket_settlement",
                        "timestamp": now_iso,
                        "client_order_id": row["client_order_id"],
                        "order_id": oid,
                        "game_title": game_title,
                        "selection": row["selection"],
                        "stake_usd": stake,
                        "shares": shares,
                        "status": new_status,
                        "realized_pnl": pnl,
                        "settlement_source": settlement_source,
                    }
                    try:
                        with open(self.log_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(log_entry) + "\n")
                    except Exception as err:
                        log.warning(f"Could not append to JSONL log: {err}")

                conn.commit()
        except Exception as exc:
            log.error(f"Failed to execute Polymarket settlement: {exc}")

        won_cnt = sum(1 for r in settled_results if r["status"] == "won")
        lost_cnt = sum(1 for r in settled_results if r["status"] == "lost")
        push_cnt = sum(1 for r in settled_results if r["status"] == "pushed")
        tot_turnover = sum(r["stake"] for r in settled_results)
        tot_pnl = round(sum(r["pnl"] for r in settled_results), 2)
        roi = round(tot_pnl / tot_turnover, 4) if tot_turnover > 0 else 0.0

        log.info(
            f"Polymarket Settlement Complete: {len(settled_results)} orders settled "
            f"({won_cnt}W - {lost_cnt}L - {push_cnt}P), Total PnL: ${tot_pnl:+.2f}, ROI: {roi*100:+.1f}%"
        )

        return {
            "timestamp": now_iso,
            "settled_count": len(settled_results),
            "won_count": won_cnt,
            "lost_count": lost_cnt,
            "push_count": push_cnt,
            "total_turnover": round(tot_turnover, 2),
            "total_pnl": tot_pnl,
            "roi": roi,
            "settled_orders": settled_results,
        }
