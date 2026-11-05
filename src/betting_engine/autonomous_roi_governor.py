"""Autonomous Self-Supervising ROI & Alpha Governor.

Supreme oversight engine designed to enforce the user's prime objective:
Consistent high betting volume (minimum 100+ bets/Budapest day) with a
minimum weekly +5% to +10% portfolio ROI.

Operates across 3 dynamic control vectors:
1. Alpha Acceleration: Heavily scales capital ($3.00–$5.00) into demonstrably
   winning segments (MLB spreads/totals, WNBA ML, NFL spreads, MLS, ATP ML).
2. Capital Defense Clamp: Instantly clamps underperforming segments
   (ROI < -3%, net loss <= -$5.00) to $1.00 flat and requires +3.5% min EV.
3. Toxic Micro-Market Quarantine: Completely suppresses negative outlier
   markets (e.g. NHL regulation both teams to score, Win Rate < 25%) to prevent
   catastrophic profit drain.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json
import logging
from pathlib import Path
import sqlite3
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

        conn = sqlite3.connect(self.db_path)
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
        try:
            from .unified_market_universe import UnifiedMarketUniverse
            roster = UnifiedMarketUniverse(self.data_dir).build_universe_roster()
            grad_markets = {k for k, p in roster.items() if p.tier == 3}
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
                or any(d_k.startswith(f"{league}|{scope}|") and (market in d_k or base_mkt in d_k) for d_k in demoted_markets_set)
            )

            # Evaluate recent rolling cohort (last 10 wagers) for rolling health
            rec_bets = recent_by_seg.get(seg_key, [])[:10]
            rec_pnl = sum(b[0] for b in rec_bets) if rec_bets else 0.0
            rec_stk = sum(b[1] for b in rec_bets) if rec_bets else 0.0
            rec_roi = (rec_pnl / rec_stk * 100) if rec_stk > 0 else 0.0
            rec_avg_stk = (rec_stk / len(rec_bets)) if rec_bets else 1.0
            rec_units = rec_pnl / max(0.50, rec_avg_stk)
            is_rolling_degraded = bool(len(rec_bets) >= 4 and (rec_roi <= -3.0 or rec_units <= -2.0 or (rec_pnl < 0.0 and rec_roi < 0.0)))

            # 1. Demoted Markets in Shadow Incubator / Tier 1-2 Quarantine ($0.00 stake)
            if is_demoted_seg:
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.10
                action = 'Quarantined: demoted in Unified Market Universe / Shadow Incubator ($0.00)'
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
            elif is_rolling_degraded:
                tier = 'CAPITAL_DEFENSE'
                stake_cap = 0.50
                min_ev = 0.035
                action = f'Capital defense tourniquet: rolling drawdown ({rec_roi:.1f}% ROI, {rec_units:.1f}U over {len(rec_bets)} bets)'
                capital_defense.append(seg_key)

            # 5. Dynamic Loss Circuit Breaker (Scale-Invariant in Risk Units - Commandment 6):
            # Universal Meritocracy Mandate: Zero-Bleed on ALL markets without exception.
            # Any segment with cnt >= 4 and (pnl <= -2.00 or units_pnl <= -2.0),
            # or cnt >= 3 and pnl <= -1.50 and roi < -10.0%,
            # or cnt >= 6 and pnl < 0.0 is strictly placed in TOXIC_QUARANTINE ($0.00 stake).
            elif (
                (cnt >= 4 and (pnl <= -2.00 or units_pnl <= -2.0))
                or (cnt >= 3 and pnl <= -1.50 and roi < -10.0)
                or (cnt >= 6 and pnl < 0.0)
            ):
                tier = 'TOXIC_QUARANTINE'
                stake_cap = 0.0
                min_ev = 0.08
                action = f'Quarantined: net loss ({units_pnl:.1f} units, ${pnl:+.2f}, {roi:+.1f}% ROI) triggered zero-bleed circuit breaker'
                toxic_quarantine.append(seg_key)

            # 6. Super Alpha: Rolling ROI >= +12%, PnL >= +3 units (or >=+$3.00) with 5+ games (verified major markets only)
            elif cnt >= 5 and roi >= 12.0 and (units_pnl >= 3.0 or pnl >= 3.00) and league not in ('CHALLENGER', 'ITF') and not is_rolling_degraded and not is_demoted_seg and rec_pnl >= 0.0:
                tier = 'SUPER_ALPHA'
                stake_cap = 8.00
                min_ev = 0.015
                action = 'Super Alpha Acceleration: maximum confidence scaling ($4.00–$8.00)'
                super_alpha.append(seg_key)

            # 7. Solid Alpha: Rolling ROI >= +5%, PnL >= +1 unit (or >=+$1.00) with 2+ games
            elif ((cnt >= 2 and roi >= 15.0 and (units_pnl >= 1.0 or pnl >= 1.00))
                  or (cnt >= 4 and roi >= 5.0 and (units_pnl >= 1.5 or pnl >= 1.50))) and league not in ('CHALLENGER', 'ITF') and not is_rolling_degraded and not is_demoted_seg and rec_pnl >= 0.0:
                tier = 'SOLID_ALPHA'
                stake_cap = 3.50
                min_ev = 0.010
                action = 'Solid Alpha Acceleration: prioritized scaling ($2.50–$3.50)'
                solid_alpha.append(seg_key)

            # 8. Capital Defense ($0.50 Tourniquet - Commandment 6):
            # Reserved strictly for exploratory trials (cnt in (1, 2, 3) with minor drawdown)
            elif cnt in (1, 2, 3) and pnl >= -1.50 and (roi < 0.0 or pnl < 0.0):
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
        # If an entire sport/league has aggregate rolling net losses < -$10.00 (e.g. NHL -$43, NCAAF -$34),
        # freeze all derivative submarkets in that sport to $0.00 (toxic quarantine) to protect NBA, MLS, ATP gains!
        league_pnls = defaultdict(float)
        for s in segments:
            league_pnls[s.league] += s.total_pnl

        for s in segments:
            if league_pnls[s.league] < -10.0 and s.scope != 'full_game' and s.segment not in super_alpha:
                if s.segment not in toxic_quarantine:
                    toxic_quarantine.append(s.segment)
                if s.segment in capital_defense:
                    capital_defense.remove(s.segment)
                s.tier = 'TOXIC_QUARANTINE'
                s.stake_cap = 0.00
                s.action = f"Sport Firewall: Freezing derivative to $0.00 due to {s.league} net losses (${league_pnls[s.league]:.2f})"

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


def get_latest_governor_report(data_dir: Path = DATA) -> Dict[str, Any]:
    """Returns the cached governor report or runs an on-demand audit."""
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return run_governor_cycle(data_dir)
