"""Polymarket Autonomous R&D Prioritizer.

Scans active Polymarket tournaments and events (NBA, NFL, NHL, etc.), determines
which contracts are actively offered on Polymarket, filters by Tier 3 graduation,
and autonomously enqueues high-priority learning jobs for unproven reachable markets.

Prime Directives:
- Reachable Market Focus: Only enqueue R&D for contracts genuinely offered on Polymarket.
- Zero Hardcoded Bans: As soon as a candidate model passes the Crucible and re-graduates,
  it automatically unlocks in PolymarketAutonomousExecutor.
- Priority Elevation: Tagged as CRITICAL_URGENT_POLYMARKET so Codex and HistoricalTrainer
  prioritize them ahead of generic tasks.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .domain import utcnow
from .polymarket_service import PolymarketService, _detect_market_scope
from .polymarket_executor import PolymarketAutonomousExecutor
from .providers import DATA

log = logging.getLogger(__name__)


DEFAULT_POLYMARKET_LEAGUES: Tuple[str, ...] = (
    "NBA",
    "NFL",
    "MLB",
    "NHL",
    "WNBA",
    "NCAAF",
    "NCAAM",
    "NCAAB",
    "EPL",
    "UCL",
    "MLS",
    "LALIGA",
    "BUNDESLIGA",
    "SERIEA",
    "UEL",
    "ATP",
    "WTA",
)


class PolymarketRDPrioritizer:
    """Prioritizes model synthesis and calibration repairs for liquid Polymarket contracts."""

    _cached_report: Optional[Dict[str, Any]] = None
    _cached_at: float = 0.0
    _cache_ttl_seconds: float = 60.0
    _is_revalidating: bool = False
    _revalidate_lock = threading.Lock()

    @classmethod
    def _load_disk_cache(cls) -> Optional[Dict[str, Any]]:
        cache_file = DATA / "cache" / "polymarket_rd_priorities.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                cls._cached_report = data
                cls._cached_at = cache_file.stat().st_mtime
                return data
            except Exception:
                pass
        return None

    @classmethod
    def _save_disk_cache(cls, report: Dict[str, Any]):
        cache_file = DATA / "cache" / "polymarket_rd_priorities.json"
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            temp = cache_file.with_suffix(".tmp")
            temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
            temp.replace(cache_file)
        except Exception as exc:
            log.debug(f"Could not persist Polymarket RD priorities cache: {exc}")

    def __init__(
        self,
        db_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        executor: Optional[PolymarketAutonomousExecutor] = None,
    ):
        self.db_path = Path(db_path) if db_path else (DATA / "engine.sqlite3")
        self.cache_dir = Path(cache_dir) if cache_dir else (DATA / "cache" / "polymarket")
        self.service = PolymarketService(cache_dir=self.cache_dir)
        self.executor = executor or PolymarketAutonomousExecutor(db_path=self.db_path, dry_run=True)

    def scan_and_prioritize_gaps(
        self,
        leagues: Optional[Tuple[str, ...]] = None,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> Dict[str, Any]:
        """Scans reachable contracts, identifies unproven gaps, and enqueues learning jobs."""
        now_ts = time.time()
        if PolymarketRDPrioritizer._cached_report is None:
            PolymarketRDPrioritizer._load_disk_cache()

        if not force_refresh and leagues is None and PolymarketRDPrioritizer._cached_report is not None:
            age = now_ts - PolymarketRDPrioritizer._cached_at
            if age < PolymarketRDPrioritizer._cache_ttl_seconds:
                return dict(PolymarketRDPrioritizer._cached_report)
            elif allow_stale:
                with PolymarketRDPrioritizer._revalidate_lock:
                    if not PolymarketRDPrioritizer._is_revalidating:
                        PolymarketRDPrioritizer._is_revalidating = True
                        t = threading.Thread(
                            target=self._background_revalidate,
                            daemon=True,
                            name="poly-rd-revalidate",
                        )
                        t.start()
                return dict(PolymarketRDPrioritizer._cached_report)

        return self._execute_gap_scan(leagues=leagues)

    def _background_revalidate(self):
        try:
            self._execute_gap_scan(leagues=None)
        except Exception as exc:
            log.debug(f"Background Polymarket RD revalidation error: {exc}")
        finally:
            PolymarketRDPrioritizer._is_revalidating = False

    def _execute_gap_scan(self, leagues: Optional[Tuple[str, ...]] = None) -> Dict[str, Any]:
        now_ts = time.time()
        target_leagues = leagues if leagues is not None else DEFAULT_POLYMARKET_LEAGUES
        discovered_segments: Set[str] = set()
        segment_details: Dict[str, Dict[str, Any]] = {}

        # 1. Seed standard full-game contract types across all target leagues
        from .contracts import is_preferred_alpha_league, is_dual_reachable_contract, get_sport_priority
        for lg in target_leagues:
            if not is_preferred_alpha_league(lg):
                continue
            for fam in ("moneyline", "spreads", "totals"):
                if not is_dual_reachable_contract(lg, "full_game", fam):
                    continue
                seg_key = f"{lg}|full_game|{fam}"
                discovered_segments.add(seg_key)
                segment_details[seg_key] = {
                    "league": lg,
                    "scope": "full_game",
                    "market_family": fam,
                    "sample_event": f"Active {lg} CLOB Orderbook",
                    "sample_token": None,
                }

        # 2. Discover live open contracts across target leagues (Parallel Concurrent Fetch)
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_league(lg: str):
            try:
                return lg, self.service.get_open_events(league=lg, horizon_hours=72.0)
            except Exception as exc:
                log.warning(f"Error scanning Polymarket events for league {lg}: {exc}")
                return lg, []

        with ThreadPoolExecutor(max_workers=min(8, len(target_leagues))) as pool:
            league_results = list(pool.map(_fetch_league, target_leagues))

        from .polymarket_runner import POLYMARKET_STRUCTURED_TYPE_MAP

        for league, events in league_results:
            norm_league = "NCAAM" if league in ("CBB", "NCAAB") else ("NCAAF" if league == "CFB" else league)
            for ev in events:
                markets = ev.get("markets", [])
                for m in markets:
                    m_type = (m.get("sportsMarketType") or m.get("type") or "").strip().lower()
                    q = m.get("question") or ""

                    # Canonical resolution via POLYMARKET_STRUCTURED_TYPE_MAP
                    if m_type in POLYMARKET_STRUCTURED_TYPE_MAP:
                        scope, m_fam = POLYMARKET_STRUCTURED_TYPE_MAP[m_type]
                    else:
                        # Skip player props, team totals, and excluded novelties from R&D queue
                        is_excluded = any(prop in m_type for prop in (
                            "touchdown", "reception", "pass", "rush", "assist", "rebound", "point", "yard",
                            "strikeout", "tackle", "sack", "interception", "margin", "race", "corner",
                            "goal", "shot", "save", "team_total", "team_totals", "overtime", "odd_even",
                            "pick_six", "fourth_down", "field_goal", "first_down", "exact_score",
                            "first_to_score", "score_first", "two_point", "completed_match",
                            "three", "threes", "steal", "block", "turnover", "foul", "home_run", "hit", "rbi"
                        ))
                        if is_excluded:
                            continue
                        scope = _detect_market_scope(m_type, q)
                        if "spread" in m_type or "handicap" in m_type:
                            m_fam = "spreads"
                        elif "total" in m_type or "over" in m_type:
                            m_fam = "totals"
                        else:
                            m_fam = "moneyline"

                    if not is_preferred_alpha_league(norm_league):
                        continue
                    if not is_dual_reachable_contract(norm_league, scope, m_fam):
                        continue

                    seg_key = f"{norm_league}|{scope}|{m_fam}"
                    discovered_segments.add(seg_key)

                    tokens = m.get("clobTokenIds", [])
                    if isinstance(tokens, str):
                        try:
                            tokens = json.loads(tokens)
                        except Exception:
                            tokens = [tokens]

                    segment_details[seg_key] = {
                        "league": norm_league,
                        "scope": scope,
                        "market_family": m_fam,
                        "sample_event": ev.get("title", f"Active {league} Match"),
                        "sample_token": tokens[0] if tokens else None,
                    }

        # 3. Load authoritative UnifiedMarketUniverse & SegmentScoreboard metadata
        roster = {}
        try:
            from .unified_market_universe import UnifiedMarketUniverse
            umu = UnifiedMarketUniverse(data_dir=DATA)
            roster = umu.build_universe_roster()
        except Exception as exc:
            log.warning(f"Could not load UnifiedMarketUniverse: {exc}")

        scoreboard_segs = {}
        try:
            from .segment_scoreboard import compute_segment_scoreboard
            if self.db_path.exists():
                with sqlite3.connect(self.db_path, timeout=30.0) as conn:
                    conn.execute("PRAGMA busy_timeout = 30000;")
                    sb = compute_segment_scoreboard(conn)
                    for s in (sb.get("alpha_segments", []) + sb.get("exploration_segments", []) + sb.get("underperforming_segments", [])):
                        scoreboard_segs[s["segment"]] = s
        except Exception as exc:
            log.warning(f"Could not load SegmentScoreboard: {exc}")

        # 4. Evaluate each discovered segment for Tier 4 Proven Alpha vs Tier 3 Probation vs Tier 2 Gaps
        proven_markets: List[Dict[str, Any]] = []
        unproven_gaps: List[Dict[str, Any]] = []

        for seg_key in sorted(discovered_segments):
            details = segment_details[seg_key]
            league = details["league"]
            scope = details["scope"]
            m_fam = details["market_family"]

            # Match scoreboard stats (using aliases if needed)
            sb_match = scoreboard_segs.get(seg_key)
            if not sb_match:
                clean_m = m_fam.split(":")[-1]
                for k, v in scoreboard_segs.items():
                    if v.get("league") == league and v.get("scope") == scope:
                        f_name = v.get("family", "")
                        clean_f = f_name.split(":")[-1]
                        if clean_m in clean_f or clean_f in clean_m or (clean_m in ("moneyline", "winner", "h2h") and clean_f in ("three_way", "winner", "h2h", "moneyline")):
                            sb_match = v
                            break

            live_games = int(sb_match.get("games", 0)) if sb_match else 0
            live_pnl = float(sb_match.get("pnl", 0.0)) if sb_match else 0.0
            live_roi_pct = float(sb_match.get("roi_pct", 0.0)) if sb_match else 0.0
            rolling_games = int(sb_match.get("rolling_games", live_games)) if sb_match else 0
            rolling_pnl = float(sb_match.get("rolling_pnl", live_pnl)) if sb_match else 0.0
            rolling_roi_pct = float(sb_match.get("rolling_roi_pct", live_roi_pct)) if sb_match else 0.0
            rolling_units = float(sb_match.get("rolling_units", sb_match.get("units_pnl", 0.0))) if sb_match else 0.0
            sub_tier = str(sb_match.get("sub_tier", "")) if sb_match else ""

            # Tier 4 graduation conditions on Stake:
            # 1. Sample size: Rolling N >= 10 (or games >= 10)
            # 2. Rolling ROI >= +2.0%
            # 3. Rolling Units >= +0.4U (or rolling PnL >= $2.00)
            games_needed = max(0, 10 - rolling_games)
            games_met = (rolling_games >= 10 or live_games >= 10)
            roi_met = (rolling_roi_pct >= 2.0 or (rolling_pnl > 0.0 and rolling_roi_pct > 0.0))
            units_met = (rolling_units >= 0.4)
            conditions_met_count = sum(1 for c in (games_met, roi_met, units_met) if c)

            # Match model archetype from universe
            u_entry = roster.get(seg_key)
            model_id = u_entry.active_model_id if u_entry and u_entry.active_model_id else f"{league.lower()}_{scope}_{m_fam}_kernel"
            model_archetype = u_entry.model_archetype if u_entry and u_entry.model_archetype else "CalibratedDomainKernel"

            is_eligible, reason, stake_cap = self.executor.is_market_eligible(
                league=league,
                scope=scope,
                market_family=m_fam,
                require_tier_4=True,
            )

            if is_eligible:
                tier_status = "TIER_4_PROVEN"
                proximity_score = 1000.0 + rolling_roi_pct + (rolling_units * 2.0)
            elif "DEMOTED" in reason:
                tier_status = "TIER_1_RESEARCH"
                proximity_score = 50.0
            elif "UNDERPERFORMING" in reason or (rolling_games >= 4 and (rolling_pnl < 0.0 or rolling_roi_pct < 0.0)) or (live_pnl < 0.0 and live_games > 0):
                tier_status = "TIER_1_UNDERPERFORMING"
                proximity_score = -100.0 + max(-500.0, rolling_roi_pct)
            elif (rolling_pnl > 0.0 or live_pnl > 0.0) and (rolling_roi_pct >= 2.0 or live_roi_pct >= 2.0) and rolling_games > 0:
                if rolling_games >= 5 or conditions_met_count >= 2:
                    tier_status = "APPROACHING_TIER_4"
                    proximity_score = 500.0 + (rolling_games * 25.0) + (conditions_met_count * 50.0) + min(50.0, max(0.0, rolling_roi_pct))
                else:
                    tier_status = "TIER_3_PROBATION"
                    proximity_score = 200.0 + (rolling_games * 15.0) + min(30.0, max(0.0, rolling_roi_pct))
            elif (live_pnl > 0.0 or rolling_pnl > 0.0) and live_games > 0:
                tier_status = "TIER_3_PROBATION"
                proximity_score = 200.0 + (rolling_games * 15.0) + min(30.0, max(0.0, rolling_roi_pct))
            else:
                tier_status = "TIER_2_INCUBATING"
                proximity_score = 50.0

            seg_info = {
                "segment_key": seg_key,
                "league": league,
                "scope": scope,
                "market_family": m_fam,
                "model_id": model_id,
                "model_archetype": model_archetype,
                "live_games": live_games,
                "live_pnl": round(live_pnl, 2),
                "live_roi_pct": round(live_roi_pct, 1),
                "rolling_games": rolling_games,
                "rolling_pnl": round(rolling_pnl, 2),
                "rolling_roi_pct": round(rolling_roi_pct, 1),
                "rolling_units": round(rolling_units, 2),
                "sub_tier": sub_tier,
                "games_needed": games_needed,
                "games_met": games_met,
                "roi_met": roi_met,
                "units_met": units_met,
                "conditions_met_count": conditions_met_count,
                "proximity_score": round(proximity_score, 1),
                "is_profitable": (live_pnl > 0.0) if live_games > 0 else (False if live_games > 0 and live_pnl < 0.0 else None),
                "tier_status": tier_status,
                "sample_event": details["sample_event"],
                "sample_token": details["sample_token"],
                "reason": reason,
                "stake_cap": stake_cap,
            }

            if is_eligible:
                proven_markets.append(seg_info)
            else:
                unproven_gaps.append(seg_info)

        # 3. Enqueue learning jobs for unproven reachable gaps
        enqueued_count = 0
        now_iso = utcnow().isoformat()

        if self.db_path.exists() and unproven_gaps:
            records = []
            for gap in unproven_gaps:
                seg_key = gap["segment_key"]
                league = gap["league"]
                job_id = f"poly_gap_{seg_key.lower().replace('|', '_')}"
                payload = {
                    "kind": "urgent_polymarket_model_repair",
                    "segment_key": seg_key,
                    "league": league,
                    "scope": gap["scope"],
                    "market_family": gap["market_family"],
                    "sample_event": gap["sample_event"],
                    "sample_token": gap["sample_token"],
                    "priority_tag": "CRITICAL_URGENT_POLYMARKET",
                    "objective": "Pass Crucible out-of-sample backtest (ROI >= +3.5%, Brier skill > 0) to graduate market into live Polymarket execution.",
                    "timestamp": now_iso,
                }
                records.append((
                    job_id,
                    league,
                    now_iso,
                    now_iso,
                    now_iso,
                    json.dumps(payload),
                ))

            import time as _t
            for attempt in range(3):
                try:
                    with sqlite3.connect(self.db_path, timeout=45.0) as conn:
                        conn.execute("PRAGMA busy_timeout = 45000;")
                        conn.executemany(
                            """
                            INSERT INTO learning_jobs (
                                id, kind, league, status, created_at, updated_at, next_attempt, payload
                            ) VALUES (?, 'urgent_polymarket_model_repair', ?, 'queued', ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                updated_at=excluded.updated_at,
                                payload=excluded.payload;
                            """,
                            records,
                        )
                    enqueued_count = len(records)
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc).lower() and attempt < 2:
                        _t.sleep(0.5 * (attempt + 1))
                        continue
                    log.error(f"Failed to enqueue Polymarket R&D repair jobs into SQLite: {exc}")
                    break
                except Exception as exc:
                    log.error(f"Failed to enqueue Polymarket R&D repair jobs into SQLite: {exc}")
                    break

        log.info(
            f"Polymarket R&D Gap Analysis complete: {len(discovered_segments)} reachable segments, "
            f"{len(proven_markets)} proven active, {len(unproven_gaps)} unproven gaps ({enqueued_count} enqueued)."
        )

        def _poly_strategic_key(x):
            prio = get_sport_priority(x.get("league", ""))
            score = x.get("proximity_score", 0.0)
            return (prio, -score)

        proven_markets.sort(key=_poly_strategic_key)
        unproven_gaps.sort(key=_poly_strategic_key)
        all_reachable_markets = sorted(
            proven_markets + unproven_gaps,
            key=_poly_strategic_key,
        )

        result = {
            "timestamp": now_iso,
            "reachable_segments_count": len(discovered_segments),
            "proven_active_count": len(proven_markets),
            "unproven_gaps_count": len(unproven_gaps),
            "proven_markets": proven_markets,
            "unproven_gaps": unproven_gaps,
            "all_reachable_markets": all_reachable_markets,
            "enqueued_jobs_count": enqueued_count,
        }
        if leagues is None:
            PolymarketRDPrioritizer._cached_report = result
            PolymarketRDPrioritizer._cached_at = now_ts
            PolymarketRDPrioritizer._save_disk_cache(result)
        return result
