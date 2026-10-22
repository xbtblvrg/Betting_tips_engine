"""Polymarket Live Orderbook and Consensus Sensor for R&D Lab.

Connects Polymarket Gamma discovery API and CLOB (Central Limit Order Book)
to ingest sharp zero-vig prediction market prices, orderbook depth imbalance,
and detect stale sportsbook lines. Operates in shadow mode within the
AQ-R&D laboratory.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
import urllib.request
import urllib.error

from .providers import DATA

POLYMARKET_CACHE_DIR = DATA / "cache" / "polymarket"

LEAGUE_SERIES: Dict[str, int] = {
    "NFL": 12185,
    "NBA": 10345,
    "MLB": 3,
    "NHL": 10346,
    "WNBA": 10105,
    "CFB": 12756,
    "CBB": 10470,
    "NCAAF": 12756,
    "NCAAB": 10470,
    "NCAAM": 10470,
    "NCAAW": 10471,
    "CWBB": 10471,
    "ATP": 10365,
    "WTA": 10366,
    "MLS": 10189,
    "EPL": 10188,
    "UCL": 10204,
    "UEL": 10209,
    "LALIGA": 10193,
    "BUNDESLIGA": 10194,
    "SERIEA": 10203,
    "LIGUE1": 10195,
    "UFC": 38,
}


def _normalize_name(name: str) -> str:
    """Normalize team name for matching."""
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", name)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


class PolymarketService:
    """Polymarket data service for R&D laboratory and quantitative shadow evaluation."""

    def __init__(self, cache_dir: Optional[Path] = None, timeout: float = 8.0):
        self.cache_dir = cache_dir or POLYMARKET_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.builder_key = os.getenv("POLYMARKET_BUILDER_API_KEY")

    def _http_get_json(self, url: str) -> Optional[Any]:
        headers = {"User-Agent": "BettingEngine-AQ-RD/1.0", "Accept": "application/json"}
        if self.builder_key:
            headers["POLY_BUILDER_API_KEY"] = self.builder_key
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        return None

    def get_open_events(self, league: str, horizon_hours: float = 72.0) -> List[Dict[str, Any]]:
        """Fetch open events for a league, cached with 15-minute TTL."""
        series_id = LEAGUE_SERIES.get(league.upper())
        if not series_id:
            return []

        cache_file = self.cache_dir / f"events_{league.lower()}_{series_id}.json"
        now = datetime.now(timezone.utc)

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_time = datetime.fromisoformat(cached.get("fetched_at", ""))
                if (now - cached_time).total_seconds() < 900:  # 15 min TTL
                    return cached.get("events", [])
            except Exception:
                pass

        url = f"https://gamma-api.polymarket.com/events?closed=false&limit=100&series_id={series_id}"
        events = self._http_get_json(url)
        if events is None:
            # Fallback to stale cache if present
            if cache_file.exists():
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8")).get("events", [])
                except Exception:
                    return []
            return []

        valid_events = []
        for e in events:
            if not isinstance(e, dict):
                continue
            start_str = e.get("startTime")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                # Filter within pregame horizon
                if start_dt > now - timedelta(hours=2) and start_dt <= now + timedelta(hours=horizon_hours):
                    valid_events.append(e)
            except Exception:
                continue

        try:
            payload = {"fetched_at": now.isoformat(), "events": valid_events}
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

        return valid_events

    def match_game(self, game: Any, events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Match engine Game object or dict against Polymarket events."""
        home = getattr(game, "home", None) or (game.get("home") if isinstance(game, dict) else "")
        away = getattr(game, "away", None) or (game.get("away") if isinstance(game, dict) else "")
        start = getattr(game, "start", None) or (game.get("start") if isinstance(game, dict) else "")

        if not home or not away:
            return None

        norm_home = _normalize_name(home)
        norm_away = _normalize_name(away)

        target_start = None
        if start:
            try:
                target_start = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            except Exception:
                pass

        for e in events:
            raw_title = str(e.get("title", ""))
            # Prefer main full-game event over props/periods/halves/scores
            if re.search(r'\b(?:halftime|second half|exact score|first team|corners?|cards?|specials?)\b', raw_title, re.I):
                continue

            title = _normalize_name(raw_title)
            # Check team names in event title or teams array
            matched_teams = False
            teams = e.get("teams", [])
            if isinstance(teams, list) and len(teams) >= 2:
                team_identities = []
                for t in teams:
                    if isinstance(t, dict):
                        alias = _normalize_name(t.get("alias", ""))
                        mname = _normalize_name(t.get("name", ""))
                        team_identities.append({
                            "alias": alias,
                            "name": mname,
                            "full": (alias + mname) if alias else mname
                        })

                def team_matches(game_norm: str, t_info: Dict[str, str]) -> bool:
                    if t_info["alias"]:
                        return (t_info["alias"] in game_norm or game_norm in t_info["alias"]
                                or t_info["full"] in game_norm or game_norm in t_info["full"])
                    if len(t_info["name"]) >= 5:
                        return t_info["name"] in game_norm or game_norm in t_info["name"]
                    return False

                # Ensure distinct 1-to-1 assignment: home matches T0 and away matches T1, OR home matches T1 and away matches T0
                if len(team_identities) >= 2:
                    t0, t1 = team_identities[0], team_identities[1]
                    if (team_matches(norm_home, t0) and team_matches(norm_away, t1)) or \
                       (team_matches(norm_home, t1) and team_matches(norm_away, t0)):
                        matched_teams = True

            if not matched_teams and norm_home in title and norm_away in title:
                matched_teams = True

            if matched_teams:
                if target_start:
                    e_start_str = e.get("startTime")
                    if e_start_str:
                        try:
                            e_start = datetime.fromisoformat(e_start_str.replace("Z", "+00:00"))
                            if abs((e_start - target_start).total_seconds()) > 24 * 3600:
                                continue
                        except Exception:
                            pass
                return e

        return None

    def get_clob_book(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Fetch and analyze orderbook for a token from CLOB, cached with 5-minute TTL."""
        if not token_id or not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', str(token_id)):
            return None

        cache_file = self.cache_dir / f"book_{token_id}.json"
        now = datetime.now(timezone.utc)

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_time = datetime.fromisoformat(cached.get("fetched_at", ""))
                if (now - cached_time).total_seconds() < 300:  # 5 min TTL
                    return cached.get("analysis")
            except Exception:
                pass

        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        book = self._http_get_json(url)
        if not book or not isinstance(book, dict):
            return None

        bids = sorted(
            [
                (_safe_float(b.get("price")), _safe_float(b.get("size")))
                for b in book.get("bids", [])
                if _safe_float(b.get("price")) > 0
            ],
            key=lambda x: x[0],
            reverse=True,
        )
        asks = sorted(
            [
                (_safe_float(a.get("price")), _safe_float(a.get("size")))
                for a in book.get("asks", [])
                if _safe_float(a.get("price")) > 0
            ],
            key=lambda x: x[0],
        )

        if not bids or not asks:
            return None

        best_bid, top_bid_size = bids[0]
        best_ask, top_ask_size = asks[0]

        if best_bid >= best_ask:
            # Crossed/locked book
            return None

        midpoint = round((best_bid + best_ask) / 2.0, 4)
        spread = round(best_ask - best_bid, 4)

        bid_depth_usd = sum(p * s for p, s in bids[:5])
        ask_depth_usd = sum(p * s for p, s in asks[:5])
        total_depth = bid_depth_usd + ask_depth_usd

        imbalance = 0.0
        if total_depth > 0:
            imbalance = round((bid_depth_usd - ask_depth_usd) / total_depth, 4)

        analysis = {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "spread": spread,
            "bid_depth_usd": round(bid_depth_usd, 2),
            "ask_depth_usd": round(ask_depth_usd, 2),
            "order_book_imbalance": imbalance,
            "last_trade_price": _safe_float(book.get("last_trade_price")),
            "min_order_size": _safe_float(book.get("min_order_size")),
            "tick_size": _safe_float(book.get("tick_size")),
            "fetched_at": now.isoformat(),
        }

        try:
            payload = {"fetched_at": now.isoformat(), "analysis": analysis}
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

        return analysis

    def enrich_game_context(self, game: Any) -> Dict[str, Any]:
        """Produce structured Polymarket intelligence dictionary for game context."""
        league = getattr(game, "league", "") or (game.get("league") if isinstance(game, dict) else "")
        events = self.get_open_events(league)
        matched_event = self.match_game(game, events)

        if not matched_event:
            return {"matched": False, "reason": "no_matching_polymarket_event"}

        home = getattr(game, "home", "") or (game.get("home") if isinstance(game, dict) else "")
        away = getattr(game, "away", "") or (game.get("away") if isinstance(game, dict) else "")
        norm_home = _normalize_name(home)
        norm_away = _normalize_name(away)

        markets = matched_event.get("markets", [])
        ml_data: Dict[str, Any] = {}

        for m in markets:
            if not isinstance(m, dict):
                continue
            q = str(m.get("question", "")).lower()
            if re.search(r'\b(?:half|quarters?|innings?|periods?|regulation|f[135]|[12]h|q[1-4]|p[1-3])\b', q):
                continue
            mtype = m.get("sportsMarketType", "")
            outcomes = m.get("outcomes", [])
            tokens = m.get("clobTokenIds", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    pass
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except Exception:
                    pass

            if len(outcomes) == 2 and len(tokens) == 2:
                # 1. 2-way American markets (Team A vs Team B in outcomes)
                if mtype == "moneyline" or "winner" in q:
                    for outcome_label, token in zip(outcomes, tokens):
                        norm_label = _normalize_name(outcome_label)
                        side = (
                            "home"
                            if (norm_home in norm_label or norm_label in norm_home)
                            else ("away" if (norm_away in norm_label or norm_label in norm_away) else None)
                        )
                        if side:
                            book = self.get_clob_book(str(token))
                            if book:
                                ml_data[side] = book

                # 2. Binary question markets (Soccer 1X2 / Yes-No)
                norm_outcomes = [_normalize_name(o) for o in outcomes]
                if "yes" in norm_outcomes and "no" in norm_outcomes:
                    yes_idx = norm_outcomes.index("yes")
                    yes_token = str(tokens[yes_idx])
                    norm_q = _normalize_name(q)
                    target_side = None
                    if "draw" in q or "tie" in q or "endinadraw" in norm_q:
                        target_side = "draw"
                    elif norm_home in norm_q or norm_q.startswith(f"will{norm_home}"):
                        target_side = "home"
                    elif norm_away in norm_q or norm_q.startswith(f"will{norm_away}"):
                        target_side = "away"

                    if target_side:
                        book = self.get_clob_book(yes_token)
                        if book:
                            ml_data[target_side] = book

        has_ml = "home" in ml_data and "away" in ml_data
        summary = {
            "matched": True,
            "event_id": str(matched_event.get("id")),
            "title": matched_event.get("title"),
            "slug": matched_event.get("slug"),
            "has_moneyline": has_ml,
            "moneyline": ml_data,
        }

        if has_ml:
            h_mid = ml_data["home"]["midpoint"]
            a_mid = ml_data["away"]["midpoint"]
            h_spread = ml_data["home"].get("spread")
            if h_spread is None:
                h_spread = ml_data["home"].get("best_ask", 1.0) - ml_data["home"].get("best_bid", 0.0)
            a_spread = ml_data["away"].get("spread")
            if a_spread is None:
                a_spread = ml_data["away"].get("best_ask", 1.0) - ml_data["away"].get("best_bid", 0.0)

            has_draw = "draw" in ml_data
            d_mid = ml_data["draw"]["midpoint"] if has_draw else 0.0
            if has_draw:
                d_spread = ml_data["draw"].get("spread")
                if d_spread is None:
                    d_spread = ml_data["draw"].get("best_ask", 1.0) - ml_data["draw"].get("best_bid", 0.0)
            else:
                d_spread = 0.0

            active_liquidity = (h_spread < 0.50 and a_spread < 0.50 and (not has_draw or d_spread < 0.50))
            summary["has_active_liquidity"] = active_liquidity

            tot = h_mid + a_mid + (d_mid if has_draw else 0.0)
            if tot > 0:
                summary["true_prob_home"] = round(h_mid / tot, 4)
                summary["true_prob_away"] = round(a_mid / tot, 4)
                if has_draw:
                    summary["true_prob_draw"] = round(d_mid / tot, 4)

        return summary

    @staticmethod
    def calculate_stale_edge(poly_prob: float, sportsbook_odds: float) -> Dict[str, Any]:
        """Detect latency/stale-line arbitrage edge against sportsbook odds."""
        if poly_prob <= 0 or poly_prob >= 1 or sportsbook_odds <= 1.0:
            return {"is_stale": False, "edge_pct": 0.0, "expected_roi_pct": 0.0}

        sportsbook_implied = 1.0 / sportsbook_odds
        edge = poly_prob - sportsbook_implied
        expected_roi = (poly_prob * sportsbook_odds) - 1.0

        return {
            "is_stale": edge >= 0.045,  # +4.5% edge hurdle
            "poly_probability": round(poly_prob, 4),
            "sportsbook_odds": round(sportsbook_odds, 3),
            "sportsbook_implied_prob": round(sportsbook_implied, 4),
            "edge_pct": round(edge * 100, 2),
            "expected_roi_pct": round(expected_roi * 100, 2),
        }
