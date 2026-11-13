"""Autonomous Background Execution Runner for Polymarket Prediction Markets.

Periodically scans active liquid tournaments (NFL, NBA, NHL, etc.), maps
upcoming fixtures within the optimal execution window (10m to 24h pre-game),
evaluates continuous lines and Kelly stakes, executes Tier 3 proven-alpha bets
via the gasless Builder relayer, and triggers settlement and token auto-redemption.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow
from .polymarket_executor import PolymarketAutonomousExecutor, PreflightResult, PRIMARY_LEAGUES
from .polymarket_line_evaluator import ContinuousLineEvaluator
from .polymarket_rd_prioritizer import PolymarketRDPrioritizer
from .polymarket_service import PolymarketService
from .polymarket_settlement import PolymarketOutcomeSettler
from .providers import DATA

log = logging.getLogger(__name__)

RUNNER_STATE_FILE = DATA / "polymarket_runner_state.json"
_RUNNER_CYCLE_LOCK = threading.Lock()
DEFAULT_RUNNER_LEAGUES: Tuple[str, ...] = ("NBA", "NFL", "NHL", "EPL", "UCL", "UEL", "MLS", "NCAAF", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "ATP", "WTA")

# Explicit Canonical Taxonomy Mapping for Polymarket Gamma API sportsMarketType
# Qualified with authoritative sport/league scope to prevent cross-sport taxonomic bleed
POLYMARKET_STRUCTURED_TAXONOMY: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    # Full Game Main Markets (Core Leagues)
    "moneyline": ("full_game", "moneyline", ("NBA", "NFL", "NHL", "MLB", "WNBA", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "MLS", "EPL", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA", "ATP", "WTA", "CHALLENGER")),
    "spreads": ("full_game", "spreads", ("NBA", "NFL", "NHL", "MLB", "WNBA", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "MLS", "EPL", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA", "ATP", "WTA", "CHALLENGER")),
    "totals": ("full_game", "totals", ("NBA", "NFL", "NHL", "MLB", "WNBA", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "MLS", "EPL", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA", "ATP", "WTA", "CHALLENGER")),

    # Soccer Specific Markets (EPL, MLS, UCL, UEL, LALIGA, BUNDESLIGA, SERIEA)
    "both_teams_to_score": ("full_game", "both_score", ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "both_teams_to_score_first_half": ("half_1", "both_score", ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "both_teams_to_score_second_half": ("half_2", "both_score", ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "soccer_halftime_result": ("half_1", "three_way", ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "soccer_second_half_result": ("half_2", "three_way", ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),

    # Tennis Specific Markets (ATP, WTA, CHALLENGER)
    "tennis_first_set_winner": ("period_1", "moneyline", ("ATP", "WTA", "CHALLENGER")),
    "tennis_first_set_totals": ("period_1", "totals", ("ATP", "WTA", "CHALLENGER")),
    "tennis_set_winner": ("period_2", "moneyline", ("ATP", "WTA", "CHALLENGER")),
    "tennis_set_games_totals": ("period_2", "totals", ("ATP", "WTA", "CHALLENGER")),
    "tennis_game_handicap": ("full_game", "spreads", ("ATP", "WTA", "CHALLENGER")),
    "tennis_match_totals": ("full_game", "totals", ("ATP", "WTA", "CHALLENGER")),
    "tennis_set_handicap": ("full_game", "set_handicap", ("ATP", "WTA", "CHALLENGER")),
    "tennis_set_totals": ("full_game", "total_sets", ("ATP", "WTA", "CHALLENGER")),

    # Halves (Football, Basketball, Soccer)
    "first_half_moneyline": ("half_1", "moneyline", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA")),
    "first_half_spreads": ("half_1", "spreads", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA", "EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "first_half_totals": ("half_1", "totals", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA", "EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "second_half_moneyline": ("half_2", "moneyline", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA")),
    "second_half_spreads": ("half_2", "spreads", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA", "EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),
    "second_half_totals": ("half_2", "totals", ("NBA", "NFL", "NCAAF", "CFB", "CBB", "NCAAB", "NCAAM", "NCAAW", "CWBB", "WNBA", "EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")),

    # Quarters (NBA, NFL, NCAAF, WNBA, NCAAM, NCAAW)
    "q1_moneyline": ("quarter_1", "moneyline", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q1_spreads": ("quarter_1", "spreads", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q1_totals": ("quarter_1", "totals", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q2_moneyline": ("quarter_2", "moneyline", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q2_spreads": ("quarter_2", "spreads", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q2_totals": ("quarter_2", "totals", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q3_moneyline": ("quarter_3", "moneyline", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q3_spreads": ("quarter_3", "spreads", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q3_totals": ("quarter_3", "totals", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q4_moneyline": ("quarter_4", "moneyline", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q4_spreads": ("quarter_4", "spreads", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
    "q4_totals": ("quarter_4", "totals", ("NBA", "NFL", "NCAAF", "WNBA", "NCAAM", "NCAAW")),
}

# Derived canonical map for backwards compatibility
POLYMARKET_STRUCTURED_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    k: (v[0], v[1]) for k, v in POLYMARKET_STRUCTURED_TAXONOMY.items()
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
        min_ev_hurdle: float = 0.04,
        execution_window_hours: float = 72.0,
        min_pregame_minutes: float = 10.0,
        require_tier_4: bool = True,
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
        self.require_tier_4 = require_tier_4

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _get_active_tokens_already_traded(self) -> Set[str]:
        """Returns set of token_ids already ordered or staged to prevent double execution."""
        if not self.db_path.exists():
            return set()
        try:
            with closing(sqlite3.connect(self.db_path, timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
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
            with closing(sqlite3.connect(self.db_path, timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
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
            with closing(sqlite3.connect(self.db_path, timeout=30.0)) as conn:
                conn.execute("PRAGMA busy_timeout = 30000;")
                cur = conn.cursor()
                # Check if tips table exists
                tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tips';").fetchone()
                if not tbl:
                    return []
                if leagues:
                    expanded_leagues = set(str(l).upper() for l in leagues)
                    if "CBB" in expanded_leagues or "NCAAM" in expanded_leagues:
                        expanded_leagues.update(["CBB", "NCAAM", "NCAAB", "COLLEGE_BASKETBALL"])
                    if "NCAAF" in expanded_leagues or "CFB" in expanded_leagues:
                        expanded_leagues.update(["NCAAF", "CFB", "COLLEGE_FOOTBALL"])
                    if "NCAAW" in expanded_leagues or "CWBB" in expanded_leagues:
                        expanded_leagues.update(["NCAAW", "CWBB"])
                    q_leagues = tuple(sorted(expanded_leagues))
                    placeholders = ",".join("?" for _ in q_leagues)
                    rows = cur.execute(
                        f"""SELECT payload FROM tips 
                            WHERE json_extract(payload, '$.league') IN ({placeholders})
                            ORDER BY last_seen DESC LIMIT 4000;""",
                        q_leagues,
                    ).fetchall()
                else:
                    rows = cur.execute(
                        "SELECT payload FROM tips ORDER BY last_seen DESC LIMIT 4000;"
                    ).fetchall()
                tips = [json.loads(r[0]) for r in rows if r[0]]

            # Also enrich with latest publication games and calibrated pregame forecasts
            # to match upcoming scheduled fixtures across 72h even before Stake contracts are posted.
            try:
                from .storage import Store
                store = Store(self.db_path)
                latest_pub = store.latest()
                if latest_pub:
                    games_by_id = {g['id']: g for g in latest_pub.get('games', []) if isinstance(g, dict) and g.get('id')}
                    existing_pairs = {(str(t.get('league')).upper(), str(t.get('home')).lower(), str(t.get('away')).lower()) for t in tips}
                    for gid, fc in latest_pub.get('forecasts', {}).items():
                        g = games_by_id.get(gid)
                        if not g or not isinstance(fc, dict):
                            continue
                        home = g.get('home')
                        away = g.get('away')
                        league = (g.get('league') or '').upper()
                        if not home or not away or not league:
                            continue
                        pair_key = (league, home.lower(), away.lower())
                        if pair_key in existing_pairs:
                            continue
                        existing_pairs.add(pair_key)
                        h_prob = fc.get('home_probability')
                        h_pts = fc.get('home_points')
                        a_pts = fc.get('away_points')
                        tips.append({
                            'id': f"fc_{gid}_ml",
                            'game_id': gid,
                            'league': league,
                            'home': home,
                            'away': away,
                            'market': 'moneyline',
                            'model_scope': 'full_game',
                            'side': 'home',
                            'probability': h_prob,
                            'predicted_home_points': h_pts,
                            'predicted_away_points': a_pts,
                            'allocation_code': 'prospective_domain_forecast',
                            'funding_veto': False,
                        })
            except Exception as exc:
                log.warning(f"Could not enrich tips from latest publication forecasts: {exc}")

            return tips
        except Exception as exc:
            log.warning(f"Could not load domain tips: {exc}")
            return []

    def _lookup_game_forecast(
        self,
        league: str,
        teams: List[Dict[str, Any]],
        title: str,
        tips: List[Dict[str, Any]],
        return_failure_reason: bool = False,
    ) -> Any:
        """Matches a Polymarket event against calibrated domain model tips.

        Guarantees strict separation between 2-way Moneyline (incl. OT) and 3-way
        regulation, and strictly avoids taking totals/spreads probabilities as home_prob.
        """
        import math

        league_up = (league or "").strip().upper()
        allowed_leagues = {league_up}
        if league_up in ("NCAAF", "CFB", "COLLEGE_FOOTBALL"):
            allowed_leagues = {"NCAAF", "CFB", "COLLEGE_FOOTBALL"}
        elif league_up in ("CBB", "NCAAB", "NCAAM", "COLLEGE_BASKETBALL"):
            allowed_leagues = {"CBB", "NCAAB", "NCAAM", "COLLEGE_BASKETBALL"}
        elif league_up in ("CWBB", "NCAAW"):
            allowed_leagues = {"CWBB", "NCAAW"}

        league_tips = [t for t in tips if (t.get("league") or "").upper() in allowed_leagues]
        if not league_tips:
            return (None, "no_domain_tips_for_league") if return_failure_reason else None

        import unicodedata, re

        def _clean_str(s: str) -> str:
            if not s:
                return ""
            norm = unicodedata.normalize('NFKD', str(s)).encode('ASCII', 'ignore').decode('utf-8').lower()
            norm = re.sub(r'\([a-zA-Z0-9\s\.]+\)', '', norm)
            norm = re.sub(r'[^a-z0-9\s]', ' ', norm)
            return ' '.join(norm.split())

        STOPWORDS = {
            'state', 'saint', 'north', 'south', 'east', 'west', 'city',
            'united', 'real', 'club', 'town', 'athletic', 'sporting', 'inter',
            'fc', 'cf', 'sc', 'ac', 'as', 'afc', 'rb'
        }

        def _name_variants(s: str) -> Set[str]:
            cleaned = _clean_str(s)
            if not cleaned:
                return set()
            variants = {cleaned}
            if ',' in str(s):
                parts = [_clean_str(p) for p in str(s).split(',') if p.strip()]
                if len(parts) == 2:
                    variants.add(f"{parts[1]} {parts[0]}")
            for token in cleaned.split():
                if len(token) >= 4 and token not in STOPWORDS:
                    variants.add(token)
            return variants

        t_variants: Set[str] = set()
        poly_home_team = None
        poly_away_team = None
        poly_home_vars: Set[str] = set()
        poly_away_vars: Set[str] = set()
        for t in teams:
            if isinstance(t, dict):
                n_str = t.get("name") or ""
                a_str = t.get("alias") or ""
                vars_t = _name_variants(n_str) | _name_variants(a_str)
                t_variants |= vars_t
                if t.get("ordering") == "home":
                    poly_home_team = n_str or a_str
                    poly_home_vars = vars_t
                elif t.get("ordering") == "away":
                    poly_away_team = n_str or a_str
                    poly_away_vars = vars_t
        t_abbr = [_clean_str(t.get("abbreviation", "")) for t in teams if isinstance(t, dict) and t.get("abbreviation")]
        t_abbr = [ab for ab in t_abbr if len(ab) >= 2]
        
        # Extract base matchup title, resolving tournament prefixes and derivative suffixes
        matchup_title = title or ""
        if ":" in matchup_title:
            parts = [p.strip() for p in matchup_title.split(":") if p.strip()]
            for p in parts:
                if " vs" in p.lower() or " v " in p.lower():
                    matchup_title = p
                    break
        if " - " in matchup_title:
            parts = [p.strip() for p in matchup_title.split(" - ") if p.strip()]
            for p in parts:
                if " vs" in p.lower() or " v " in p.lower():
                    matchup_title = p
                    break

        title_cleaned = _clean_str(f"{matchup_title} {title}")
        title_tokens = set(title_cleaned.split())

        matched_tips = []
        for tip in league_tips:
            h_raw = tip.get("home", "")
            a_raw = tip.get("away", "")
            h_clean = _clean_str(h_raw)
            a_clean = _clean_str(a_raw)
            h_vars = _name_variants(h_raw)
            a_vars = _name_variants(a_raw)

            h_match = (
                bool(h_vars & poly_home_vars)
                or any(v in h_clean for v in poly_home_vars if len(v) >= 3)
                or any(v in h_clean for v in t_variants if len(v) >= 3)
                or any(ab in h_clean.split() for ab in t_abbr)
                or (h_clean and h_clean in title_cleaned)
                or bool(h_vars & title_tokens)
                or any(v in title_cleaned for v in h_vars if len(v) >= 4 and v not in STOPWORDS)
            )
            a_match = (
                bool(a_vars & poly_away_vars)
                or any(v in a_clean for v in poly_away_vars if len(v) >= 3)
                or any(v in a_clean for v in t_variants if len(v) >= 3)
                or any(ab in a_clean.split() for ab in t_abbr)
                or (a_clean and a_clean in title_cleaned)
                or bool(a_vars & title_tokens)
                or any(v in title_cleaned for v in a_vars if len(v) >= 4 and v not in STOPWORDS)
            )

            if h_match and a_match:
                matched_tips.append(tip)

        if not matched_tips:
            return (None, "team_names_unmatched") if return_failure_reason else None

        # Filter out quarantined / demoted tips to prevent leaking unvalidated/degraded model forecasts
        active_tips = [
            t for t in matched_tips
            if t.get("allocation_code") not in ("capital_defense_quarantined", "quarantined", "demoted")
            and not t.get("funding_veto")
        ]
        if not active_tips:
            log.info(f"Skipping Polymarket event {title}: all matched domain tips are quarantined/demoted.")
            return (None, "tips_quarantined_or_demoted") if return_failure_reason else None
        matched_tips = active_tips

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
            sigma_map = {
                "NFL": 13.86,
                "NBA": 11.5,
                "NHL": 2.1,
                "MLB": 3.5,
                "NCAAF": 16.0,
                "CFB": 16.0,
                "CBB": 11.5,
                "NCAAB": 11.5,
                "NCAAM": 11.5,
                "NCAAW": 11.5,
                "CWBB": 11.5,
                "MLS": 1.3,
                "EPL": 1.25,
                "UCL": 1.25,
                "UEL": 1.25,
                "ATP": 4.0,
                "WTA": 3.8,
            }
            sigma = sigma_map.get(league_up, 12.0)
            diff = proj_h - proj_a
            # Standard Gaussian CDF: 0.5 * (1.0 + erf(diff / (sigma * sqrt(2))))
            home_prob = 0.5 * (1.0 + math.erf(diff / (sigma * math.sqrt(2.0))))

        # Collect distinct 3-way probabilities for soccer/regulation sports
        p_reg_home = None
        p_reg_draw = None
        p_reg_away = None
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
                        p_reg_away = float(p)

        if p_reg_home is not None and p_reg_draw is not None and p_reg_away is None:
            p_reg_away = max(0.01, 1.0 - p_reg_home - p_reg_draw)
        elif p_reg_away is not None and p_reg_draw is not None and p_reg_home is None:
            p_reg_home = max(0.01, 1.0 - p_reg_away - p_reg_draw)
        elif p_reg_home is not None and p_reg_away is not None and p_reg_draw is None:
            p_reg_draw = max(0.01, 1.0 - p_reg_home - p_reg_away)

        if home_prob is None and p_reg_home is not None:
            p_draw = p_reg_draw if p_reg_draw is not None else 0.25
            home_prob = min(0.99, max(0.01, p_reg_home + 0.5 * p_draw))

        fc_result = {
            "tip": matched_tips[0],
            "proj_total": proj_total,
            "proj_spread": proj_spread,
            "proj_home": proj_h,
            "proj_away": proj_a,
            "home_prob": home_prob,
            "draw_prob": p_reg_draw,
            "away_prob": p_reg_away,
            "home_name": home_name or poly_home_team or "",
            "away_name": away_name or poly_away_team or "",
            "poly_home_team": poly_home_team or home_name or "",
            "poly_away_team": poly_away_team or away_name or "",
        }
        return (fc_result, None) if return_failure_reason else fc_result

    def run_execution_cycle(
        self,
        leagues: Tuple[str, ...] = DEFAULT_RUNNER_LEAGUES,
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
        leagues: Tuple[str, ...] = DEFAULT_RUNNER_LEAGUES,
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
        unmatched_events: List[Dict[str, Any]] = []
        unmapped_market_types: Set[str] = set()
        data_deficiency_gaps: List[Dict[str, Any]] = []
        model_deficit_gaps: List[Dict[str, Any]] = []
        clob_checks_count = 0

        # 0. Smart Order Routing: Process open Maker fallbacks & adverse selection circuit breaker
        sor_actions = []
        try:
            sor_actions = self.executor.process_open_maker_fallbacks(
                domain_tips=domain_tips,
                min_ev_hurdle=self.min_ev_hurdle,
            )
        except Exception as exc:
            log.warning(f"Error running SOR maker fallbacks: {exc}")

        # 1. Fetch live events from Polymarket Gamma API or mock (Parallel Concurrent Fetch)
        events = mock_events
        if events is None:
            events = []
            from concurrent.futures import ThreadPoolExecutor
            def _fetch_league_events(l: str) -> List[Dict[str, Any]]:
                try:
                    l_events = self.service.get_open_events(league=l, horizon_hours=self.execution_window_hours)
                    for ev in l_events:
                        ev["_league"] = l
                    return l_events
                except Exception as exc:
                    log.warning(f"Failed to fetch active Polymarket events for {l}: {exc}")
                    return []

            with ThreadPoolExecutor(max_workers=min(8, len(leagues))) as pool:
                for l_res in pool.map(_fetch_league_events, leagues):
                    events.extend(l_res)

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

        # Strict Mandate: NBA and NFL are Primary Anchor Leagues (processed first)
        primary_events = [ev for ev in qualifying_events if (ev.get("_league") or "") in PRIMARY_LEAGUES]
        secondary_events = [ev for ev in qualifying_events if (ev.get("_league") or "") not in PRIMARY_LEAGUES]

        primary_events.sort(key=lambda ev: ev.get("_start_dt", max_start))
        secondary_events.sort(key=lambda ev: ev.get("_start_dt", max_start))

        # Interleave secondary leagues among themselves to prevent a single secondary tournament from dominating
        sec_by_league: Dict[str, List[Dict[str, Any]]] = {}
        for ev in secondary_events:
            l_key = ev.get("_league") or "OTHER"
            sec_by_league.setdefault(l_key, []).append(ev)

        sec_interleaved = []
        max_sec_depth = max((len(evs) for evs in sec_by_league.values()), default=0)
        for d in range(max_sec_depth):
            for l_name in leagues:
                if l_name not in PRIMARY_LEAGUES and l_name in sec_by_league and d < len(sec_by_league[l_name]):
                    sec_interleaved.append(sec_by_league[l_name][d])
            if "OTHER" in sec_by_league and d < len(sec_by_league["OTHER"]):
                sec_interleaved.append(sec_by_league["OTHER"][d])

        ordered_events = primary_events + (sec_interleaved if sec_interleaved else secondary_events)
        if not ordered_events:
            ordered_events = qualifying_events

        primary_orders_count = 0
        secondary_orders_count = 0

        for event in ordered_events:
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

            forecast, fc_fail_reason = self._lookup_game_forecast(
                league=league,
                teams=event.get("teams", []),
                title=title,
                tips=domain_tips,
                return_failure_reason=True,
            )
            if forecast is None and fc_fail_reason:
                unmatched_events.append({
                    "league": league,
                    "event_title": title,
                    "teams": event.get("teams", []),
                    "reason": fc_fail_reason,
                })

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
                    if sports_type in POLYMARKET_STRUCTURED_TAXONOMY:
                        tax_sc, tax_fam, tax_lgs = POLYMARKET_STRUCTURED_TAXONOMY[sports_type]
                        if league in tax_lgs:
                            scope, family = tax_sc, tax_fam
                        else:
                            # Cross-sport taxonomy mismatch (e.g. tennis market in NHL event)
                            continue
                    elif sports_type in POLYMARKET_STRUCTURED_TYPE_MAP:
                        scope, family = POLYMARKET_STRUCTURED_TYPE_MAP[sports_type]
                    else:
                        # Polymarket provided a specific sportsMarketType that is not in our proven taxonomy
                        # Check if it's an unmodeled prop vs an unmapped game/period derivative
                        is_prop = any(prop in sports_type for prop in (
                            "touchdown", "reception", "pass", "rush", "assist", "rebound", "point", "yard",
                            "strikeout", "tackle", "sack", "interception", "margin", "race", "corner",
                            "goal", "shot", "save", "team_total", "team_totals", "overtime", "odd_even",
                            "pick_six", "fourth_down", "field_goal", "first_down", "exact_score",
                            "first_to_score", "score_first", "two_point", "completed_match",
                            "three", "threes", "steal", "block", "turnover", "foul", "home_run", "hit", "rbi"
                        ))
                        if not is_prop:
                            unmapped_market_types.add(sports_type)
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
                    require_tier_4=self.require_tier_4,
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
                        if model_prob is None:
                            model_deficit_gaps.append({
                                "league": league,
                                "scope": scope,
                                "family": family,
                                "segment_key": f"{league}|{scope}|{family}",
                                "title": title,
                                "line": line_val,
                                "reason": f"Extreme alternate total line {line_val} on {title} exceeds Gaussian shift bounds. Formulate calibrated discrete tail distribution from first principles for tail pricing.",
                            })
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
                        if h_name and (out_low in h_name or h_name in out_low or any(w in out_low for w in h_name.split() if len(w) >= 3)):
                            is_home = True
                        elif a_name and (out_low in a_name or a_name in out_low or any(w in out_low for w in a_name.split() if len(w) >= 3)):
                            is_home = False
                        elif out_low == "yes":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) >= 3)):
                                is_home = True
                            elif a_name and (a_name in q_low or any(w in q_low for w in a_name.split() if len(w) >= 3)):
                                is_home = False
                        elif out_low == "no":
                            if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) >= 3)):
                                is_home = False
                            elif a_name and (a_name in q_low or any(w in q_low for w in a_name.split() if len(w) >= 3)):
                                is_home = True

                        if is_home is None:
                            if h_name and any(w in q_low for w in h_name.split() if len(w) >= 3):
                                is_home = (idx == 0)
                            elif a_name and any(w in q_low for w in a_name.split() if len(w) >= 3):
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
                                max_sigma_shift=1.25,
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
                                max_sigma_shift=1.25,
                            )
                        if model_prob is None:
                            continue
                    elif forecast and family in ("moneyline", "three_way") and forecast.get("home_prob") is not None:
                        h_name = (forecast.get("home_name") or "").lower()
                        a_name = (forecast.get("away_name") or "").lower()
                        out_low = outcome_label.lower()
                        q_low = q.lower()

                        # Check for 3-way regulation soccer markets (EPL, MLS, UCL, UEL, LALIGA, BUNDESLIGA, SERIEA)
                        is_soccer = league in ("EPL", "MLS", "UCL", "UEL", "LALIGA", "BUNDESLIGA", "SERIEA")
                        is_draw_question = "draw" in q_low or out_low == "draw"

                        if is_soccer and is_draw_question:
                            draw_p = float(forecast.get("draw_prob") or 0.25)
                            model_prob = draw_p if out_low in ("yes", "draw") else (1.0 - draw_p)
                        elif is_soccer and a_name and (a_name in q_low and h_name not in q_low):
                            away_p = float(forecast.get("away_prob") or max(0.05, 1.0 - float(forecast["home_prob"]) - float(forecast.get("draw_prob") or 0.25)))
                            model_prob = away_p if out_low == "yes" else (1.0 - away_p)
                        elif is_soccer and h_name and (h_name in q_low and a_name not in q_low):
                            home_p = float(forecast.get("home_prob") or 0.45)
                            model_prob = home_p if out_low == "yes" else (1.0 - home_p)
                        else:
                            # Standard 2-way Moneyline (NBA, NFL, NHL, MLB, Tennis, CBB, NCAAF)
                            if h_name and (out_low in h_name or h_name in out_low or any(w in out_low for w in h_name.split() if len(w) >= 3)):
                                is_home = True
                            elif a_name and (out_low in a_name or a_name in out_low or any(w in out_low for w in a_name.split() if len(w) >= 3)):
                                is_home = False
                            elif out_low == "yes":
                                if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) >= 3)):
                                    is_home = True
                                else:
                                    is_home = False
                            elif out_low == "no":
                                if h_name and (h_name in q_low or any(w in q_low for w in h_name.split() if len(w) >= 3)):
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

                    # Pre-screen: Only check depth if contract is liquid, within price bounds and offers positive nominal EV
                    # Under Commandment 3 (Odds >= 1.40 floor), contracts with poly_price > 1.0 / 1.40 (~0.714) are ineligible.
                    if poly_price <= 0.05 or poly_price > (1.0 / 1.40):
                        continue

                    # Disagreement & Information Asymmetry Guard:
                    # An extreme divergence (> 0.20) between domain model and CLOB market price
                    # is overwhelmingly indicative of missing real-time context (injuries, roster scratches,
                    # transfer portal) or model miscalibration, particularly on small/college leagues.
                    disagreement = abs(model_prob - poly_price)
                    max_disagreement = 0.20 if league in ("NCAAF", "CBB", "NCAAB", "CFB", "CWBB", "NCAAW") else 0.25
                    if disagreement > max_disagreement:
                        log.info(
                            f"[MARKET DISAGREEMENT GUARD] {title} {outcome_label}: "
                            f"Model prob ({model_prob:.3f}) diverges by {disagreement:.3f} from Polymarket ({poly_price:.3f}). "
                            f"Exceeds max allowed {max_disagreement:.2f}. Rejected."
                        )
                        data_deficiency_gaps.append({
                            "league": league,
                            "scope": scope,
                            "family": family,
                            "segment_key": f"{league}|{scope}|{family}",
                            "title": title,
                            "selection": outcome_label,
                            "model_prob": model_prob,
                            "poly_price": poly_price,
                            "disagreement": disagreement,
                            "reason": f"Information asymmetry / context deficit ({disagreement:.1%} divergence): model prob {model_prob:.3f} vs market {poly_price:.3f} on {title} {outcome_label}. Ingest real-time player/injury feeds or calibrate rating variance.",
                        })
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

                    # Dynamic Stake Resilience: If full stake_cap causes slippage,
                    # adaptively check if a smaller viable stake down to the constitutional floor ($0.50)
                    # preserves positive EV, ensuring we never abandon an edge due to rigid sizing!
                    effective_stake_cap = stake_cap
                    if not preflight.ok and "INSUFFICIENT_EV_AFTER_SLIPPAGE" in (preflight.reason or ""):
                        for reduced_stake in [round(stake_cap * 0.5, 2), 2.0, 1.0, 0.50]:
                            if reduced_stake >= stake_cap or reduced_stake < 0.50:
                                continue
                            pref_retry = self.executor.preflight_check(
                                token_id=token_id,
                                side="BUY",
                                requested_stake=reduced_stake,
                                model_prob=model_prob,
                                min_ev=self.min_ev_hurdle,
                            )
                            if pref_retry.ok:
                                preflight = pref_retry
                                effective_stake_cap = reduced_stake
                                log.info(
                                    f"[ADAPTIVE DEPTH RESILIENCE] {title} {outcome_label}: "
                                    f"Adjusted stake from ${stake_cap:.2f} to ${reduced_stake:.2f} to secure positive EV (+{pref_retry.ev*100:.1f}%)"
                                )
                                break

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
                        "stake_cap": effective_stake_cap,
                        "scope": scope,
                        "family": family,
                    })

            # Submit highest-EV optimal line/side per (scope, family), with fallback to next-best candidate
            for (grp_scope, grp_family), cands in event_candidates_by_group.items():
                if max_orders_per_cycle is not None and len(orders_placed) >= max_orders_per_cycle:
                    break

                # Rank candidates by Certainty-Adjusted Alpha:
                # We weight EV by the density/liquidity weight (1.0 - abs(price - 0.50))
                # to prevent adverse selection into illiquid, high-variance extreme tail lines.
                # Core liquid lines near consensus (odds 1.50 - 2.20, price 0.45 - 0.67) receive highest priority.
                def candidate_rank(c):
                    ev_val = float(c.get("ev", 0.0))
                    pref = c.get("preflight")
                    p_eff = float(getattr(pref, "eff_price", 0.50) if pref else 0.50)
                    density_weight = max(0.1, 1.0 - abs(p_eff - 0.50))
                    return (ev_val * density_weight, ev_val)

                sorted_cands = sorted(cands, key=candidate_rank, reverse=True)

                # Pacing Guard: Secondary leagues act as complements and cannot overtake NBA/NFL
                if league not in PRIMARY_LEAGUES:
                    max_sec = max(5, int((len(orders_placed) + 1) * 0.35))
                    if secondary_orders_count >= max_sec and primary_orders_count > 0:
                        continue

                for best_cand in sorted_cands:
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
                        require_tier_4=self.require_tier_4,
                    )

                    if order_res.get("ok"):
                        if league in PRIMARY_LEAGUES:
                            primary_orders_count += 1
                        else:
                            secondary_orders_count += 1
                        order_res["token_id"] = best_cand["token_id"]
                        orders_placed.append(order_res)
                        traded_tokens.add(best_cand["token_id"])
                        traded_market_groups.add((title, best_cand["scope"].lower(), best_cand["family"]))
                        log.info(f"[AUTONOMOUS RUNNER] Placed Polymarket Order: {order_res.get('client_order_id')} ({title} {best_cand['selection']} line={best_cand['line']} EV=+{best_cand['ev']*100:.1f}%)")
                        break

        # 2. Trigger Outcome Settlement & Token Auto-Redemption
        settle_res = self.settler.settle_orders()

        # 3. Enqueue identification, taxonomy, data context, and model gaps to learning_jobs for autonomous AI repair
        if self.db_path.exists() and (unmatched_events or unmapped_market_types or data_deficiency_gaps or model_deficit_gaps):
            id_records = []
            for unmatch in unmatched_events:
                if unmatch.get("reason") == "team_names_unmatched":
                    u_title = unmatch.get("event_title", "")
                    u_league = unmatch.get("league", "UNKNOWN")
                    job_id = f"poly_id_{u_league.lower()}_{hashlib.sha256(u_title.encode()).hexdigest()[:12]}"
                    payload = {
                        "kind": "polymarket_identification_repair",
                        "league": u_league,
                        "event_title": u_title,
                        "teams": unmatch.get("teams", []),
                        "reason": "Polymarket team names failed to match calibrated domain tips. Update team aliases or schedule synchronization.",
                        "priority": "HIGH",
                        "timestamp": now_iso,
                    }
                    id_records.append((job_id, u_league, now_iso, now_iso, now_iso, json.dumps(payload)))

            tax_records = []
            for m_type in unmapped_market_types:
                job_id = f"poly_tax_{hashlib.sha256(m_type.encode()).hexdigest()[:12]}"
                payload = {
                    "kind": "polymarket_taxonomy_repair",
                    "sports_market_type": m_type,
                    "reason": f"Unmapped sportsMarketType '{m_type}' discovered on Polymarket. Propose mapping to canonical (scope, family).",
                    "priority": "NORMAL",
                    "timestamp": now_iso,
                }
                tax_records.append((job_id, now_iso, now_iso, now_iso, json.dumps(payload)))

            data_records = []
            for d_gap in data_deficiency_gaps:
                seg_k = d_gap.get("segment_key") or f"{d_gap.get('league')}|full_game|moneyline"
                lg = d_gap.get("league", "UNKNOWN")
                job_id = f"gap_data_{lg.lower()}_{hashlib.sha256((d_gap.get('title', '') + d_gap.get('selection', '')).encode()).hexdigest()[:12]}"
                payload = {
                    "kind": "upstream_sports_sensor_synthesis",
                    "task_id": job_id,
                    "league": lg,
                    "segment_key": seg_k,
                    "event_title": d_gap.get("title"),
                    "selection": d_gap.get("selection"),
                    "divergence": d_gap.get("disagreement"),
                    "reason": d_gap.get("reason"),
                    "priority": "HIGH",
                    "timestamp": now_iso,
                }
                data_records.append((job_id, lg, now_iso, now_iso, now_iso, json.dumps(payload)))

            model_records = []
            for m_gap in model_deficit_gaps:
                seg_k = m_gap.get("segment_key") or f"{m_gap.get('league')}|full_game|totals"
                lg = m_gap.get("league", "UNKNOWN")
                job_id = f"gap_model_{lg.lower()}_{hashlib.sha256((seg_k + str(m_gap.get('line', ''))).encode()).hexdigest()[:12]}"
                payload = {
                    "kind": "urgent_polymarket_model_repair",
                    "league": lg,
                    "segment_key": seg_k,
                    "line": m_gap.get("line"),
                    "reason": m_gap.get("reason"),
                    "priority": "HIGH",
                    "timestamp": now_iso,
                }
                model_records.append((job_id, lg, now_iso, now_iso, now_iso, json.dumps(payload)))

            import time as _t
            from .db_accelerator import get_optimized_connection
            for attempt in range(10):
                try:
                    with closing(get_optimized_connection(self.db_path, timeout=60.0)) as conn:
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS learning_jobs(
                                id TEXT PRIMARY KEY,
                                kind TEXT NOT NULL,
                                league TEXT NOT NULL,
                                status TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL,
                                next_attempt TEXT NOT NULL,
                                attempts INTEGER NOT NULL DEFAULT 0,
                                payload TEXT NOT NULL
                            );
                        """)
                        if id_records:
                            conn.executemany("""
                                INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
                                VALUES (?, 'polymarket_identification_repair', ?, 'queued', ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, payload = excluded.payload;
                            """, id_records)
                        if tax_records:
                            conn.executemany("""
                                INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
                                VALUES (?, 'polymarket_taxonomy_repair', 'ALL', 'queued', ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, payload = excluded.payload;
                            """, tax_records)
                        if data_records:
                            conn.executemany("""
                                INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
                                VALUES (?, 'upstream_sports_sensor_synthesis', ?, 'queued', ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, payload = excluded.payload;
                            """, data_records)
                        if model_records:
                            conn.executemany("""
                                INSERT INTO learning_jobs (id, kind, league, status, created_at, updated_at, next_attempt, payload)
                                VALUES (?, 'urgent_polymarket_model_repair', ?, 'queued', ?, ?, ?, ?)
                                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, payload = excluded.payload;
                            """, model_records)
                        conn.commit()
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() and attempt < 9:
                        _t.sleep(0.15 * (1.5 ** attempt))
                        continue
                    log.warning(f"Could not persist Polymarket identification gaps into learning_jobs: {exc}")
                    break
                except Exception as exc:
                    log.warning(f"Could not persist Polymarket identification gaps into learning_jobs: {exc}")
                    break

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
            "unmatched_events_count": len(unmatched_events),
            "unmatched_events": unmatched_events[:20],
            "unmapped_market_types_count": len(unmapped_market_types),
            "unmapped_market_types": sorted(list(unmapped_market_types)),
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
            "unmatched_events_count": last_state.get("unmatched_events_count", 0),
            "unmapped_market_types_count": last_state.get("unmapped_market_types_count", 0),
            "unmatched_events": last_state.get("unmatched_events", [])[:10],
            "unmapped_market_types": last_state.get("unmapped_market_types", []),
            "last_cycle": last_state,
        }
