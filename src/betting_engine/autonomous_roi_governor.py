"""Autonomous Self-Supervising ROI & Alpha Governor.

Supreme oversight engine designed to enforce the user's prime objective:
Consistent high betting volume (minimum 100+ bets/Budapest day) with a
minimum weekly +5% to +10% portfolio ROI.

Operates across 3 dynamic control vectors:
1. Alpha Acceleration: Heavily scales capital ($2.50–$8.00) into demonstrably
   winning segments (MLB spreads/totals, WNBA ML, NFL spreads, MLS, ATP ML).
2. Capital Defense Clamp: Instantly clamps underperforming segments
   (rolling ROI < -3% or negative PnL over 4+ games) to $0.50 tourniquet and requires +3.5% min EV.
3. Toxic Micro-Market Quarantine: Completely suppresses negative outlier
   markets (Tier 1/2 R&D $0.00 stake) to prevent catastrophic profit drain.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json
import logging
from pathlib import Path
import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

from .domain import utcnow
from .io_utils import atomic_json
from .providers import DATA
from .unified_market_universe import UnifiedMarketUniverse

log = logging.getLogger(__name__)

STATUS_PATH = DATA / 'roi_governor_status.json'


@dataclass
class SegmentMetric:
    segment: str
    league: str
    scope: str
    family: str
    bets_count: int
    wins: int
    losses: int
    pushes: int
    win_rate_pct: float
    total_stake: float
    total_pnl: float
    roi_pct: float
    tier: str
    stake_cap: float
    min_ev_hurdle: float
    action: str


class AutonomousROIGovernor:
    """Supreme self-supervising quantitative controller for weekly ROI maximization."""

    def __init__(self, data_dir: Path = DATA, db_path: Optional[Path] = None):
        self.data_dir = data_dir
        self.db_path = db_path or (data_dir / 'engine.sqlite3')

    def analyze_weekly_performance(self, days: int = 7) -> Dict[str, Any]:
        """Analyzes all settled bets in the trailing rolling window."""
        if not self.db_path.exists():
            return {'status': 'error', 'reason': 'database_missing'}

        conn = sqlite3.connect(self.db_path, timeout=45.0)
        conn.execute("PRAGMA busy_timeout=45000")
        c = conn.cursor()

        cutoff_str = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # 1. Total Portfolio Metrics
        c.execute("""
            SELECT 
                COUNT(*),
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END),
                SUM(stake),
                SUM(pnl)
            FROM bets 
            WHERE status IN ('settled', 'won', 'lost', 'push')
              AND created_at >= ?
        """, (cutoff_str,))
        tot_bets, wins, losses, pushes, tot_stake, tot_pnl = c.fetchone()
        tot_bets = tot_bets or 0
        wins = wins or 0
        losses = losses or 0
        pushes = pushes or 0
        tot_stake = tot_stake or 0.0
        tot_pnl = tot_pnl or 0.0
        portfolio_roi = (tot_pnl / tot_stake * 100) if tot_stake > 0 else 0.0
        portfolio_wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

        # 2. Segment-level breakdown
        c.execute("""
            SELECT 
                json_extract(payload, '$.league') as league,
                json_extract(payload, '$.model_scope') as scope,
                json_extract(payload, '$.market') as market,
                COUNT(*),
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl = 0 THEN 1 ELSE 0 END),
                SUM(stake),
                SUM(pnl)
            FROM bets 
            WHERE status IN ('settled', 'won', 'lost', 'push')
              AND created_at >= ?
            GROUP BY league, scope, market
            ORDER BY SUM(pnl) DESC
        """, (cutoff_str,))
        rows = c.fetchall()

        # Collect recent individual settled wagers per segment to evaluate rolling health
        c.execute("""
            SELECT 
                json_extract(payload, '$.league') as league,
                json_extract(payload, '$.model_scope') as scope,
                json_extract(payload, '$.market') as market,
                pnl,
                stake
            FROM bets 
            WHERE status IN ('settled', 'won', 'lost', 'push')
              AND created_at >= ?
            ORDER BY created_at DESC
        """, (cutoff_str,))
        all_recent_bets = c.fetchall()
        conn.close()

        recent_by_seg = {}
        for r_lg, r_sc, r_mkt, r_pnl, r_stk in all_recent_bets:
            sk = f"{r_lg or 'UNKNOWN'}|{r_sc or 'full_game'}|{r_mkt or 'unknown'}"
            recent_by_seg.setdefault(sk, []).append((float(r_pnl or 0.0), float(r_stk or 0.0)))

        segments: List[SegmentMetric] = []
        super_alpha = []
        solid_alpha = []
        capital_defense = []
        toxic_quarantine = []

        grad_markets = set()
        demoted_markets_set = set()
        sb_underperforming_set = set()
        try:
            from .unified_market_universe import UnifiedMarketUniverse
            roster = UnifiedMarketUniverse(self.data_dir).build_universe_roster()
            grad_markets = {k for k, p in roster.items() if p.tier >= 3}
            demoted_markets_set = {k for k, p in roster.items() if p.tier in (1, 2)}
        except Exception:
            try:
                from .shadow_market_incubator import load_graduated_markets, load_demoted_markets
                gm = load_graduated_markets(self.data_dir)
                if gm:
                    grad_markets = set(gm.keys())
                dm = load_demoted_markets(self.data_dir)
                if dm:
                    demoted_markets_set = set(dm.keys())
            except Exception:
                pass

        try:
            from .segment_scoreboard import compute_segment_scoreboard
            with closing(sqlite3.connect(self.db_path, timeout=10.0)) as sb_conn:
                sb_obj = compute_segment_scoreboard(sb_conn)
                sb_underperforming_set = {s["segment"] for s in sb_obj.get("underperforming_segments", [])}
        except Exception as e_sb:
            log.debug(f"Could not load segment scoreboard in governor: {e_sb}")

        for r in rows:
            league = r[0] or 'UNKNOWN'
            scope = r[1] or 'full_game'
            market = r[2] or 'unknown'
            seg_key = f"{league}|{scope}|{market}"

            cnt = r[3] or 0
            w = r[4] or 0
            l = r[5] or 0
            p = r[6] or 0
            st = r[7] or 0.0
            pnl = r[8] or 0.0
            roi = (pnl / st * 100) if st > 0 else 0.0
            wr = (w / (w + l) * 100) if (w + l) > 0 else 0.0
            avg_st = (st / cnt) if cnt > 0 else 1.0
            units_pnl = pnl / max(0.50, avg_st)

            # Dynamic Tier Classification
            tier = 'NEUTRAL'
            stake_cap = 2.0
            min_ev = 0.02
            action = 'Standard unit sizing'

            canon_k = f"{league}|{scope}|{market}"
            base_mkt = market.split(':')[-1] if ':' in market else market
            base_canon_k = f"{league}|{scope}|{base_mkt}"

            is_grad_seg = bool(
                seg_key in grad_markets
                or canon_k in grad_markets
                or base_canon_k in grad_markets
            )

            is_demoted_seg = bool(
                seg_key in demoted_markets_set
                or canon_k in demoted_markets_set
                or base_canon_k in demoted_markets_set
            ) and not is_grad_seg

            is_sb_underperforming = bool(
                seg_key in sb_underperforming_set
                or canon_k in sb_underperforming_set
                or base_canon_k in sb_underperforming_set
            )

            # Evaluate recent rolling cohort (last 10 wagers) for rolling health
            rec_bets = recent_by_seg.get(seg_key, [])[:10]
            rec_pnl = sum(b[0] for b in rec_bets) if rec_bets else 0.0
            rec_stk = sum(b[1] for b in rec_bets) if rec_bets else 0.0
            rec_roi = (rec_pnl / rec_stk * 100) if rec_stk > 0 else 0.0
            rec_avg_stk = (rec_stk / len(rec_bets)) if rec_bets else 1.0
            rec_units = rec_pnl / max(0.50, rec_avg_stk)
            is_rolling_degraded = bool(len(rec_bets) >= 4 and (rec_roi <= -3.0 or rec_units <= -2.0 or (rec_pnl < 0.0 and rec_roi < 0.0)))

            # 1. Demoted Markets in Tier 1 R&D / Tier 2 Shadow Incubator Quarantine ($0.00 stake)
            if is_demoted_seg:
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.10
                action = 'Quarantined: demoted to Tier 1 R&D ($0.00) for historical training'
                toxic_quarantine.append(seg_key)

            # 2. Quarantined lower-tier circuits (Challenger/ITF) receive zero stake allocation UNLESS graduated via Crucible
            elif ((league in ('CHALLENGER', 'ITF') or 'challenger' in league.lower() or 'challenger' in seg_key.lower())
                and not is_grad_seg):
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.10
                action = 'Quarantined: lower-tier circuit zero stake allocation until formal promotion'
                toxic_quarantine.append(seg_key)

            # 3. Pure noise 50/50 markets (odd/even): zero fundamental edge, negative bookmaker vig
            elif 'odd_even' in str(market).lower():
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.10
                action = 'Quarantined: odd/even coin-flip market suppressed'
                toxic_quarantine.append(seg_key)

            # 4. Rolling Breakdown / Degradation Invariant (Recent drawdown overrides ancient history)
            elif is_rolling_degraded or is_sb_underperforming:
                tier = 'CAPITAL_DEFENSE'
                stake_cap = 0.50
                min_ev = 0.035
                action = f'Capital defense tourniquet: rolling drawdown ({rec_roi:.1f}% ROI, {rec_units:.1f}U over {len(rec_bets)} bets)'
                capital_defense.append(seg_key)

            # 5. Dynamic Loss Circuit Breaker (Scale-Invariant in Risk Units - Commandment 3 & 6):
            # Zero hardcoded dollar limits. A normal 2-3 bet wobble is protected by Phase 1 Capital Defense ($0.50 clamp).
            # Placed in TOXIC_QUARANTINE ($0.00 stake) ONLY upon statistically significant sustained failure:
            # - N >= 8 games with rolling units <= -4.0U and negative ROI
            # - OR N >= 12 games with total units <= -5.0U and ROI <= -10%
            elif (
                (len(rec_bets) >= 8 and rec_units <= -4.0 and rec_roi <= -20.0)
                or (cnt >= 12 and units_pnl <= -5.0 and roi <= -10.0)
            ):
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.08
                action = f'Quarantined: sustained breakdown ({units_pnl:.1f} units, {roi:+.1f}% ROI over {cnt} games) triggered zero-bleed circuit breaker'
                toxic_quarantine.append(seg_key)

            # 6. Super Alpha: Rolling ROI >= +12%, PnL >= +3 units with 5+ games (verified major markets only)
            elif cnt >= 5 and roi >= 12.0 and units_pnl >= 3.0 and league not in ('CHALLENGER', 'ITF') and not is_rolling_degraded and not is_sb_underperforming and not is_demoted_seg and rec_pnl >= 0.0:
                tier = 'SUPER_ALPHA'
                stake_cap = 8.00
                min_ev = 0.015
                action = 'Super Alpha Acceleration: maximum confidence scaling ($4.00–$8.00)'
                super_alpha.append(seg_key)

            # 7. Solid Alpha: Rolling ROI >= +5%, PnL >= +1 unit with 3+ games
            #    OR Large-Sample Empirical Alpha: cnt >= 15, ROI >= +3.5%, PnL >= +3 units
            elif ((cnt >= 3 and roi >= 5.0 and units_pnl >= 1.0)
                  or (cnt >= 15 and roi >= 3.5 and units_pnl >= 3.0)) and league not in ('CHALLENGER', 'ITF') and not is_rolling_degraded and not is_sb_underperforming and not is_demoted_seg and rec_pnl >= 0.0:
                tier = 'SOLID_ALPHA'
                stake_cap = 3.50
                min_ev = 0.010
                action = 'Solid Alpha Acceleration: prioritized scaling ($2.50–$3.50)'
                solid_alpha.append(seg_key)

            # 8. Capital Defense ($0.50 Tourniquet - Commandment 6):
            # Reserved strictly for exploratory trials (cnt in (1, 2, 3) with minor drawdown in risk units)
            elif cnt in (1, 2, 3) and units_pnl >= -1.5 and (roi < 0.0 or pnl < 0.0):
                tier = 'CAPITAL_DEFENSE'
                stake_cap = 0.50
                min_ev = 0.035
                action = 'Capital defense clamp: $0.50 flat stake, +3.5% min EV hurdle'
                capital_defense.append(seg_key)

            elif is_grad_seg:
                # Retain normal graduated standing without defense clamping if healthy
                tier = 'GRADUATED_EXPLORATION'
                stake_cap = 2.00
                min_ev = 0.02
                action = 'Graduated Market: Active verified alpha expectation'

            segments.append(SegmentMetric(
                segment=seg_key,
                league=league,
                scope=scope,
                family=market,
                bets_count=cnt,
                wins=w,
                losses=l,
                pushes=p,
                win_rate_pct=round(wr, 1),
                total_stake=round(st, 2),
                total_pnl=round(pnl, 2),
                roi_pct=round(roi, 2),
                tier=tier,
                stake_cap=stake_cap,
                min_ev_hurdle=min_ev,
                action=action
            ))

        # Sport-Level Capital Firewall (Portfolio Profit Shield, Layer 1):
        # Scale-Invariant Dynamic Risk Units (Constitution Commandments 1, 2, 3, 6 & 7):
        # Only freeze ungraduated derivatives when the league has statistically significant sustained breakdown
        # (N >= 15, units <= -10.0U, ROI <= -15.0%).
        # Graduated Tier 3 markets have passed the Crucible and MUST NEVER be frozen by a league firewall!
        league_pnls = defaultdict(float)
        league_stakes = defaultdict(float)
        league_counts = defaultdict(int)
        for s in segments:
            league_pnls[s.league] += s.total_pnl
            league_stakes[s.league] += s.total_stake
            league_counts[s.league] += s.bets_count

        for s in segments:
            lg_cnt = league_counts[s.league]
            lg_pnl = league_pnls[s.league]
            lg_stk = league_stakes[s.league]
            lg_roi = (lg_pnl / lg_stk * 100) if lg_stk > 0 else 0.0
            lg_avg_stk = (lg_stk / lg_cnt) if lg_cnt > 0 else 1.0
            lg_units = lg_pnl / max(0.50, lg_avg_stk)

            is_graduated_market = bool(
                s.segment in grad_markets
                or f"{s.league}|{s.scope}|{s.family}" in grad_markets
                or f"{s.league}|{s.scope}|{s.family.split(':')[-1]}" in grad_markets
            )

            # Firewall only freezes losing/unproven ungraduated derivatives (total_pnl <= 0.0), preserving winning alpha engines and graduated models
            if (
                lg_cnt >= 15
                and lg_units <= -10.0
                and lg_roi <= -15.0
                and s.scope != 'full_game'
                and not is_graduated_market
                and s.segment not in super_alpha
                and s.segment not in solid_alpha
                and s.total_pnl <= 0.0
            ):
                if s.segment not in toxic_quarantine:
                    toxic_quarantine.append(s.segment)
                if s.segment in capital_defense:
                    capital_defense.remove(s.segment)
                s.tier = 'TOXIC_QUARANTINE'
                s.stake_cap = 0.00
                s.action = f"Sport Firewall: Freezing non-profitable ungraduated derivative to $0.00 due to {s.league} net losses ({lg_units:.1f}U, {lg_roi:.1f}% ROI over {lg_cnt} bets)"

        target_roi = 10.0  # +10.0% weekly target per Constitution Prime Directive #1
        gap_to_target = target_roi - portfolio_roi

        report = {
            'governor': 'AutonomousROIGovernor',
            'governance_state': 'active_optimizing',
            'evaluated_at': utcnow().isoformat(),
            'window_days': days,
            'portfolio_metrics': {
                'total_settled_bets': tot_bets,
                'win_loss_push': f"{wins}-{losses}-{pushes}",
                'win_rate_pct': round(portfolio_wr, 2),
                'total_stake_usd': round(tot_stake, 2),
                'net_pnl_usd': round(tot_pnl, 2),
                'realized_roi_pct': round(portfolio_roi, 2),
                'portfolio_roi_pct': round(portfolio_roi, 2),
                'target_weekly_roi_pct': target_roi,
                'roi_status': 'ON_TARGET' if portfolio_roi >= target_roi else ('RECOVERING' if portfolio_roi >= 0 else 'DEFENSE_ACTIVE'),
                'gap_to_target_pct': round(gap_to_target, 2)
            },
            'tier_summary': {
                'super_alpha_segments_count': len(super_alpha),
                'solid_alpha_segments_count': len(solid_alpha),
                'capital_defense_segments_count': len(capital_defense),
                'toxic_quarantine_segments_count': len(toxic_quarantine)
            },
            'segments_detailed': [asdict(s) for s in segments],
            'quarantine_list': toxic_quarantine,
            'capital_defense_list': capital_defense,
            'super_alpha_list': super_alpha,
            'solid_alpha_list': solid_alpha,
            'executive_remedy': (
                f"Portfolio ROI is currently {portfolio_roi:+.2f}%. Clamping {len(capital_defense)} leaky segments "
                f"to $0.50 flat (+3.5% min EV) and quarantining {len(toxic_quarantine)} toxic micro-markets while "
                f"scaling capital up to $8.00 across {len(super_alpha) + len(solid_alpha)} Alpha segments."
            )
        }

        # Atomically persist governor status
        atomic_json(STATUS_PATH, report)
        return report


def run_governor_cycle(data_dir: Path = DATA) -> Dict[str, Any]:
    """Runs a complete ROI governor audit and adjustment cycle."""
    governor = AutonomousROIGovernor(data_dir=data_dir)
    return governor.analyze_weekly_performance(days=7)


import time

_REPORT_CACHE: Dict[str, Any] = {'ts': 0.0, 'mtime': 0.0, 'data': None}


def get_latest_governor_report(data_dir: Path = DATA) -> Dict[str, Any]:
    """Returns the cached governor report or runs an on-demand audit."""
    target_status = Path(data_dir) / 'roi_governor_status.json' if data_dir else STATUS_PATH
    if target_status.exists():
        try:
            mtime = target_status.stat().st_mtime
            now = time.time()
            if _REPORT_CACHE['data'] is not None and (now - _REPORT_CACHE['ts'] < 10.0) and (_REPORT_CACHE['mtime'] == mtime):
                return _REPORT_CACHE['data']
            data = json.loads(target_status.read_text(encoding='utf-8'))
            _REPORT_CACHE['ts'] = now
            _REPORT_CACHE['mtime'] = mtime
            _REPORT_CACHE['data'] = data
            return data
        except Exception:
            pass
    return run_governor_cycle(data_dir)
