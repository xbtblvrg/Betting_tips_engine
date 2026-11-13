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
import logging
import math
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
import urllib.request
import urllib.error

from .providers import DATA

logger = logging.getLogger(__name__)

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


def _canonical_match_key(norm_name: str) -> str:
    """Strip common variations, prefixes, and multilingual aliases for fuzzy matching."""
    if not norm_name:
        return ""
    k = norm_name
    for prefix in ("fc", "cf", "club", "sk", "vfb", "stade"):
        if k.startswith(prefix) and len(k) > len(prefix) + 3:
            k = k[len(prefix):]
    aliases = {
        "munchen": "munich",
        "clubatleticode": "atletico",
        "atleticode": "atletico",
        "atletico": "atletico",
        "internazionale": "inter",
        "parissaintgermain": "psg",
        "sportingcp": "sporting",
        "sportingclube": "sporting",
        "wolves": "wolverhampton",
        "mancity": "manchestercity",
        "manutd": "manchesterunited",
        "manunited": "manchesterunited",
    }
    for src, tgt in aliases.items():
        k = k.replace(src, tgt)
    return k


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _detect_market_scope(mtype: str, q: str) -> Optional[str]:
    """Classifies Polymarket market into full_game, period scope, or team_totals."""
    mtype_lower = str(mtype or "").lower()
    q_lower = str(q or "").lower()

    if "team_total" in mtype_lower or "team total" in q_lower:
        return "team_totals"

    if mtype_lower in ("first_half_spreads", "first_half_totals", "first_half_moneyline") or re.search(r'\b(?:1h|1st half|first half)\b', q_lower):
        return "half_1"
    if mtype_lower in ("second_half_spreads", "second_half_totals", "second_half_moneyline") or re.search(r'\b(?:2h|2nd half|second half)\b', q_lower):
        return "half_2"
    if mtype_lower in ("q1_spreads", "q1_totals", "q1_moneyline") or re.search(r'\b(?:1q|q1|1st quarter|first quarter)\b', q_lower):
        return "quarter_1"
    if mtype_lower in ("q2_spreads", "q2_totals", "q2_moneyline") or re.search(r'\b(?:2q|q2|2nd quarter|second quarter)\b', q_lower):
        return "quarter_2"
    if mtype_lower in ("q3_spreads", "q3_totals", "q3_moneyline") or re.search(r'\b(?:3q|q3|3rd quarter|third quarter)\b', q_lower):
        return "quarter_3"
    if mtype_lower in ("q4_spreads", "q4_totals", "q4_moneyline") or re.search(r'\b(?:4q|q4|4th quarter|fourth quarter)\b', q_lower):
        return "quarter_4"
    if re.search(r'\b(?:p1|1st period|first period)\b', q_lower):
        return "period_1"
    if re.search(r'\b(?:p2|2nd period|second period)\b', q_lower):
        return "period_2"
    if re.search(r'\b(?:p3|3rd period|third period)\b', q_lower):
        return "period_3"

    if any(p in q_lower for p in ("quarter", "half", "period", "inning", "team total")):
        return "other_period"

    return "full_game"


class PolymarketService:
    """Polymarket data service for R&D laboratory and quantitative shadow evaluation."""
    _events_mem_cache: Dict[str, Tuple[datetime, List[Dict[str, Any]]]] = {}
    _clob_book_mem: Dict[str, Tuple[datetime, Optional[Dict[str, Any]]]] = {}

    def __init__(self, cache_dir: Optional[Path] = None, timeout: float = 8.0):
        self.cache_dir = cache_dir or POLYMARKET_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.builder_key = os.getenv("POLYMARKET_BUILDER_API_KEY")

    def _http_get_json(self, url: str, timeout: Optional[float] = None) -> Optional[Any]:
        headers = {
            "User-Agent": "BettingEngine-AQ-RD/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate"
        }
        if self.builder_key:
            headers["POLY_BUILDER_API_KEY"] = self.builder_key
        req = urllib.request.Request(url, headers=headers)
        eff_timeout = timeout or self.timeout
        try:
            with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    if raw.startswith(b"\x1f\x8b") or resp.headers.get("Content-Encoding") == "gzip":
                        import gzip
                        raw = gzip.decompress(raw)
                    return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.debug("Polymarket resource 404 (empty book or no trades) for %s", url)
            else:
                logger.warning("Polymarket HTTP error %d fetching %s: %s", e.code, url, e.reason)
            return None
        except urllib.error.URLError as e:
            logger.warning("Polymarket network error fetching %s: %s", url, e.reason)
            return None
        except Exception as e:
            logger.warning("Polymarket unexpected error fetching %s: %s", url, e)
            return None
        return None

    def get_open_events(self, league: str, horizon_hours: float = 72.0) -> List[Dict[str, Any]]:
        """Fetch open events for a league, cached with 15-minute TTL in RAM and disk."""
        series_id = LEAGUE_SERIES.get(league.upper())
        if not series_id:
            return []

        cache_key = f"{league.upper()}_{series_id}_{horizon_hours}"
        now = datetime.now(timezone.utc)

        # 1. Check in-memory cache first (0.00ms)
        mem = self._events_mem_cache.get(cache_key)
        if mem:
            cached_time, cached_events = mem
            if (now - cached_time).total_seconds() < 900:
                return cached_events

        cache_file = self.cache_dir / f"events_{league.lower()}_{series_id}.json"

        # 2. Check disk cache and promote to memory
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_time = datetime.fromisoformat(cached.get("fetched_at", ""))
                if (now - cached_time).total_seconds() < 900:  # 15 min TTL
                    evs = cached.get("events", [])
                    self._events_mem_cache[cache_key] = (cached_time, evs)
                    return evs
            except Exception:
                pass

        url = f"https://gamma-api.polymarket.com/events?closed=false&order=startTime&ascending=true&limit=100&series_id={series_id}"
        events = self._http_get_json(url)
        if events is None:
            # Fallback to query without ordering if API fails
            fallback_url = f"https://gamma-api.polymarket.com/events?closed=false&limit=100&series_id={series_id}"
            events = self._http_get_json(fallback_url)
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
            start_str = e.get("startTime") or e.get("startDate")
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                # Filter within pregame horizon
                if start_dt > now - timedelta(hours=2) and start_dt <= now + timedelta(hours=horizon_hours):
                    pruned_m = []
                    for m in e.get("markets", []):
                        if isinstance(m, dict):
                            pruned_m.append({
                                "id": m.get("id"),
                                "slug": m.get("slug"),
                                "question": m.get("question"),
                                "sportsMarketType": m.get("sportsMarketType"),
                                "line": m.get("line"),
                                "outcomes": m.get("outcomes"),
                                "clobTokenIds": m.get("clobTokenIds"),
                                "outcomePrices": m.get("outcomePrices"),
                                "bestBid": m.get("bestBid"),
                                "bestAsk": m.get("bestAsk"),
                                "volume": m.get("volume"),
                                "volumeNum": m.get("volumeNum"),
                            })
                    valid_events.append({
                        "id": e.get("id"),
                        "title": e.get("title"),
                        "slug": e.get("slug"),
                        "startTime": start_str,
                        "markets": pruned_m,
                    })
            except Exception:
                continue

        self._events_mem_cache[cache_key] = (now, valid_events)
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
                        if (t_info["alias"] in game_norm or game_norm in t_info["alias"]
                                or t_info["full"] in game_norm or game_norm in t_info["full"]):
                            return True
                    if len(t_info["name"]) >= 5:
                        if t_info["name"] in game_norm or game_norm in t_info["name"]:
                            return True
                    # Check canonical match keys for cross-platform international clubs
                    c_game = _canonical_match_key(game_norm)
                    c_tname = _canonical_match_key(t_info["name"])
                    if len(c_game) >= 4 and len(c_tname) >= 4:
                        if c_game in c_tname or c_tname in c_game:
                            return True
                    return False

                # Ensure distinct 1-to-1 assignment: home matches T0 and away matches T1, OR home matches T1 and away matches T0
                if len(team_identities) >= 2:
                    t0, t1 = team_identities[0], team_identities[1]
                    if (team_matches(norm_home, t0) and team_matches(norm_away, t1)) or \
                       (team_matches(norm_home, t1) and team_matches(norm_away, t0)):
                        matched_teams = True

            if not matched_teams:
                if norm_home in title and norm_away in title:
                    matched_teams = True
                else:
                    c_home = _canonical_match_key(norm_home)
                    c_away = _canonical_match_key(norm_away)
                    c_title = _canonical_match_key(title)
                    if len(c_home) >= 4 and len(c_away) >= 4:
                        if c_home in c_title and c_away in c_title:
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

    def get_clob_book(self, token_id: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """Fetch and analyze orderbook for a token from CLOB, cached in RAM and disk with 5-minute TTL."""
        if not token_id or not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', str(token_id)):
            return None

        now = datetime.now(timezone.utc)
        mem = self._clob_book_mem.get(str(token_id))
        if mem:
            cached_time, cached_analysis = mem
            if (now - cached_time).total_seconds() < 300:  # 5 min TTL
                return cached_analysis

        cache_file = self.cache_dir / f"book_{token_id}.json"

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_time = datetime.fromisoformat(cached.get("fetched_at", ""))
                if (now - cached_time).total_seconds() < 300:  # 5 min TTL
                    analysis = cached.get("analysis")
                    self._clob_book_mem[str(token_id)] = (cached_time, analysis)
                    return analysis
            except Exception:
                pass

        url = f"https://clob.polymarket.com/book?token_id={token_id}"
        book = self._http_get_json(url, timeout=timeout)
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
            "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        }

        self._clob_book_mem[str(token_id)] = (now, analysis)
        try:
            payload = {"fetched_at": now.isoformat(), "analysis": analysis}
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

        return analysis

    def enrich_game_context(self, game: Any, fetch_clob: bool = False) -> Dict[str, Any]:
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
        spreads_data: Dict[str, Any] = {}
        totals_data: Dict[str, Any] = {}
        period_spreads: Dict[str, Dict[str, Any]] = {}
        period_totals: Dict[str, Dict[str, Any]] = {}

        for m in markets:
            if not isinstance(m, dict):
                continue
            q = str(m.get("question", "")).lower()
            mtype = str(m.get("sportsMarketType", "")).lower()

            scope = _detect_market_scope(mtype, q)
            if scope in ("team_totals", "other_period"):
                continue

            target_spreads = spreads_data if scope == "full_game" else period_spreads.setdefault(scope, {})
            target_totals = totals_data if scope == "full_game" else period_totals.setdefault(scope, {})

            outcomes = m.get("outcomes") or []
            tokens = m.get("clobTokenIds") or []
            outcome_prices = m.get("outcomePrices") or []
            best_bid = m.get("bestBid")
            best_ask = m.get("bestAsk")
            volume = m.get("volumeNum") or m.get("volume") or 0.0

            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    pass
            if not isinstance(outcomes, list):
                outcomes = []

            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except Exception:
                    pass
            if not isinstance(tokens, list):
                tokens = []

            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except Exception:
                    pass
            if not isinstance(outcome_prices, list):
                outcome_prices = []

            if len(outcomes) == 2:
                # 1. 2-way American markets (Team A vs Team B in outcomes) - Moneyline (Full Game only)
                if scope == "full_game" and (mtype == "moneyline" or "winner" in q) and len(tokens) == 2:
                    for outcome_idx, (outcome_label, token) in enumerate(zip(outcomes, tokens)):
                        norm_label = _normalize_name(outcome_label)
                        side = (
                            "home"
                            if (norm_home in norm_label or norm_label in norm_home)
                            else ("away" if (norm_away in norm_label or norm_label in norm_away) else None)
                        )
                        if side:
                            if outcome_prices and len(outcome_prices) > outcome_idx:
                                p = _safe_float(outcome_prices[outcome_idx])
                                bid = float(best_bid) if (best_bid is not None and _safe_float(best_bid) > 0) else round(max(0.01, p - 0.01), 4)
                                ask = float(best_ask) if (best_ask is not None and _safe_float(best_ask) > 0) else round(min(0.99, p + 0.01), 4)
                                ml_data[side] = {
                                    "token_id": str(token),
                                    "best_bid": bid,
                                    "best_ask": ask,
                                    "midpoint": p if p > 0 else round((bid + ask) / 2.0, 4),
                                    "spread": round(max(0.001, ask - bid), 4),
                                    "bid_depth_usd": float(volume or 100.0),
                                    "ask_depth_usd": float(volume or 100.0),
                                    "order_book_imbalance": 0.0,
                                }
                                if fetch_clob:
                                    book = self.get_clob_book(str(token))
                                    if book:
                                        ml_data[side] = book
                            else:
                                book = self.get_clob_book(str(token))
                                if book:
                                    ml_data[side] = book

                # 2. Binary question markets (Soccer 1X2 / Yes-No) - Moneyline (Full Game only)
                norm_outcomes = [_normalize_name(o) for o in outcomes]
                if scope == "full_game" and "yes" in norm_outcomes and "no" in norm_outcomes and len(tokens) == 2:
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
                        if outcome_prices and len(outcome_prices) > yes_idx:
                            p = _safe_float(outcome_prices[yes_idx])
                            bid = float(best_bid) if (best_bid is not None and _safe_float(best_bid) > 0) else round(max(0.01, p - 0.01), 4)
                            ask = float(best_ask) if (best_ask is not None and _safe_float(best_ask) > 0) else round(min(0.99, p + 0.01), 4)
                            ml_data[target_side] = {
                                "token_id": yes_token,
                                "best_bid": bid,
                                "best_ask": ask,
                                "midpoint": p if p > 0 else round((bid + ask) / 2.0, 4),
                                "spread": round(max(0.001, ask - bid), 4),
                                "bid_depth_usd": float(volume or 100.0),
                                "ask_depth_usd": float(volume or 100.0),
                                "order_book_imbalance": 0.0,
                            }
                            if fetch_clob:
                                book = self.get_clob_book(yes_token)
                                if book:
                                    ml_data[target_side] = book
                        else:
                            book = self.get_clob_book(yes_token)
                            if book:
                                ml_data[target_side] = book

                # 3. Spreads Ladder (Full Game and Scoped Periods)
                if ("spread" in mtype or "spread" in q or m.get("line") is not None and "total" not in mtype and "o/u" not in q):
                    raw_line = m.get("line")
                    if raw_line is None:
                        line_match = re.search(r'\(([+-]?\d+(?:\.\d+)?)\)', q)
                        if line_match:
                            raw_line = line_match.group(1)
                    if raw_line is not None:
                        try:
                            line_val = float(raw_line)
                        except (ValueError, TypeError):
                            line_val = None
                        if line_val is not None:
                            t1 = _normalize_name(outcomes[0])
                            t2 = _normalize_name(outcomes[1])
                            t1_home = (norm_home in t1 or t1 in norm_home) if norm_home else False
                            t1_away = (norm_away in t1 or t1 in norm_away) if norm_away else False
                            p1 = _safe_float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.0
                            p2 = _safe_float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.0

                            if t1_home:
                                home_line = line_val
                                away_line = -line_val
                                p_home, p_away = p1, p2
                            elif t1_away:
                                home_line = -line_val
                                away_line = line_val
                                p_home, p_away = p2, p1
                            else:
                                home_line, away_line, p_home, p_away = None, None, 0.0, 0.0

                            if home_line is not None and p_home > 0 and p_away > 0:
                                tot_p = p_home + p_away
                                true_p_home = round(p_home / tot_p, 4)
                                true_p_away = round(p_away / tot_p, 4)
                                spread_gap = abs(float(best_ask or 0.0) - float(best_bid or 0.0)) if (best_ask and best_bid) else 0.04
                                vol_usd = float(volume or 0.0)
                                w_depth = vol_usd / (vol_usd + 50.0) if vol_usd > 0 else 0.5
                                w_tight = max(0.0, 1.0 - (spread_gap / 0.20))
                                conf = round(float(w_depth * w_tight), 4)

                                home_ask = float(best_ask) if (best_ask and t1_home) else round(p_home, 4)
                                away_ask = float(best_ask) if (best_ask and t1_away) else round(p_away, 4)

                                home_entry = {
                                    "side": "home",
                                    "line": home_line,
                                    "home_line": home_line,
                                    "away_line": away_line,
                                    "prob": true_p_home,
                                    "p_true_home": true_p_home,
                                    "p_true_away": true_p_away,
                                    "confidence": conf,
                                    "volume_usd": vol_usd,
                                    "best_bid": round(1.0 - away_ask, 4),
                                    "best_ask": home_ask,
                                    "market_id": m.get("id"),
                                    "market_slug": m.get("slug"),
                                    "market_url": f"https://polymarket.com/event/{matched_event.get('slug')}" if matched_event.get("slug") else None,
                                    "question": m.get("question"),
                                    "scope": scope,
                                }
                                away_entry = {
                                    "side": "away",
                                    "line": away_line,
                                    "home_line": home_line,
                                    "away_line": away_line,
                                    "prob": true_p_away,
                                    "p_true_home": true_p_home,
                                    "p_true_away": true_p_away,
                                    "confidence": conf,
                                    "volume_usd": vol_usd,
                                    "best_bid": round(1.0 - home_ask, 4),
                                    "best_ask": away_ask,
                                    "market_id": m.get("id"),
                                    "market_slug": m.get("slug"),
                                    "market_url": f"https://polymarket.com/event/{matched_event.get('slug')}" if matched_event.get("slug") else None,
                                    "question": m.get("question"),
                                    "scope": scope,
                                }

                                # Explicit side-indexed entries
                                target_spreads[f"home:{home_line:+g}"] = home_entry
                                target_spreads[f"home:{home_line:g}"] = home_entry
                                target_spreads[f"home:{home_line}"] = home_entry

                                target_spreads[f"away:{away_line:+g}"] = away_entry
                                target_spreads[f"away:{away_line:g}"] = away_entry
                                target_spreads[f"away:{away_line}"] = away_entry

                                # Flat line entry for backward compatibility
                                target_spreads[str(home_line)] = home_entry
                                target_spreads[f"{home_line:+g}"] = home_entry
                                target_spreads[f"{home_line:g}"] = home_entry
                                if away_line != home_line:
                                    target_spreads[str(away_line)] = away_entry
                                    target_spreads[f"{away_line:+g}"] = away_entry
                                    target_spreads[f"{away_line:g}"] = away_entry

                # 4. Totals Ladder (Full Game and Scoped Periods)
                if ("total" in mtype or "o/u" in q or "totals" in mtype):
                    raw_line = m.get("line")
                    if raw_line is None:
                        line_match = re.search(r'o/u\s*([0-9]+(?:\.[0-9]+)?)', q)
                        if line_match:
                            raw_line = line_match.group(1)
                    if raw_line is not None:
                        try:
                            line_val = float(raw_line)
                        except (ValueError, TypeError):
                            line_val = None
                        if line_val is not None:
                            p1 = _safe_float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.0
                            p2 = _safe_float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.0
                            is_over_first = "over" in str(outcomes[0]).lower()
                            p_over = p1 if is_over_first else p2
                            p_under = p2 if is_over_first else p1

                            if p_over > 0 and p_under > 0:
                                tot_p = p_over + p_under
                                true_p_over = round(p_over / tot_p, 4)
                                true_p_under = round(p_under / tot_p, 4)
                                total_gap = abs(float(best_ask or 0.0) - float(best_bid or 0.0)) if (best_ask and best_bid) else 0.04
                                vol_usd = float(volume or 0.0)
                                w_depth = vol_usd / (vol_usd + 50.0) if vol_usd > 0 else 0.5
                                w_tight = max(0.0, 1.0 - (total_gap / 0.20))
                                conf = round(float(w_depth * w_tight), 4)

                                ask_over = float(best_ask) if (best_ask and is_over_first) else round(p_over, 4)
                                ask_under = float(best_ask) if (best_ask and not is_over_first) else round(p_under, 4)

                                entry = {
                                    "line": line_val,
                                    "p_true_over": true_p_over,
                                    "p_true_under": true_p_under,
                                    "ask_over": ask_over,
                                    "ask_under": ask_under,
                                    "confidence": conf,
                                    "volume_usd": vol_usd,
                                    "best_bid": best_bid,
                                    "best_ask": best_ask,
                                    "market_id": m.get("id"),
                                    "market_slug": m.get("slug"),
                                    "market_url": f"https://polymarket.com/event/{matched_event.get('slug')}" if matched_event.get("slug") else None,
                                    "question": m.get("question"),
                                    "scope": scope,
                                }
                                target_totals[str(line_val)] = entry
                                target_totals[f"{line_val:g}"] = entry

        has_ml = "home" in ml_data and "away" in ml_data
        has_spreads = bool(spreads_data)
        has_totals = bool(totals_data)
        slug_val = matched_event.get("slug")
        event_url = f"https://polymarket.com/event/{slug_val}" if slug_val else (f"https://polymarket.com/event/{matched_event.get('id')}" if matched_event.get("id") else None)
        summary = {
            "matched": True,
            "event_id": str(matched_event.get("id")),
            "title": matched_event.get("title"),
            "slug": slug_val,
            "event_url": event_url,
            "has_moneyline": has_ml,
            "moneyline": ml_data,
            "has_spreads": has_spreads,
            "spreads": spreads_data,
            "has_totals": has_totals,
            "totals": totals_data,
            "period_spreads": period_spreads,
            "period_totals": period_totals,
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
    def calculate_dynamic_ev_hurdle(odds: float) -> float:
        """Calculates continuous odds-adjusted alpha hurdle (Anti-Constant Mandate)."""
        if odds <= 1.0:
            return 0.05
        variance_term = max(0.2, odds - 1.0)
        return round(max(0.015, 0.030 / math.sqrt(variance_term)), 4)

    @staticmethod
    def calculate_stale_edge(poly_prob: float, sportsbook_odds: float, hurdle: Optional[float] = None) -> Dict[str, Any]:
        """Detect latency/stale-line arbitrage edge against sportsbook odds."""
        if poly_prob <= 0 or poly_prob >= 1 or sportsbook_odds <= 1.0:
            return {"is_stale": False, "edge_pct": 0.0, "expected_roi_pct": 0.0}

        sportsbook_implied = 1.0 / sportsbook_odds
        edge = poly_prob - sportsbook_implied
        expected_roi = (poly_prob * sportsbook_odds) - 1.0
        eff_hurdle = hurdle if hurdle is not None else 0.045

        return {
            "is_stale": edge >= eff_hurdle,
            "poly_probability": round(poly_prob, 4),
            "sportsbook_odds": round(sportsbook_odds, 3),
            "sportsbook_implied_prob": round(sportsbook_implied, 4),
            "edge_pct": round(edge * 100, 2),
            "expected_roi_pct": round(expected_roi * 100, 2),
            "dynamic_hurdle_pct": round(eff_hurdle * 100, 2),
        }
