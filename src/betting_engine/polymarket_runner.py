"""Autonomous Background Execution Runner for Polymarket Prediction Markets.

Periodically scans active liquid tournaments (NFL, NBA, NHL, etc.), maps
upcoming fixtures within the optimal execution window (10m to 24h pre-game),
evaluates continuous lines and Kelly stakes, executes Tier 3 proven-alpha bets
via the gasless Builder relayer, and triggers settlement and token auto-redemption.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow
from .polymarket_executor import PolymarketAutonomousExecutor, PreflightResult
from .polymarket_line_evaluator import ContinuousLineEvaluator
from .polymarket_rd_prioritizer import PolymarketRDPrioritizer
from .polymarket_service import PolymarketService
from .polymarket_settlement import PolymarketOutcomeSettler
from .providers import DATA

log = logging.getLogger(__name__)

RUNNER_STATE_FILE = DATA / "polymarket_runner_state.json"
_RUNNER_CYCLE_LOCK = threading.Lock()

# Explicit Canonical Taxonomy Mapping for Polymarket Gamma API sportsMarketType
POLYMARKET_STRUCTURED_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    # Full Game Main Markets
    "moneyline": ("full_game", "moneyline"),
    "spreads": ("full_game", "spreads"),
    "totals": ("full_game", "totals"),
    "tennis_game_handicap": ("full_game", "spreads"),
    "tennis_match_totals": ("full_game", "totals"),
    "both_teams_to_score": ("full_game", "both_score"),

    # First Half Main Markets
    "first_half_moneyline": ("half_1", "moneyline"),
    "first_half_spreads": ("half_1", "spreads"),
    "first_half_totals": ("half_1", "totals"),
    "both_teams_to_score_first_half": ("half_1", "both_score"),

    # Second Half Main Markets
    "second_half_moneyline": ("half_2", "moneyline"),
    "second_half_spreads": ("half_2", "spreads"),
    "second_half_totals": ("half_2", "totals"),
    "both_teams_to_score_second_half": ("half_2", "both_score"),

    # Quarters (NBA & NFL)
    "q1_moneyline": ("quarter_1", "moneyline"),
    "q1_spreads": ("quarter_1", "spreads"),
    "q1_totals": ("quarter_1", "totals"),
    "q2_moneyline": ("quarter_2", "moneyline"),
    "q2_spreads": ("quarter_2", "spreads"),
    "q2_totals": ("quarter_2", "totals"),
    "q3_moneyline": ("quarter_3", "moneyline"),
    "q3_spreads": ("quarter_3", "spreads"),
    "q3_totals": ("quarter_3", "totals"),
    "q4_moneyline": ("quarter_4", "moneyline"),
    "q4_spreads": ("quarter_4", "spreads"),
    "q4_totals": ("quarter_4", "totals"),

    # Tennis Sets (ATP & WTA)
    "tennis_first_set_winner": ("period_1", "moneyline"),
    "tennis_first_set_totals": ("period_1", "totals"),
    "tennis_set_handicap": ("full_game", "spreads"),
    "tennis_set_totals": ("full_game", "totals"),
}


class PolymarketAutonomousRunner:
    """Autonomous Background Loop executing trades on Polymarket CLOB."""

    def __init__(
        self,
        executor: Optional[PolymarketAutonomousExecutor] = None,
        settler: Optional[PolymarketOutcomeSettler] = None,
        line_evaluator: Optional[ContinuousLineEvaluator] = None,
        prioritizer: Optional[PolymarketRDPrioritizer] = None,
        service: Optional[PolymarketService] = None,
        db_path: Optional[Path] = None,
        state_file: Optional[Path] = None,
        min_ev_hurdle: float = 0.025,
        execution_window_hours: float = 72.0,
        min_pregame_minutes: float = 10.0,
    ):
        self.db_path = Path(db_path) if db_path else (DATA / "engine.sqlite3")
        self.state_file = Path(state_file) if state_file else RUNNER_STATE_FILE
        self.executor = executor or PolymarketAutonomousExecutor(db_path=self.db_path)
        self.settler = settler or PolymarketOutcomeSettler(db_path=self.db_path)
        self.line_evaluator = line_evaluator or ContinuousLineEvaluator()
        self.prioritizer = prioritizer or PolymarketRDPrioritizer(db_path=self.db_path)
        self.service = service or PolymarketService()
        self.min_ev_hurdle = min_ev_hurdle
        self.execution_window_hours = execution_window_hours
        self.min_pregame_minutes = min_pregame_minutes

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _get_active_tokens_already_traded(self) -> Set[str]:
        """Returns set of token_ids already ordered or staged to prevent double execution."""
        if not self.db_path.exists():
            return set()
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                cur = conn.cursor()
                rows = cur.execute(
                    "SELECT token_id FROM polymarket_orders WHERE status IN ('open', 'filled', 'staged', 'signed', 'relayed')"
                ).fetchall()
                return {r[0] for r in rows if r[0]}
        except Exception as exc:
            log.warning(f"Could not read existing polymarket orders: {exc}")
            return set()

    def _get_active_market_groups(self) -> Set[Tuple[str, str, str]]:
        """Returns set of (game_title, scope, market_family) tuples already traded to prevent ladder depletion."""
        if not self.db_path.exists():
            return set()
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                cur = conn.cursor()
                rows = cur.execute(
                    "SELECT game_title, scope, market_family FROM polymarket_orders WHERE status IN ('open', 'filled', 'staged', 'signed', 'relayed')"
                ).fetchall()
                return {(r[0], (r[1] or "full_game").lower(), r[2]) for r in rows if r[0] and r[2]}
        except Exception as exc:
            log.warning(f"Could not read existing polymarket market groups: {exc}")
            return set()

    def _load_domain_tips(self, leagues: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
        """Loads recent calibrated domain model tips from SQLite."""
        if not self.db_path.exists():
            return []
        try:
            with sqlite3.connect(self.db_path, timeout=5) as conn:
                cur = conn.cursor()
                # Check if tips table exists
                tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tips';").fetchone()
                if not tbl:
                    return []
                if leagues:
                    placeholders = ",".join("?" for _ in leagues)
                    rows = cur.execute(
                        f"""SELECT payload FROM tips 
                            WHERE json_extract(payload, '$.league') IN ({placeholders})
                            ORDER BY last_seen DESC LIMIT 4000;""",
                        leagues,
                    ).fetchall()
                else:
                    rows = cur.execute(
                        "SELECT payload FROM tips ORDER BY last_seen DESC LIMIT 4000;"
                    ).fetchall()
                return [json.loads(r[0]) for r in rows if r[0]]
        except Exception as exc:
            log.warning(f"Could not load domain tips: {exc}")
            return []

    def _lookup_game_forecast(
        self,
        league: str,
        teams: List[Dict[str, Any]],
        title: str,
        tips: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Matches a Polymarket event against calibrated domain model tips.

        Guarantees strict separation between 2-way Moneyline (incl. OT) and 3-way
        regulation, and strictly avoids taking totals/spreads probabilities as home_prob.
        """
        import math

        t_names = []
        poly_home_team = None
        poly_away_team = None
        for t in teams:
            if isinstance(t, dict):
                if t.get("name"):
                    t_names.append(t["name"].lower())
                if t.get("alias"):
                    t_names.append(t["alias"].lower())
                if t.get("ordering") == "home":
                    poly_home_team = t.get("name") or t.get("alias")
                elif t.get("ordering") == "away":
                    poly_away_team = t.get("name") or t.get("alias")
        t_abbr = [t.get("abbreviation", "").lower() for t in teams if isinstance(t, dict) and t.get("abbreviation")]

        matched_tips = []
        for tip in tips:
            if tip.get("league") != league:
                continue
            h = tip.get("home", "").lower()
            a = tip.get("away", "").lower()

            h_match = any(tn in h for tn in t_names if len(tn) > 2) or any(ab in h for ab in t_abbr if len(ab) > 1) or h in title.lower()
            a_match = any(tn in a for tn in t_names if len(tn) > 2) or any(ab in a for ab in t_abbr if len(ab) > 1) or a in title.lower()

            if h_match and a_match:
                matched_tips.append(tip)

        if not matched_tips:
            return None

        # Extract projected points from matching tips
        proj_h = None
        proj_a = None
        home_name = ""
        away_name = ""
        for tip in matched_tips:
            if tip.get("predicted_home_points") is not None and tip.get("predicted_away_points") is not None:
                proj_h = float(tip["predicted_home_points"])
                proj_a = float(tip["predicted_away_points"])
                home_name = tip.get("home", "")
                away_name = tip.get("away", "")
                break
            if not home_name and tip.get("home"):
                home_name = tip.get("home", "")
                away_name = tip.get("away", "")

        proj_total = (proj_h + proj_a) if (proj_h is not None and proj_a is not None) else None
        proj_spread = (proj_a - proj_h) if (proj_h is not None and proj_a is not None) else None

        # Determine calibrated home win probability (strictly 2-way Moneyline including OT)
        home_prob = None

        # Priority 1: Exact calibrated 2-way Moneyline tip for full game
        for tip in matched_tips:
            if tip.get("model_scope") == "full_game" and tip.get("market") in ("moneyline", "winner", "h2h"):
                p = tip.get("probability")
                if p is not None:
                    if tip.get("side") == "home" or (home_name and home_name.lower() in tip.get("selection", "").lower()):
                        home_prob = float(p)
                        break
                    elif tip.get("side") == "away" or (away_name and away_name.lower() in tip.get("selection", "").lower()):
                        home_prob = 1.0 - float(p)
                        break

        # Priority 2: If no direct moneyline tip but projected points exist, calculate using domain normal CDF
        if home_prob is None and proj_h is not None and proj_a is not None:
            sigma_map = {"NFL": 13.86, "NBA": 11.5, "NHL": 2.1, "MLB": 3.5}
            sigma = sigma_map.get(league, 12.0)
            diff = proj_h - proj_a
            # Standard Gaussian CDF: 0.5 * (1.0 + erf(diff / (sigma * sqrt(2))))
            home_prob = 0.5 * (1.0 + math.erf(diff / (sigma * math.sqrt(2.0))))

        # Priority 3: If only three_way regulation tips exist, adjust regulation draw probability:
        # P(2-way ML home) = P(reg home) + 0.5 * P(reg draw)
        if home_prob is None:
            p_reg_home = None
            p_reg_draw = None
            for tip in matched_tips:
                if tip.get("model_scope") == "full_game" and tip.get("market") in ("three_way", "1x2"):
                    side = tip.get("side")
                    p = tip.get("probability")
                    if p is not None:
                        if side == "home":
                            p_reg_home = float(p)
                        elif side == "draw":
                            p_reg_draw = float(p)
                        elif side == "away":
                            p_reg_home = 1.0 - float(p) - (p_reg_draw or 0.05)
            if p_reg_home is not None:
                p_draw = p_reg_draw if p_reg_draw is not None else 0.05
                home_prob = min(0.99, max(0.01, p_reg_home + 0.5 * p_draw))

        return {
            "tip": matched_tips[0],
            "proj_total": proj_total,
            "proj_spread": proj_spread,
            "proj_home": proj_h,
            "proj_away": proj_a,
            "home_prob": home_prob,
            "home_name": home_name or poly_home_team or "",
            "away_name": away_name or poly_away_team or "",
            "poly_home_team": poly_home_team,
            "poly_away_team": poly_away_team,
        }

    def run_execution_cycle(
        self,
        leagues: Tuple[str, ...] = ("NBA", "NFL", "NHL"),
        max_orders_per_cycle: Optional[int] = None,
        max_clob_checks: Optional[int] = None,
        mock_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Executes a single end-to-end autonomous scan, evaluation, order placement and settlement cycle."""
        if not _RUNNER_CYCLE_LOCK.acquire(blocking=False):
            log.info("[AUTONOMOUS RUNNER] Cycle already in progress, skipping concurrent trigger.")
            return {
                "timestamp": utcnow().isoformat(),
                "skipped": True,
                "reason": "concurrent_cycle_in_progress",
            }
        try:
            return self._run_execution_cycle_inner(
                leagues=leagues,
                max_orders_per_cycle=max_orders_per_cycle,
                max_clob_checks=max_clob_checks,
                mock_events=mock_events,
            )
        finally:
            _RUNNER_CYCLE_LOCK.release()

    def _run_execution_cycle_inner(
        self,
        leagues: Tuple[str, ...] = ("NBA", "NFL", "NHL"),
        max_orders_per_cycle: Optional[int] = None,
        max_clob_checks: Optional[int] = None,
        mock_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now = utcnow()
        now_iso = now.isoformat()
        cycle_start = time.time()

        traded_tokens = self._get_active_tokens_already_traded()
        traded_market_groups = self._get_active_market_groups()
        domain_tips = self._load_domain_tips(leagues=leagues)
        orders_placed: List[Dict[str, Any]] = []
        rejected_candidates: List[Dict[str, Any]] = []
        clob_checks_count = 0

        # 1. Fetch live events from Polymarket Gamma API or mock
        events = mock_events
        if events is None:
            events = []
            for l in leagues:
                try:
                    l_events = self.service.get_open_events(league=l, horizon_hours=self.execution_window_hours)
                    for ev in l_events:
                        ev["_league"] = l
                    events.extend(l_events)
                except Exception as exc:
                    log.warning(f"Failed to fetch active Polymarket events for {l}: {exc}")

        scanned_count = len(events)
        evaluated_markets_count = 0

        # Optimal pregame window: between 10 minutes and 36 hours before kickoff
        min_start = now + timedelta(minutes=self.min_pregame_minutes)
        max_start = now + timedelta(hours=self.execution_window_hours)

        # Filter and sort events by start time ascending (soonest kickoff first)
        qualifying_events = []
        for event in events:
            start_str = event.get("startTime") or event.get("startDate") or ""
            g_start = max_start
            if start_str:
                try:
                    g_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if not (min_start <= g_start <= max_start):
                        continue
                except Exception:
                    pass
            event["_start_dt"] = g_start
            qualifying_events.append(event)

        qualifying_events.sort(key=lambda ev: ev.get("_start_dt", max_start))

        # Ensure league is assigned to each qualifying event
        for ev in qualifying_events:
            if not ev.get("_league"):
                title_lower = (ev.get("title") or "").lower()
                for l in leagues:
                    if l.lower() in title_lower or any(l.lower() in m.get("question", "").lower() for m in ev.get("markets", [])):
                        ev["_league"] = l
                        break
                if not ev.get("_league"):
                    tags = [t.get("label", "").upper() for t in ev.get("tags", []) if isinstance(t, dict)]
                    for l in leagues:
                        if l in tags:
                            ev["_league"] = l
                            break

        # Ensure balanced multi-sport coverage across active leagues
        events_by_league: Dict[str, List[Dict[str, Any]]] = {}
        for ev in qualifying_events:
            l_key = ev.get("_league") or "OTHER"
            events_by_league.setdefault(l_key, []).append(ev)

        interleaved_events = []
        max_depth = max((len(evs) for evs in events_by_league.values()), default=0)
        for d in range(max_depth):
            for l_name in leagues:
                if l_name in events_by_league and d < len(events_by_league[l_name]):
                    interleaved_events.append(events_by_league[l_name][d])
            if "OTHER" in events_by_league and d < len(events_by_league["OTHER"]):
                interleaved_events.append(events_by_league["OTHER"][d])

        if not interleaved_events:
            interleaved_events = qualifying_events

        for event in interleaved_events:
            if max_orders_per_cycle is not None and len(orders_placed) >= max_orders_per_cycle:
                break
            if max_clob_checks is not None and clob_checks_count >= max_clob_checks:
                break

            title = event.get("title", "")
            title_lower = title.lower()
            # Strictly skip player props events, individual player stat markets
            if any(prop_kw in title_lower for prop_kw in ("player prop", "player props", "- props", "props -", "props", "touchdowns", "receptions", "passing")):
                continue

            start_str = event.get("startTime") or event.get("startDate") or ""
            markets = event.get("markets", [])

            # Match league
            league = event.get("_league")
            if not league:
                for l in leagues:
                    if l.lower() in title_lower or any(l.lower() in m.get("question", "").lower() for m in markets):
                        league = l
                        break
            if not league:
                # Try inferring from tags
                tags = [t.get("label", "").upper() for t in event.get("tags", []) if isinstance(t, dict)]
                for l in leagues:
                    if l in tags:
                        league = l
                        break

            if not league:
                continue

            forecast = self._lookup_game_forecast(
                league=league,
                teams=event.get("teams", []),
                title=title,
                tips=domain_tips,
            )

            # Collect candidate bets for this event to select optimal line per group
            event_candidates_by_group: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}

            for market in markets:
                if max_orders_per_cycle is not None and len(orders_placed) >= max_orders_per_cycle:
                    break

                evaluated_markets_count += 1
                q = market.get("question", "")
                q_low = q.lower()
                sports_type = (market.get("sportsMarketType") or "").strip().lower()

                # Deterministic Scope and Family resolution via Structured Taxonomy Map
                scope = None
                family = None

                if sports_type:
                    if sports_type in POLYMARKET_STRUCTURED_TYPE_MAP:
                        scope, family = POLYMARKET_STRUCTURED_TYPE_MAP[sports_type]
                    else:
                        # Polymarket provided a specific sportsMarketType that is not in our proven taxonomy
                        # (e.g. passing_touchdowns, receptions, points, assists, rebounds, exact_margin, race_to_points)
                        # Strictly skip without falling through to fuzzy regex!
                        continue
                else:
                    # Fallback text parsing ONLY when sportsMarketType is not provided
                    if any(prop in q_low for prop in ("prop", "player", "touchdown", "reception", "pass", "rush", "assist", "rebound", "point", "yard", "strikeout", "tackle", "sack", "interception", "race to", "margin", "both teams to score", "first to", "team total")):
                        continue
                    if q.strip().lower() == title.strip().lower() and not market.get("line"):
                        scope, family = "full_game", "moneyline"
                    elif q_low.startswith("spread:") and market.get("line") is not None:
                        scope, family = "full_game", "spreads"
                    elif ("over/under" in q_low or "o/u" in q_low) and market.get("line") is not None:
                        scope, family = "full_game", "totals"
                    else:
                        # Unmodeled prop, player stat, exact margin, race to points, or team derivative -> strictly skip
                        continue

                # Check Tier 3 eligibility gate
                line_val = None
                try:
                    if market.get("line"):
                        line_val = float(market["line"])
                except (ValueError, TypeError):
                    pass

                is_eligible, elig_reason, stake_cap = self.executor.is_market_eligible(
                    league=league,
                    scope=scope,
                    market_family=family,
                    line=line_val,
                )

                if not is_eligible:
                    continue

                # Single optimal line invariant: Check if this market group (game, scope, family) is already active
                market_group_key = (title, scope.lower(), family)
                if market_group_key in traded_market_groups:
                    continue

                clob_token_ids = market.get("clobTokenIds") or []
                outcomes = market.get("outcomes") or ["Yes", "No"]
                outcome_prices = market.get("outcomePrices") or []

                if isinstance(clob_token_ids, str):
                    try:
                        clob_token_ids = json.loads(clob_token_ids)
                    except Exception:
                        clob_token_ids = [clob_token_ids]

                if isinstance(outcomes, str):
                    try:
                        outcomes = json.loads(outcomes)
                    except Exception:
                        outcomes = [outcomes]

                if isinstance(outcome_prices, str):
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except Exception:
                        outcome_prices = []

                # Evaluate available contract tokens
                for idx, token_id in enumerate(clob_token_ids):
                    if not token_id or token_id in traded_tokens:
                        continue

                    outcome_label = outcomes[idx] if idx < len(outcomes) else f"Outcome {idx}"
                    poly_price = 0.50
                    if idx < len(outcome_prices):
                        try:
                            poly_price = float(outcome_prices[idx])
                        except (ValueError, TypeError):
                            pass

                    # Independent probability modeling: Strictly derive from domain forecast models
                    model_prob = None
                    outcome_spread = None
                    if forecast and forecast.get("proj_total") is not None and line_val is not None and family == "totals":
                        side = "over" if "over" in outcome_label.lower() or outcome_label == "Yes" else "under"
                        model_prob = self.line_evaluator.evaluate_total(
                            league=league,
                            base_line=float(forecast["proj_total"]),
                            base_prob=0.50,
                            base_side="over",
                            target_line=line_val,
                            target_side=side,
                            scope=scope,
                        )
                    elif forecast and family == "spreads" and line_val is not None:
                        # Outcome spread handicap:
                        # Polymarket defines line_val for Outcome 0 (Named Team in question).
                        # Outcome 1 (Opponent) receives the inverse handicap (-line_val).
                        outcome_spread = float(line_val) if idx == 0 else -float(line_val)

                        h_name = (forecast.get("home_name") or "").lower()
                        a_name = (forecast.get("away_name") or "").lower()
                        out_low = outcome_label.lower()
                        q_low = q.lower()

                        is_home = None
                        if h_name and (out_low in h_name or h_name in out_low or any(w in out_low for w in h_name.split() if len(w) > 3)):
                            is_home = True
                        elif a_name and (out_low in a_name or a_name in out_low or any(w in out_low for w in a_name.split() if len(w) > 3)):
                            is_home = False
                        elif out_low == "yes":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) > 3)):
                                is_home = True
                            elif a_name and (a_name in q_low or any(w in q_low for w in a_name.split() if len(w) > 3)):
                                is_home = False
                        elif out_low == "no":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) > 3)):
                                is_home = False
                            elif a_name and (a_name in q_low or any(w in q_low for w in a_name.split() if len(w) > 3)):
                                is_home = True

                        if is_home is None:
                            if h_name and any(w in q_low for w in h_name.split() if len(w) > 3):
                                is_home = (idx == 0)
                            elif a_name and any(w in q_low for w in a_name.split() if len(w) > 3):
                                is_home = (idx != 0)
                            else:
                                is_home = (idx == 0)

                        proj_h = forecast.get("proj_home")
                        proj_a = forecast.get("proj_away")
                        if proj_h is not None and proj_a is not None:
                            model_prob = self.line_evaluator.evaluate_team_spread(
                                league=league,
                                proj_home=float(proj_h),
                                proj_away=float(proj_a),
                                is_home=is_home,
                                spread_line=outcome_spread,
                                scope=scope,
                            )
                        elif forecast.get("proj_spread") is not None:
                            proj_h_eff = 0.0
                            proj_a_eff = float(forecast["proj_spread"])
                            model_prob = self.line_evaluator.evaluate_team_spread(
                                league=league,
                                proj_home=proj_h_eff,
                                proj_away=proj_a_eff,
                                is_home=is_home,
                                spread_line=outcome_spread,
                                scope=scope,
                            )
                    elif forecast and family == "moneyline" and forecast.get("home_prob") is not None:
                        h_name = (forecast.get("home_name") or "").lower()
                        a_name = (forecast.get("away_name") or "").lower()
                        out_low = outcome_label.lower()
                        q_low = q.lower()

                        if h_name and (h_name in out_low or any(w in out_low for w in h_name.split() if len(w) > 3)):
                            is_home = True
                        elif a_name and (a_name in out_low or any(w in out_low for w in a_name.split() if len(w) > 3)):
                            is_home = False
                        elif out_low == "yes":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) > 3)):
                                is_home = True
                            else:
                                is_home = False
                        elif out_low == "no":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) > 3)):
                                is_home = False
                            else:
                                is_home = True
                        else:
                            is_home = (idx == 0)

                        model_prob = float(forecast["home_prob"]) if is_home else (1.0 - float(forecast["home_prob"]))
                    else:
                        # Strictly skip unmodeled or uncalibrated contracts - synthetic price shifts are forbidden!
                        continue

                    if model_prob is None or model_prob <= 0.01 or model_prob >= 0.99:
                        continue

                    # Pre-screen: Only check depth if contract is liquid and offers positive nominal EV
                    if poly_price <= 0.05 or poly_price >= 0.95:
                        continue

                    nominal_ev = (model_prob / poly_price) - 1.0
                    if nominal_ev < self.min_ev_hurdle:
                        continue

                    if max_clob_checks is not None and clob_checks_count >= max_clob_checks:
                        break
                    clob_checks_count += 1

                    # Run preflight check (Depth & Slippage Guard)
                    preflight = self.executor.preflight_check(
                        token_id=token_id,
                        side="BUY",
                        requested_stake=stake_cap,
                        model_prob=model_prob,
                        min_ev=self.min_ev_hurdle,
                    )

                    if not preflight.ok:
                        rejected_candidates.append({
                            "token_id": token_id,
                            "game": title,
                            "selection": outcome_label,
                            "reason": preflight.reason,
                        })
                        continue

                    # Group candidate by (scope, family) to select single optimal alpha line/side per market
                    group_key = (scope, family)
                    contract_line = outcome_spread if family == "spreads" else line_val
                    event_candidates_by_group.setdefault(group_key, []).append({
                        "token_id": token_id,
                        "line": contract_line,
                        "selection": outcome_label,
                        "model_prob": model_prob,
                        "ev": preflight.ev,
                        "preflight": preflight,
                        "stake_cap": stake_cap,
                        "scope": scope,
                        "family": family,
                    })

            # Submit only the single highest-EV optimal line/side per (scope, family)
            for (grp_scope, grp_family), cands in event_candidates_by_group.items():
                if max_orders_per_cycle is not None and len(orders_placed) >= max_orders_per_cycle:
                    break

                best_cand = max(cands, key=lambda c: c["ev"])

                order_res = self.executor.stage_and_submit_order(
                    league=league,
                    game_title=title,
                    start_time=start_str or now_iso,
                    market_family=best_cand["family"],
                    selection=best_cand["selection"],
                    side="BUY",
                    line=best_cand["line"],
                    token_id=best_cand["token_id"],
                    model_prob=best_cand["model_prob"],
                    scope=best_cand["scope"],
                    requested_stake=best_cand["stake_cap"],
                    order_type="GTC",
                )

                if order_res.get("ok"):
                    order_res["token_id"] = best_cand["token_id"]
                    orders_placed.append(order_res)
                    traded_tokens.add(best_cand["token_id"])
                    traded_market_groups.add((title, best_cand["scope"].lower(), best_cand["family"]))
                    log.info(f"[AUTONOMOUS RUNNER] Placed Polymarket Order: {order_res.get('client_order_id')} ({title} {best_cand['selection']} line={best_cand['line']} EV=+{best_cand['ev']*100:.1f}%)")

        # 2. Trigger Outcome Settlement & Token Auto-Redemption
        settle_res = self.settler.settle_orders()

        cycle_duration = round(time.time() - cycle_start, 3)
        result = {
            "timestamp": now_iso,
            "duration_seconds": cycle_duration,
            "scanned_events_count": scanned_count,
            "evaluated_markets_count": evaluated_markets_count,
            "orders_placed_count": len(orders_placed),
            "orders_placed": orders_placed,
            "rejected_candidates_count": len(rejected_candidates),
            "rejected_candidates": rejected_candidates[:25],
            "settlement": settle_res,
        }

        # Persist runner state
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except Exception as exc:
            log.warning(f"Could not persist runner state: {exc}")

        return result

    def get_runner_status(self) -> Dict[str, Any]:
        """Provides status of the background execution runner."""
        last_state = {}
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    last_state = json.load(f)
            except Exception:
                pass

        return {
            "runner_active": self._running or True,
            "min_ev_hurdle_pct": self.min_ev_hurdle * 100.0,
            "execution_window_hours": self.execution_window_hours,
            "min_pregame_minutes": self.min_pregame_minutes,
            "last_cycle": last_state,
        }
