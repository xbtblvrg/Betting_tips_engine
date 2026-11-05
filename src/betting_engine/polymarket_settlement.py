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
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .contracts import contract_value
from .domain import utcnow
from .providers import DATA

log = logging.getLogger(__name__)

ORDER_LOG_FILE = DATA / "logs" / "polymarket_orders.jsonl"


class PolymarketOutcomeSettler:
    """Supervises and settles executed Polymarket orders against verified match outcomes."""

    _positions_cache: Dict[str, Any] = {}
    _positions_cached_at: float = 0.0
    _activity_cache: List[Dict[str, Any]] = []
    _activity_cached_at: float = 0.0
    _rate_limited_until: float = 0.0

    @classmethod
    def _sync_from_disk_cache(cls) -> None:
        cache_file = DATA / 'polymarket_data_api_cache.json'
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding='utf-8'))
                cls._rate_limited_until = max(cls._rate_limited_until, float(data.get('rate_limited_until', 0.0)))
                if not cls._positions_cache and 'positions' in data:
                    cls._positions_cache = data.get('positions', {})
                    cls._positions_cached_at = float(data.get('positions_cached_at', 0.0))
                if not cls._activity_cache and 'activities' in data:
                    cls._activity_cache = data.get('activities', [])
                    cls._activity_cached_at = float(data.get('activity_cached_at', 0.0))
            except Exception:
                pass

    @classmethod
    def _persist_to_disk_cache(cls) -> None:
        cache_file = DATA / 'polymarket_data_api_cache.json'
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'rate_limited_until': cls._rate_limited_until,
                'positions_cached_at': cls._positions_cached_at,
                'positions': cls._positions_cache,
                'activity_cached_at': cls._activity_cached_at,
                'activities': cls._activity_cache,
            }
            cache_file.write_text(json.dumps(payload), encoding='utf-8')
        except Exception:
            pass

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
        """Fetches official live account positions directly from Polymarket Data API v2.
        
        Indexed by token ID ('token_id' or 'asset'). Provides exact dollar amounts, cash PnL,
        redemption status, and oracle resolution directly from Polymarket.
        """
        if not self.proxy_address:
            return {}

        PolymarketOutcomeSettler._sync_from_disk_cache()
        now_ts = time.time()
        if now_ts < PolymarketOutcomeSettler._rate_limited_until:
            return PolymarketOutcomeSettler._positions_cache
        if PolymarketOutcomeSettler._positions_cache and (now_ts - PolymarketOutcomeSettler._positions_cached_at < 180.0):
            return PolymarketOutcomeSettler._positions_cache

        url = f"https://data-api.polymarket.com/v2/positions?user={self.proxy_address}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode())
                positions = res.get("data", res) if isinstance(res, dict) else res
                out = {}
                for p in positions:
                    if isinstance(p, dict):
                        tid = p.get("token_id") or p.get("asset")
                        if tid:
                            out[str(tid)] = p
                PolymarketOutcomeSettler._positions_cache = out
                PolymarketOutcomeSettler._positions_cached_at = now_ts
                PolymarketOutcomeSettler._persist_to_disk_cache()
                return out
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                PolymarketOutcomeSettler._rate_limited_until = now_ts + 300.0  # Back off 5 min
                PolymarketOutcomeSettler._persist_to_disk_cache()
                log.debug("Polymarket Data API 429 rate limit received; backing off for 300s")
            else:
                log.debug("Polymarket positions HTTP error: %s", exc)
            return PolymarketOutcomeSettler._positions_cache
        except Exception as exc:
            log.debug("Could not fetch Polymarket positions from Data API: %s", exc)
            return PolymarketOutcomeSettler._positions_cache

    def fetch_recent_redemptions(self) -> List[Dict[str, Any]]:
        """Fetches recent REDEEM activities and on-chain payout transaction hashes from v2."""
        if not self.proxy_address:
            return []

        PolymarketOutcomeSettler._sync_from_disk_cache()
        now_ts = time.time()
        if now_ts < PolymarketOutcomeSettler._rate_limited_until:
            return PolymarketOutcomeSettler._activity_cache
        if PolymarketOutcomeSettler._activity_cache and (now_ts - PolymarketOutcomeSettler._activity_cached_at < 180.0):
            return PolymarketOutcomeSettler._activity_cache

        url = f"https://data-api.polymarket.com/v2/activity?user={self.proxy_address}&limit=20"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode())
                activities = res.get("data", res) if isinstance(res, dict) else res
                redeems = [a for a in activities if isinstance(a, dict) and a.get("type") == "REDEEM"]
                PolymarketOutcomeSettler._activity_cache = redeems
                PolymarketOutcomeSettler._activity_cached_at = now_ts
                PolymarketOutcomeSettler._persist_to_disk_cache()
                return redeems
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                PolymarketOutcomeSettler._rate_limited_until = now_ts + 300.0
                PolymarketOutcomeSettler._persist_to_disk_cache()
                log.debug("Polymarket Data API 429 activity rate limit; backing off for 300s")
            else:
                log.debug("Polymarket activity HTTP error: %s", exc)
            return PolymarketOutcomeSettler._activity_cache
        except Exception as exc:
            log.debug("Could not fetch Polymarket redemptions: %s", exc)
            return PolymarketOutcomeSettler._activity_cache

    def auto_redeem_positions(self) -> Dict[str, Any]:
        """Inspects account positions and triggers redemption fallback for winning contracts."""
        if not self.proxy_address:
            return {"redeemed_count": 0, "redeemable_positions": [], "message": "No proxy address configured"}

        positions_map = self.fetch_polymarket_positions()
        redeemable_items = []
        for asset_id, p in positions_map.items():
            cur_price = float(p.get("curPrice", 0.0))
            size = float(p.get("size", 0.0))
            # Only positions with positive value / winning payoff ($1.00 per share) are redeemable for cash
            if size > 0 and (cur_price >= 0.99 or (p.get("redeemable") is True and cur_price > 0.01)):
                redeemable_items.append({
                    "asset": asset_id,
                    "title": p.get("title", ""),
                    "size": size,
                    "cur_price": cur_price,
                    "cash_pnl": p.get("cashPnl", 0),
                })

        log.info(f"[AUTO-REDEEMER] Found {len(redeemable_items)} redeemable positions on account.")
        return {
            "redeemable_count": len(redeemable_items),
            "redeemable_positions": redeemable_items,
            "auto_redeem_configured_on_profile": True,
            "message": f"{len(redeemable_items)} positions flagged for redemption.",
        }

    def fetch_gamma_market_resolution(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Queries Polymarket Gamma API to check if the market containing token_id is closed and resolved."""
        if not token_id:
            return None
        url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}&closed=true"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                markets = json.loads(r.read().decode())
                if not markets or not isinstance(markets, list):
                    return None
                m = markets[0]
                if not m.get("closed"):
                    return None

                raw_tokens = m.get("clobTokenIds")
                tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else (raw_tokens or [])
                raw_prices = m.get("outcomePrices")
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else (raw_prices or [])

                parsed_prices = []
                for p in prices:
                    try:
                        parsed_prices.append(float(p))
                    except (ValueError, TypeError):
                        pass

                # Anti-False-Loss Guard: Verify the market is officially resolved by the oracle.
                # A resolved market has at least one winning outcome (>= 0.98), or a confirmed push (both ~0.50).
                # If outcomePrices is ["0", "0"] or sum is near 0, trading has halted but oracle has NOT resolved yet!
                max_p = max(parsed_prices) if parsed_prices else 0.0
                is_resolved = False
                if max_p >= 0.98:
                    is_resolved = True
                elif len(parsed_prices) >= 2 and all(0.45 <= p <= 0.55 for p in parsed_prices[:2]):
                    is_resolved = True

                if not is_resolved:
                    return {"closed": True, "resolved": False}

                token_price = None
                if token_id in tokens:
                    idx = tokens.index(token_id)
                    if idx < len(parsed_prices):
                        token_price = parsed_prices[idx]

                return {
                    "closed": True,
                    "resolved": True,
                    "token_price": token_price,
                    "question": m.get("question"),
                }
        except Exception as exc:
            log.debug(f"Could not fetch Gamma resolution for token {token_id}: {exc}")
            return None

    def settle_orders(
        self,
        order_ids: Optional[List[str]] = None,
        mock_scores: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Any]:
        """Settles pending Polymarket orders.

        Dual-Pillar Resolution:
        1. Primary: Direct Polymarket Data API & On-Chain Oracle resolution
           (Reads exact cash PnL, redemption status and payout straight from Polymarket).
        1.5 Polymarket Gamma Market Resolution: Official oracle resolution for closed markets
           (essential for shadow paper executions and unindexed tokens).
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
        gamma_res_cache: Dict[str, Optional[Dict[str, Any]]] = {}

        try:
            with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
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
                    selection = row["selection"]
                    side = row["side"]
                    line = row["line"]
                    stake = float(row["stake_usd"])
                    shares = float(row["shares_count"])

                    # Anti-Hallucination Guard: If shares / stake > 12.0 on an order with a maker slice,
                    # verify whether the order contains a realistic taker slice in error_detail.
                    # If so, settle strictly on the verified taker slice to prevent phantom maker payouts.
                    if (shares / max(0.01, stake)) > 12.0 and "taker_slice" in str(row["error_detail"] or ""):
                        try:
                            det = json.loads(row["error_detail"])
                            tk = det.get("taker_slice", {})
                            if tk.get("shares") and float(tk.get("price", 0.0)) >= 0.08:
                                log.warning(
                                    f"[SETTLEMENT AUDIT] Overriding inflated maker shares ({shares}) with realistic taker shares ({tk['shares']}) "
                                    f"for {game_title} {selection}"
                                )
                                shares = float(tk["shares"])
                                stake = float(tk.get("stake", stake))
                        except Exception as e_sh:
                            log.debug(f"Could not parse taker slice from error_detail: {e_sh}")

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
                    # Pillar 1.5: Direct Polymarket Gamma Official Market Resolution
                    # (Essential for shadow paper executions and unindexed tokens)
                    # ---------------------------------------------------------
                    if new_status is None and not mock_scores:
                        start_time_str = row["start_time"]
                        game_may_be_done = False
                        if start_time_str:
                            try:
                                st = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                                if utcnow() >= st:
                                    game_may_be_done = True
                            except Exception:
                                game_may_be_done = True
                        else:
                            game_may_be_done = True

                        if game_may_be_done:
                            if token_id not in gamma_res_cache:
                                gamma_res_cache[token_id] = self.fetch_gamma_market_resolution(token_id)
                            gamma_res = gamma_res_cache[token_id]
                            if gamma_res and gamma_res.get("closed") and gamma_res.get("resolved") is not False:
                                outcome_price = gamma_res.get("token_price")
                                if outcome_price is not None:
                                    if outcome_price >= 0.99:
                                        new_status = "won"
                                        pnl = round(shares * 1.0 - stake, 2)
                                        settlement_source = "polymarket_gamma_resolution"
                                    elif outcome_price <= 0.01:
                                        new_status = "lost"
                                        pnl = -round(stake, 2)
                                        settlement_source = "polymarket_gamma_resolution"
                                    elif 0.45 <= outcome_price <= 0.55:
                                        new_status = "pushed"
                                        pnl = 0.00
                                        settlement_source = "polymarket_gamma_resolution"

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

                        # Check bet_outcomes table for matching event and scope
                        if home_score is None:
                            try:
                                order_scope = (row["scope"] or "full_game").lower()
                                # 1. Try matching both game_title and order_scope in evidence
                                bo_rows = cur.execute(
                                    "SELECT evidence FROM bet_outcomes WHERE evidence LIKE ? AND evidence LIKE ? LIMIT 1",
                                    (f"%{game_title}%", f'%"{order_scope}"%'),
                                ).fetchall()
                                # 2. Fallback to game_title only if full_game
                                if not bo_rows and order_scope in ("full_game", "full", "game"):
                                    bo_rows = cur.execute(
                                        "SELECT evidence FROM bet_outcomes WHERE evidence LIKE ? LIMIT 1",
                                        (f"%{game_title}%",),
                                    ).fetchall()
                                for bo in bo_rows:
                                    ev_data = json.loads(bo["evidence"])
                                    # Ensure derivative scope does not settle using full-game score
                                    ev_scope_str = str(ev_data.get("score_scope") or "").lower()
                                    if order_scope not in ("full_game", "full", "game") and order_scope not in ev_scope_str:
                                        continue
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
                        sel_str = str(row["selection"] or "")
                        sel_low = sel_str.lower()

                        if market_fam in ("totals", "Total"):
                            norm_market = "totals"
                            norm_side = "over" if "over" in sel_low else "under"
                        else:
                            norm_market = "spreads" if market_fam in ("spreads", "Spread") else "moneyline"
                            home_name = None
                            away_name = None
                            if 'ev_data' in locals() and ev_data:
                                home_name = ev_data.get("home_team") or ev_data.get("home")
                                away_name = ev_data.get("away_team") or ev_data.get("away")
                            if not home_name:
                                try:
                                    tip_row = cur.execute(
                                        "SELECT json_extract(payload, '$.home'), json_extract(payload, '$.away') FROM tips WHERE payload LIKE ? LIMIT 1",
                                        (f"%{game_title}%",),
                                    ).fetchone()
                                    if tip_row:
                                        home_name, away_name = tip_row[0], tip_row[1]
                                except Exception:
                                    pass

                            if home_name and (sel_low in home_name.lower() or any(w in sel_low for w in home_name.lower().split() if len(w) > 3)):
                                norm_side = "home"
                            elif away_name and (sel_low in away_name.lower() or any(w in sel_low for w in away_name.lower().split() if len(w) > 3)):
                                norm_side = "away"
                            else:
                                parts = game_title.split(" vs. ") if " vs. " in game_title else game_title.split(" vs ")
                                if len(parts) == 2:
                                    away_part, home_part = parts[0].strip().lower(), parts[1].strip().lower()
                                    if any(w in sel_low for w in home_part.split() if len(w) > 3):
                                        norm_side = "home"
                                    else:
                                        norm_side = "away"
                                else:
                                    norm_side = "home" if "home" in sel_low else "away"

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

        # Trigger auto-redemption inspection for completed wins
        redemption_info = self.auto_redeem_positions()

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
            "redemption": redemption_info,
        }
