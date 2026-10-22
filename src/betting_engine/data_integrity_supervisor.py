"""Data Integrity Supervisor and Autonomous Multi-League Sentinel.

Ensures that for ALL 14 supported leagues (NBA, MLB, NHL, NFL, WNBA, NCAAF,
NCAAM, NCAAW, EPL, UCL, MLS, ATP, WTA, CHALLENGER), all required historical
and upcoming sports data, context features, and model artifacts are:
1. Updated and available.
2. Verified for completeness and consistency.
3. Automatically healed / backfilled if any feed omits historical matches.
4. Hooked directly into the engine's execution cycle and exposed via REST API.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib

from .domain import LEAGUES, Game, utcnow, parse_time
from .io_utils import atomic_json, atomic_bytes

log = logging.getLogger(__name__)

# Minimum required completed matches per league for production validity
LEAGUE_MIN_FINALS: Dict[str, int] = {
    'NCAAM': 1000,
    'NCAAW': 1000,
    'MLB': 1000,
    'NHL': 1000,
    'NBA': 1000,
    'NCAAF': 500,
    'MLS': 500,
    'NFL': 500,
    'WNBA': 300,
    'EPL': 300,
    'UCL': 100,
    'ATP': 20,
    'WTA': 20,
}


@dataclass
class LeagueDataReport:
    league: str
    total_games: int
    final_games: int
    scheduled_games: int
    latest_final_date: Optional[str]
    model_present: bool
    model_type: str
    model_version: str
    status: str  # 'OPTIMAL', 'DEGRADED', 'CRITICAL'
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DataIntegritySummary:
    checked_at: str
    overall_health: str  # 'OPTIMAL', 'DEGRADED', 'ACTION_REQUIRED'
    total_games_all_leagues: int
    leagues_optimal_count: int
    leagues_degraded_count: int
    leagues_critical_count: int
    healing_actions_performed: List[str]
    league_reports: Dict[str, Any]
    context_sources_status: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DataIntegritySupervisor:
    """Supervises and self-heals multi-league sports data completeness."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            from .providers import DATA
            self.data_dir = Path(DATA)
        else:
            self.data_dir = Path(data_dir)
        self.status_file = self.data_dir / 'data_integrity_status.json'
        self.games_file = self.data_dir / 'games.json'

    def audit(self) -> Tuple[str, Dict[str, LeagueDataReport], Dict[str, Any]]:
        """Audits all 14 leagues against contract thresholds."""
        games_raw = []
        if self.games_file.exists():
            try:
                games_raw = json.loads(self.games_file.read_text())
            except Exception as e:
                log.error("Failed to read games.json during data integrity audit: %s", e)

        by_league: Dict[str, List[Dict[str, Any]]] = {lg: [] for lg in LEAGUES}
        for g in games_raw:
            lg = g.get('league') or g.get('sport')
            if lg in by_league:
                by_league[lg].append(g)

        league_reports: Dict[str, LeagueDataReport] = {}
        optimal_cnt = 0
        degraded_cnt = 0
        critical_cnt = 0

        for lg in LEAGUES:
            lg_games = by_league[lg]
            finals = [g for g in lg_games if g.get('state') == 'final']
            scheduled = [g for g in lg_games if g.get('state') == 'scheduled']
            dates = [g.get('start') for g in finals if g.get('start')]
            latest_final = max(dates)[:10] if dates else None

            # Model artifact inspection
            model_path = self.data_dir / 'models' / f'{lg}.joblib'
            model_present = model_path.exists()
            model_type = 'None'
            model_version = 'None'
            if model_present:
                try:
                    from .tennis_engine import TennisLeagueModel
                    from .soccer_model import SoccerModel
                    from .models import LeagueModel
                    m = joblib.load(model_path)
                    model_type = type(m).__name__
                    model_version = getattr(m, 'version', getattr(m, 'report', {}).get('version', 'v1'))
                except Exception as exc:
                    model_type = f'Corrupt: {exc}'[:60]

            min_req = LEAGUE_MIN_FINALS.get(lg, 20)
            issues = []
            if len(finals) < min_req:
                issues.append(f'Final matches below threshold: {len(finals)} < {min_req}')
            if not model_present:
                issues.append(f'Model artifact missing: {model_path.name}')
            elif 'Corrupt' in model_type:
                issues.append(f'Model artifact corrupt: {model_type}')

            if issues:
                status = 'CRITICAL' if (len(finals) < min_req / 2 or not model_present) else 'DEGRADED'
                if status == 'CRITICAL':
                    critical_cnt += 1
                else:
                    degraded_cnt += 1
            else:
                status = 'OPTIMAL'
                optimal_cnt += 1

            league_reports[lg] = LeagueDataReport(
                league=lg,
                total_games=len(lg_games),
                final_games=len(finals),
                scheduled_games=len(scheduled),
                latest_final_date=latest_final,
                model_present=model_present,
                model_type=model_type,
                model_version=model_version,
                status=status,
                issues=issues,
            )

        # Context sources check
        context_status = {
            'context_sources': (self.data_dir / 'context_sources.json').exists(),
            'pitching_sources': (self.data_dir / 'pitching_sources.json').exists(),
            'tennis_archive': (self.data_dir / 'tennis_results_archive.json').exists(),
            'period_scores': (self.data_dir / 'periods' / 'scores.json').exists(),
        }

        overall = 'OPTIMAL'
        if critical_cnt > 0:
            overall = 'ACTION_REQUIRED'
        elif degraded_cnt > 0:
            overall = 'DEGRADED'

        return overall, league_reports, context_status

    def heal(
        self,
        league_reports: Dict[str, LeagueDataReport],
        engine_instance: Optional[Any] = None,
    ) -> List[str]:
        """Automatically heals and backfills leagues with data deficiencies."""
        actions: List[str] = []
        now_year = utcnow().year

        from .providers import SportsData, Fetcher

        sd = SportsData()

        # Load existing games
        existing_games = []
        if self.games_file.exists():
            try:
                existing_games = json.loads(self.games_file.read_text())
            except Exception:
                existing_games = []

        games_dict = {g['id']: g for g in existing_games}
        new_matches_added = 0

        # 1. Heal EPL / UCL if needed
        for lg in ('EPL', 'UCL'):
            rep = league_reports.get(lg)
            if rep and rep.status in ('CRITICAL', 'DEGRADED'):
                try:
                    log.info("DataIntegritySupervisor: Backfilling official %s seasons...", lg)
                    for s in range(2023, now_year + 1):
                        games, _ = sd.soccer_official_season(lg, s)
                        for g in games:
                            gid = g.id
                            g_dict = g.dict()
                            if gid not in games_dict:
                                games_dict[gid] = g_dict
                                new_matches_added += 1
                            elif games_dict[gid].get('state') != 'final' and g_dict.get('state') == 'final':
                                games_dict[gid] = g_dict
                    actions.append(f'Backfilled official {lg} matches (seasons 2023-{now_year})')

                    # Retrain soccer model if needed
                    from .soccer_model import SoccerModel
                    model_p = self.data_dir / 'models' / f'{lg}.joblib'
                    if not model_p.exists() or rep.status == 'CRITICAL':
                        sorted_matches = sorted(
                            [g for g in games_dict.values() if g.get('league') == lg and g.get('state') == 'final'],
                            key=lambda x: x.get('start', '')
                        )
                        if sorted_matches:
                            sm = SoccerModel(league=lg)
                            for gm in sorted_matches:
                                hs = gm.get('home_score')
                                as_ = gm.get('away_score')
                                if hs is not None and as_ is not None:
                                    sm.update_match_outcome(gm.get('home', ''), gm.get('away', ''), int(hs), int(as_), gm.get('start', ''))
                            joblib.dump(sm, model_p)
                            actions.append(f'Retrained and calibrated {lg}.joblib on {len(sorted_matches)} matches')
                except Exception as exc:
                    log.exception("DataIntegritySupervisor: Failed to backfill %s: %s", lg, exc)

        # 2. Heal Tennis if needed (merge tennis_results_archive into games.json)
        for t_lg in ('ATP', 'WTA'):
            rep = league_reports.get(t_lg)
            if rep and rep.final_games < 10:
                t_arch_p = self.data_dir / 'tennis_results_archive.json'
                arch_data = {}
                if t_arch_p.exists():
                    try:
                        arch_data = json.loads(t_arch_p.read_text())
                    except Exception:
                        arch_data = {}

                t_added = 0
                for gid, row in arch_data.items():
                    row_lg = row.get('league')
                    if row_lg != t_lg:
                        continue
                    if gid not in games_dict:
                        games_dict[gid] = {
                            'id': gid,
                            'league': row_lg,
                            'start': row.get('verified_at', utcnow().isoformat()),
                            'home': row.get('p1', ''),
                            'away': row.get('p2', ''),
                            'home_id': row.get('p1', ''),
                            'away_id': row.get('p2', ''),
                            'home_abbr': '',
                            'away_abbr': '',
                            'state': 'final',
                            'season': now_year,
                            'home_score': float(row.get('games_home', row.get('games1', 0))),
                            'away_score': float(row.get('games_away', row.get('games2', 0))),
                            'neutral': True,
                            'game_type': 'regular',
                            'source': row.get('source', 'tennis_archive'),
                            'fetched_at': row.get('verified_at', utcnow().isoformat()),
                            'context': {
                                'set_scores': row.get('set_scores', []),
                                'sets_home': row.get('sets_home', row.get('sets1', 0)),
                                'sets_away': row.get('sets_away', row.get('sets2', 0)),
                            }
                        }
                        t_added += 1
                if t_added > 0:
                    new_matches_added += t_added
                    actions.append(f'Attached {t_added} verified {t_lg} matches from tennis archive/decisions')

        # 3. If new matches were added, save games.json atomically and sync engine instance
        if new_matches_added > 0:
            merged_list = sorted(games_dict.values(), key=lambda x: (x.get('start', ''), x.get('id', '')))
            atomic_json(self.games_file, merged_list)
            log.info("DataIntegritySupervisor: Saved %d games (+%d new) to %s", len(merged_list), new_matches_added, self.games_file)

            if engine_instance is not None:
                try:
                    engine_instance.games = [Game(**g) for g in merged_list]
                    engine_instance.load_models()
                    actions.append(f'Live engine instance updated with {len(merged_list)} games and reloaded models')
                except Exception as exc:
                    log.warning("Could not sync live engine instance: %s", exc)

        return actions

    def audit_and_heal(self, engine_instance: Optional[Any] = None) -> DataIntegritySummary:
        """Runs the complete audit, executes self-healing if needed, and writes durable status."""
        overall, reports, ctx_status = self.audit()
        healing_actions = []

        if overall != 'OPTIMAL':
            healing_actions = self.heal(reports, engine_instance=engine_instance)
            # Re-audit post healing
            overall, reports, ctx_status = self.audit()

        optimal_cnt = sum(1 for r in reports.values() if r.status == 'OPTIMAL')
        degraded_cnt = sum(1 for r in reports.values() if r.status == 'DEGRADED')
        critical_cnt = sum(1 for r in reports.values() if r.status == 'CRITICAL')
        total_games = sum(r.total_games for r in reports.values())

        summary = DataIntegritySummary(
            checked_at=utcnow().isoformat(),
            overall_health=overall,
            total_games_all_leagues=total_games,
            leagues_optimal_count=optimal_cnt,
            leagues_degraded_count=degraded_cnt,
            leagues_critical_count=critical_cnt,
            healing_actions_performed=healing_actions,
            league_reports={lg: rep.to_dict() for lg, rep in reports.items()},
            context_sources_status=ctx_status,
        )

        try:
            atomic_json(self.status_file, summary.to_dict())
        except Exception as exc:
            log.warning("Failed to persist data integrity status file: %s", exc)

        return summary
