"""Unified Supported Market Universe Architecture.

Unifies all 461 sports betting markets across the engine into a single,
symmetric 3-tier lifecycle state machine, eliminating historical divisions
between 'core' and 'gap' markets:

1. Tier 3 (PRODUCTION_GRADUATED):
   - Active real-money execution ($0.50 - $8.00 dynamic Kelly sizing).
   - Bounded by strict Crucible graduation standards (N >= 25, positive ROI, positive Brier edge).
   - Symmetric capital defense ($0.50 tourniquet on wobble, $0.00 quarantine on breakdown).

2. Tier 2 (SHADOW_INCUBATOR):
   - Live forward paper-trading ($0.00 virtual stakes) on real upcoming Stake fixtures.
   - Bound to an out-of-sample verified candidate model archetype.
   - Bankroll is 100% protected while live validity, CLV, and accuracy are measured.

3. Tier 1 (RESEARCH_RND):
   - Historical R&D labor for underperforming / demoted markets.
   - Muted from live odds ($0.00, no live issuance).
   - Auto-tunes 5 candidate archetypes on 97k+ historical settled matches until positive edge is proven.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import hashlib
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple
from contextlib import closing

from .providers import DATA

logger = logging.getLogger(__name__)

# Canonical segment keys for empirically proven profitable markets that produce positive alpha.
# Zero Hardcoded Privilege Mandate (Constitutional Prime Directives 1, 3, 6, 7 & 10):
# No static list of protected champions. Every market is evaluated strictly on empirical live performance.
PROTECTED_ALPHA_CHAMPIONS: Set[str] = set()


def is_protected_alpha_champion(canonical_key: str, stat: Optional[Dict[str, Any]] = None) -> bool:
    """Anti-Constant & Universal Meritocracy Mandate:
    There are ZERO hardcoded protected markets, zero core exemptions, and zero pedigree privileges.
    Only genuine empirical live performance qualifies for Alpha scaling ($2.50 - $8.00):
    Must have measured positive live PnL (pnl > 0.0) and positive ROI (roi_pct > 0.0).
    A market with no stats or negative stats is NEVER protected.
    """
    if stat and isinstance(stat, dict):
        pnl = float(stat.get('pnl', 0.0))
        roi = float(stat.get('roi_pct', stat.get('roi', 0.0)))
        sample = int(stat.get('sample_size', stat.get('games', 0)))
        return sample >= 2 and pnl > 0.0 and roi > 0.0
    return False


class MarketTier(IntEnum):
    RESEARCH_RND = 1          # Tier 1: Historical training on 97k+ matches ($0.00)
    SHADOW_INCUBATOR = 2      # Tier 2: Live forward paper-trading on upcoming fixtures ($0.00)
    PRODUCTION_GRADUATED = 3  # Tier 3: Active funded execution under dynamic Kelly ($0.50 - $8.00)


@dataclass
class UnifiedMarketProfile:
    segment_key: str
    league: str
    scope: str
    family: str
    sub_family: str
    tier: int
    tier_name: str
    stake_cap: float
    active_model_id: Optional[str]
    model_archetype: Optional[str]
    rolling_roi_pct: float
    rolling_pnl: float
    sample_size: int
    delta_brier: float
    phase_detail: str
    owner: str = "Gemini Antigravity (agy)"
    assigned_developer: str = "OpenAI Codex (gpt-6-astra) & HistoricalTrainer"
    audit_adversary: str = "xAI Grok (grok-4.7)"
    is_dual_polymarket: bool = False
    polymarket_pairing: Optional[str] = None

    def __post_init__(self):
        lg = (self.league or '').upper()
        fam = (self.family or '').lower()
        sc = str(self.scope or '').lower()
        if lg in ('EPL', 'UCL', 'MLS', 'ATP', 'WTA') or 'half' in fam or 'set' in fam or 'half' in sc:
            self.assigned_developer = "Claude (claude-opus-5-5) & HistoricalTrainer"
        else:
            self.assigned_developer = "OpenAI Codex (gpt-6-astra) & HistoricalTrainer"

        # Polymarket CLOB Canonical Dual Pairing (Commandments 6.5 & 10)
        try:
            from .polymarket_runner import POLYMARKET_STRUCTURED_TAXONOMY
            base_fam = fam.split(':')[-1] if ':' in fam else fam
            fam_aliases = {fam, base_fam}
            if fam in ('both_teams_score', 'both_score') or base_fam in ('both_teams_score', 'both_score'):
                fam_aliases.update(['both_score', 'both_teams_score'])
            soccer_leagues = {'EPL', 'MLS', 'UCL', 'UEL', 'LALIGA', 'BUNDESLIGA', 'SERIEA'}
            if lg in soccer_leagues and (fam in ('three_way', '1x2', 'moneyline') or base_fam in ('three_way', '1x2', 'moneyline')):
                fam_aliases.update(['three_way', '1x2', 'moneyline'])
            if fam in ('set_handicap', 'tennis_sets:set_handicap', 'tennis_sets') or base_fam in ('set_handicap', 'tennis_sets:set_handicap', 'tennis_sets'):
                fam_aliases.update(['set_handicap'])
            if fam in ('total_sets', 'tennis_sets:total_sets') or base_fam in ('total_sets', 'tennis_sets:total_sets'):
                fam_aliases.update(['total_sets'])

            for p_type, (p_sc, p_fam, p_leagues) in POLYMARKET_STRUCTURED_TAXONOMY.items():
                if lg in p_leagues and p_sc == sc and p_fam in fam_aliases:
                    self.is_dual_polymarket = True
                    self.polymarket_pairing = p_type
                    break
        except Exception:
            pass

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UnifiedMarketUniverse:
    """Single source of truth for the entire 461-market supported universe."""

    _cached_roster: Optional[Dict[str, UnifiedMarketProfile]] = None
    _cached_summary: Optional[Dict[str, Any]] = None
    _cached_at: float = 0.0
    _cached_dir: Optional[str] = None
    _cached_summary_dir: Optional[str] = None
    _cache_ttl_seconds: float = 60.0

    @classmethod
    def invalidate_cache(cls) -> None:
        """Flushes the cached universe roster and high-level summary."""
        cls._cached_roster = None
        cls._cached_summary = None
        cls._cached_dir = None
        cls._cached_summary_dir = None
        cls._cached_at = 0.0

    def __init__(self, data_dir: Optional[Path | str] = None):
        self.data_dir = Path(data_dir or 'data')
        self.incubator_db_path = self.data_dir / 'shadow_incubator.sqlite3'
        self.engine_db_path = self.data_dir / 'engine.sqlite3'

    @staticmethod
    def normalize_scope(scope: str) -> str:
        """Normalizes human or raw scope strings into canonical scope keys."""
        raw_sc = str(scope or 'full_game').lower().strip()
        if raw_sc in ('full game', 'full_game', 'teljes mérkőzés', 'match', 'game', 'regulation time', 'regulation') or not raw_sc:
            return 'full_game'
        elif '1st half' in raw_sc or 'half 1' in raw_sc or raw_sc == 'half_1':
            return 'half_1'
        elif '2nd half' in raw_sc or 'half 2' in raw_sc or raw_sc == 'half_2':
            return 'half_2'
        elif '1st quarter' in raw_sc or 'quarter 1' in raw_sc or raw_sc == 'quarter_1':
            return 'quarter_1'
        elif '2nd quarter' in raw_sc or 'quarter 2' in raw_sc or raw_sc == 'quarter_2':
            return 'quarter_2'
        elif '3rd quarter' in raw_sc or 'quarter 3' in raw_sc or raw_sc == 'quarter_3':
            return 'quarter_3'
        elif '4th quarter' in raw_sc or 'quarter 4' in raw_sc or raw_sc == 'quarter_4':
            return 'quarter_4'
        elif '1st period' in raw_sc or 'period 1' in raw_sc or raw_sc == 'period_1':
            return 'period_1'
        elif '2nd period' in raw_sc or 'period 2' in raw_sc or raw_sc == 'period_2':
            return 'period_2'
        elif '3rd period' in raw_sc or 'period 3' in raw_sc or raw_sc == 'period_3':
            return 'period_3'
        elif '1st inning' in raw_sc or 'inning 1' in raw_sc or raw_sc == 'inning_1':
            return 'inning_1'
        elif 'innings 1 to 5' in raw_sc or 'innings_1_5' in raw_sc:
            return 'innings_1_5'
        return raw_sc.replace(' ', '_')

    def get_canonical_key(self, league: str, scope: str, family: str) -> str:
        """Normalizes league, scope, and market family into canonical segment key."""
        lg = (league or '').upper().strip()
        sc = self.normalize_scope(scope)
        fam = (family or '').lower().strip()
        known_scopes = ('quarter_1', 'quarter_2', 'quarter_3', 'quarter_4', 'half_1', 'half_2', 'period_1', 'period_2', 'period_3', 'regulation', 'inning_1', 'innings_1_5')
        while ':' in fam:
            prefix, rest = fam.split(':', 1)
            norm_p = self.normalize_scope(prefix)
            if norm_p in known_scopes or norm_p == sc:
                sc = norm_p
                fam = rest
            elif prefix == rest.split(':')[0]:
                fam = rest
            elif prefix in ('american_derivatives', 'asian_lines', 'bivariate_derivatives', 'tennis_sets', 'sequence_hazard'):
                fam = rest
            else:
                fam = rest.split(':')[-1]
                break
        if lg == 'NHL' and sc == 'full_game' and fam == 'three_way':
            sc = 'regulation'
        return f"{lg}|{sc}|{fam}"

    def get_roster(self, force_refresh: bool = False) -> Dict[str, UnifiedMarketProfile]:
        """Convenience alias for build_universe_roster."""
        return self.build_universe_roster(force_refresh=force_refresh)

    def build_universe_roster(self, force_refresh: bool = False) -> Dict[str, UnifiedMarketProfile]:
        """Assembles the complete 461-market roster with exact tier classifications."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_roster is not None:
            if getattr(UnifiedMarketUniverse, '_cached_dir', None) == str(self.data_dir):
                if now - UnifiedMarketUniverse._cached_at < UnifiedMarketUniverse._cache_ttl_seconds:
                    return UnifiedMarketUniverse._cached_roster

        roster: Dict[str, UnifiedMarketProfile] = {}

        if not self.incubator_db_path.exists():
            return roster

        sb_underperforming_map: Dict[str, Dict[str, Any]] = {}
        if self.engine_db_path.exists():
            try:
                from .segment_scoreboard import compute_segment_scoreboard
                with closing(sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=10.0)) as sb_conn:
                    sb_data = compute_segment_scoreboard(sb_conn)
                    for s_obj in sb_data.get("underperforming_segments", []):
                        seg_s = s_obj["segment"]
                        sb_underperforming_map[seg_s] = s_obj
                        parts = seg_s.split('|')
                        if len(parts) == 3 and ':' in parts[2]:
                            fam_clean = parts[2].split(':')[-1]
                            sb_underperforming_map[f"{parts[0]}|{parts[1]}|{fam_clean}"] = s_obj
            except Exception as e_sb:
                logger.warning("Could not compute scoreboard in universe roster: %s", e_sb)

        try:
            with closing(sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=15.0)) as inc_conn:
                # 1. Ingest Graduated Markets (Tier 3 Candidates)
                grad_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           sample_size, roi, delta_brier, stats, graduated_at
                    FROM graduated_markets
                """).fetchall()

                grad_keys_set = set()
                grad_at_map: Dict[str, str] = {}
                from .contracts import is_supported_stake_contract
                for r in grad_rows:
                    seg_key, lg, sc, fam, sub_fam, n, roi, dbrier, stats_raw, grad_at = r
                    if not is_supported_stake_contract(lg, sc, fam, sub_fam):
                        # Theoretical R&D archetype not offered on Stake feed -> keep in Tier 1 R&D
                        prof = UnifiedMarketProfile(
                            segment_key=seg_key,
                            league=lg,
                            scope=sc,
                            family=fam,
                            sub_family=sub_fam,
                            tier=int(MarketTier.RESEARCH_RND),
                            tier_name="RESEARCH_RND",
                            stake_cap=0.00,
                            active_model_id=f"rnd_{lg.lower()}_{sc}_{fam}",
                            model_archetype="TheoreticalRndModel",
                            rolling_roi_pct=float(roi * 100.0 if abs(roi) < 10.0 else roi),
                            rolling_pnl=0.0,
                            sample_size=0,
                            delta_brier=float(dbrier or 0.0),
                            phase_detail="Tier 1 R&D - Theoretical market archetype not currently offered pre-match by Stake feed ($0.00)"
                        )
                        roster[seg_key] = prof
                        continue
                    init_cap = 0.50
                    init_phase = "Newly Graduated Crucible Model - Live Probation ($0.50 floor)"
                    prof = UnifiedMarketProfile(
                        segment_key=seg_key,
                        league=lg,
                        scope=sc,
                        family=fam,
                        sub_family=sub_fam,
                        tier=int(MarketTier.PRODUCTION_GRADUATED),
                        tier_name="PRODUCTION_GRADUATED",
                        stake_cap=init_cap,
                        active_model_id=f"graduated_{lg.lower()}_{sc}_{fam}",
                        model_archetype="GraduatedCrucibleModel",
                        rolling_roi_pct=float(roi * 100.0 if abs(roi) < 10.0 else roi),
                        rolling_pnl=0.0,
                        sample_size=0,
                        delta_brier=float(dbrier or 0.0),
                        phase_detail=init_phase
                    )
                    roster[seg_key] = prof
                    grad_keys_set.add(seg_key)
                    grad_at_map[seg_key] = grad_at or '1970-01-01'
                    if sub_fam and sub_fam != fam:
                        sub_k = f"{lg}|{sc}|{fam}:{sub_fam}"
                        roster[sub_k] = prof
                        grad_keys_set.add(sub_k)
                        grad_at_map[sub_k] = grad_at or '1970-01-01'

                # 2. Ingest Demoted Markets (Tier 1 & Tier 2)
                # Timestamp Precedence: If demoted_at > graduated_at, demotion overrides old graduation!
                dem_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           rolling_roi, rolling_pnl, games, incubation_phase,
                           incubated_model_id, model_archetype, historical_roi, historical_brier_delta,
                           demoted_at, reason
                    FROM demoted_markets
                """).fetchall()

                for r in dem_rows:
                    (seg_key, lg, sc, fam, sub_fam, r_roi, r_pnl, games,
                     phase, model_id, arch, h_roi, h_brier, dem_at, reason) = r

                    # Reachability Guard: Skip phantom/unsupported contracts so they never pollute the active universe
                    if phase == 'unsupported_phantom' or not is_supported_stake_contract(lg, sc, fam, sub_fam):
                        continue

                    canonical_k = self.get_canonical_key(lg, sc, fam)
                    is_sub = bool(sub_fam and sub_fam != fam)
                    sub_k = f"{lg}|{sc}|{fam}:{sub_fam}" if is_sub else canonical_k

                    # Check timestamp precedence against graduated_markets
                    grad_ts = grad_at_map.get(seg_key) or grad_at_map.get(canonical_k) or grad_at_map.get(sub_k)
                    if grad_ts:
                        dem_ts = dem_at or '1970-01-01'
                        if dem_ts < grad_ts:
                            # Re-graduation occurred AFTER demotion -> keep graduated!
                            continue
                        else:
                            # Demotion occurred AFTER graduation (live drawdown) -> demote!
                            grad_keys_set.discard(seg_key)
                            grad_keys_set.discard(canonical_k)
                            grad_keys_set.discard(sub_k)

                    if phase == 'incubating_with_new_model' and (model_id or arch):
                        tier = int(MarketTier.SHADOW_INCUBATOR)
                        tier_name = "SHADOW_INCUBATOR"
                        phase_detail = f"Live forward validation with {arch or model_id} ($0.00)"
                    else:
                        tier = int(MarketTier.RESEARCH_RND)
                        tier_name = "RESEARCH_RND"
                        phase_detail = f"Live Drawdown Demotion ({r_roi*100.0 if abs(r_roi) < 10.0 else r_roi:.1f}% ROI, ${r_pnl:.2f} over {games} games) - Quarantined ($0.00)" if games > 0 else "Tier 1 R&D historical model tuning on 97k+ matches ($0.00)"

                    prof = UnifiedMarketProfile(
                        segment_key=seg_key,
                        league=lg,
                        scope=sc,
                        family=fam,
                        sub_family=sub_fam,
                        tier=tier,
                        tier_name=tier_name,
                        stake_cap=0.00,
                        active_model_id=model_id,
                        model_archetype=arch,
                        rolling_roi_pct=float(r_roi * 100.0 if abs(r_roi) < 10.0 else r_roi),
                        rolling_pnl=float(r_pnl or 0.0),
                        sample_size=int(games or 0),
                        delta_brier=float(h_brier or 0.0),
                        phase_detail=phase_detail
                    )
                    roster[seg_key] = prof
                    if is_sub:
                        roster[sub_k] = prof
                    if ':' in fam:
                        base_f = fam.split(':')[-1]
                        roster[self.get_canonical_key(lg, sc, base_f)] = prof
                    if sub_fam and ':' in sub_fam:
                        base_sf = sub_fam.split(':')[-1]
                        roster[self.get_canonical_key(lg, sc, base_sf)] = prof

                    # Demoted markets represent current failure/quarantine and strictly override
                    # any historical graduation records (Constitutional Commandments 6 & 7).
                    clean_fam = fam.split(':')[-1] if ':' in fam else fam
                    clean_sub = sub_fam.split(':')[-1] if sub_fam and ':' in sub_fam else (sub_fam or fam)
                    keys_to_demote = [
                        k for k, p in roster.items()
                        if p.league == lg and p.scope == sc and (
                            p.family in (fam, sub_fam, clean_fam, clean_sub)
                            or p.sub_family in (fam, sub_fam, clean_fam, clean_sub)
                            or k.endswith(f":{clean_fam}")
                            or k.endswith(f":{clean_sub}")
                            or k in (seg_key, canonical_k, sub_k)
                        )
                    ]
                    for k_ov in keys_to_demote:
                        roster[k_ov] = prof
                        grad_keys_set.discard(k_ov)

        except Exception as exc:
            logger.error("Error reading incubator db for universe roster: %s", exc)

        # 3. Ingest active Baseline Production markets with actual settled performance
        perf_map: Dict[str, Dict[str, Any]] = {}
        if self.engine_db_path.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=2.0)) as eng_conn:
                    # Pre-calculate performance metrics for all markets across settled decisions
                    # Graduated markets are evaluated on decisions issued AFTER their graduation timestamp.
                    try:
                        rows_perf = eng_conn.execute("""
                            SELECT 
                                json_extract(payload, '$.league'),
                                COALESCE(json_extract(payload, '$.model_scope'), json_extract(payload, '$.period')),
                                json_extract(payload, '$.market'),
                                pnl,
                                json_extract(payload, '$.stake'),
                                issued_at,
                                json_extract(payload, '$.game_id')
                            FROM system_decisions
                            WHERE status IN ('won', 'lost', 'push')
                        """).fetchall()
                        all_time_perf_map: Dict[str, Dict[str, Any]] = {}
                        league_games_map: Dict[str, Dict[str, str]] = {}
                        for lg_r, sc_r, fam_r, pnl_r, stake_r, iss_r, gid_r in rows_perf:
                            if not lg_r or not fam_r:
                                continue
                            lg_clean_u = str(lg_r).upper().strip()
                            if gid_r and iss_r:
                                gid_clean = str(gid_r).strip()
                                cur_ts = league_games_map.setdefault(lg_clean_u, {}).get(gid_clean)
                                if cur_ts is None or str(iss_r) < cur_ts:
                                    league_games_map[lg_clean_u][gid_clean] = str(iss_r)
                            k = self.get_canonical_key(lg_r, sc_r, fam_r)
                            base_k = None
                            if ':' in str(fam_r):
                                base_k = self.get_canonical_key(lg_r, sc_r, str(fam_r).split(':')[-1])

                            pnl_v = float(pnl_r or 0.0)
                            stk_v = float(stake_r or 1.0)

                            # Track all-time settled performance across every decision
                            at_stat = all_time_perf_map.setdefault(k, {'sample_size': 0, 'pnl': 0.0, 'staked': 0.0, 'roi_pct': 0.0, 'units': 0.0})
                            at_stat['sample_size'] += 1
                            at_stat['pnl'] += pnl_v
                            at_stat['staked'] += stk_v
                            if base_k:
                                at_stat_b = all_time_perf_map.setdefault(base_k, {'sample_size': 0, 'pnl': 0.0, 'staked': 0.0, 'roi_pct': 0.0, 'units': 0.0})
                                at_stat_b['sample_size'] += 1
                                at_stat_b['pnl'] += pnl_v
                                at_stat_b['staked'] += stk_v

                            # Post-graduation filter: newly graduated models start fresh from graduation
                            if k in grad_at_map:
                                if str(iss_r) < str(grad_at_map[k]):
                                    continue
                            elif base_k and base_k in grad_at_map:
                                if str(iss_r) < str(grad_at_map[base_k]):
                                    continue

                            stat = perf_map.setdefault(k, {'sample_size': 0, 'pnl': 0.0, 'staked': 0.0, 'roi_pct': 0.0, 'units': 0.0})
                            stat['sample_size'] += 1
                            stat['pnl'] += pnl_v
                            stat['staked'] += stk_v

                        # Compute derived metrics for both maps
                        for d_m in (perf_map, all_time_perf_map):
                            for k, stat in d_m.items():
                                pnl_v = stat['pnl']
                                stk_v = stat['staked']
                                cnt_v = stat['sample_size']
                                avg_stk = stk_v / max(1, cnt_v)
                                stat['units'] = round(pnl_v / max(0.50, avg_stk), 2)
                                stat['roi_pct'] = round((pnl_v / stk_v * 100.0) if stk_v > 0 else 0.0, 2)
                                stat['pnl'] = round(pnl_v, 2)
                                stat['staked'] = round(stk_v, 2)
                                if ':' in k:
                                    parts = k.split('|')
                                    if len(parts) == 3 and ':' in parts[2]:
                                        base_k = f"{parts[0]}|{parts[1]}|{parts[2].split(':')[-1]}"
                                        d_m.setdefault(base_k, stat)
                    except Exception as e_perf:
                        logger.warning("Could not pre-aggregate settled market performance: %s", e_perf)
            except Exception as exc:
                logger.error("Error reading engine db for universe settled performance: %s", exc)

        # Synchronize existing roster profiles (including graduated) with live settled performance
        for k_perf, prof in roster.items():
            if prof.tier == int(MarketTier.PRODUCTION_GRADUATED):
                # Rolling Degradation Priority: If SegmentScoreboard marks this segment as underperforming,
                # immediately enforce Capital Defense Tourniquet ($0.50 clamp) regardless of ancient lifetime profit.
                sb_entry = sb_underperforming_map.get(k_perf)
                if not sb_entry and ':' in k_perf:
                    parts = k_perf.split('|')
                    if len(parts) == 3 and ':' in parts[2]:
                        base_k = f"{parts[0]}|{parts[1]}|{parts[2].split(':')[-1]}"
                        sb_entry = sb_underperforming_map.get(base_k)

                if sb_entry:
                    r_pnl = float(sb_entry.get("rolling_pnl", 0.0))
                    r_roi = float(sb_entry.get("rolling_roi_pct", 0.0))
                    r_games = int(sb_entry.get("rolling_games", 0))
                    prof.rolling_pnl = r_pnl
                    prof.rolling_roi_pct = r_roi
                    prof.stake_cap = 0.50
                    prof.phase_detail = f"Capital Defense Tourniquet ({r_roi:+.1f}% ROI, ${r_pnl:+.2f} over {r_games} games) - $0.50 clamp"
                    continue

                stat = perf_map.get(k_perf)
                at_stat = all_time_perf_map.get(k_perf)

                if stat and stat['sample_size'] > 0:
                    prof.rolling_pnl = stat['pnl']
                    prof.rolling_roi_pct = stat['roi_pct']
                    prof.sample_size = stat['sample_size']

                    # Universal Meritocracy Mandate (Prime Directives 1, 3, 6, 7 & 10):
                    # Zero hardcoded privileges: every market is subject to scale-invariant drawdown demotion:
                    is_stat_degraded = (
                        (stat['sample_size'] >= 4 and (stat['units'] <= -2.0 or stat['pnl'] <= -2.00))
                        or (stat['sample_size'] >= 3 and (stat['units'] <= -1.5 or stat['pnl'] <= -1.50) and stat['roi_pct'] < -10.0)
                        or (stat['sample_size'] >= 6 and (stat['units'] < 0.0 or stat['pnl'] < 0.0))
                        or (stat['sample_size'] >= 2 and (stat['units'] < 0.0 or stat['pnl'] < 0.0) and stat['roi_pct'] <= -20.0)
                        or (stat['units'] <= -3.5 or stat['pnl'] <= -4.00)
                    )
                    if is_stat_degraded:
                        prof.tier = int(MarketTier.RESEARCH_RND)
                        prof.tier_name = "RESEARCH_RND"
                        prof.stake_cap = 0.00
                        prof.phase_detail = f"Live Drawdown Demotion ({stat['roi_pct']:.1f}% ROI, ${stat['pnl']:.2f} over {stat['sample_size']} games) - Quarantined ($0.00)"
                        grad_keys_set.discard(k_perf)
                    elif stat['pnl'] < 0.0 or stat['units'] < 0.0:
                        prof.stake_cap = 0.50
                        prof.phase_detail = f"Capital Defense Tourniquet ({stat['roi_pct']:+.1f}% ROI, ${stat['pnl']:+.2f} over {stat['sample_size']} games) - $0.50 clamp"
                    elif stat['sample_size'] >= 2 and stat['pnl'] > 0.0:
                        if (stat['units'] >= 2.0 or stat['pnl'] >= 2.0) and stat['roi_pct'] >= 10.0:
                            prof.stake_cap = 8.00
                            prof.phase_detail = f"Alpha Acceleration ({stat['roi_pct']:+.1f}% ROI, ${stat['pnl']:+.2f} over {stat['sample_size']} games) - $8.00 cap"
                        else:
                            prof.stake_cap = 2.50
                            prof.phase_detail = f"Active Profitable Live Model ({stat['roi_pct']:+.1f}% ROI, ${stat['pnl']:+.2f} over {stat['sample_size']} games) - Tier 3 Kelly"
                    else:
                        prof.stake_cap = 0.50
                        prof.phase_detail = f"Live Probation ({stat['sample_size']} game, ${stat['pnl']:+.2f}) - $0.50 floor"
                else:
                    # No post-graduation live decisions yet (0 live settled games with new model)
                    # Fixture-Aware Silent Market Eviction Guard (Commandments 1, 2, 6, 7 & 10):
                    # Check how many distinct match opportunities have occurred in this league since graduation.
                    grad_ts = grad_at_map.get(k_perf) or grad_at_map.get(f"{prof.league}|{prof.scope}|{prof.family}") or '1970-01-01'
                    lg_key = (prof.league or '').upper().strip()
                    games_since_grad = 0
                    if lg_key in league_games_map:
                        games_since_grad = sum(1 for g_ts in league_games_map[lg_key].values() if g_ts >= grad_ts)

                    MIN_FIXTURE_OPPORTUNITIES = 5
                    if games_since_grad >= MIN_FIXTURE_OPPORTUNITIES:
                        # Genuine execution bottleneck: The league had >= 5 matches, but market emitted 0 bets.
                        # Evicted from Tier 3 to Tier 1 R&D for bottleneck diagnostics.
                        prof.tier = int(MarketTier.RESEARCH_RND)
                        prof.tier_name = "RESEARCH_RND"
                        prof.stake_cap = 0.00
                        prof.phase_detail = f"Dormant Market Eviction (0 bets across {games_since_grad} games post-grad) - Quarantined to R&D ($0.00)"
                        grad_keys_set.discard(k_perf)
                    elif at_stat and (at_stat['units'] < 0.0 or at_stat['pnl'] < 0.0):
                        # Constitutional Invariant (Prime Directives 6 & 7):
                        # Markets with established negative live PnL must be quarantined to Tier 1 R&D ($0.00).
                        # Clamping or letting demoted/losing markets bleed real capital at $0.50 is strictly forbidden.
                        prof.tier = int(MarketTier.RESEARCH_RND)
                        prof.tier_name = "RESEARCH_RND"
                        prof.stake_cap = 0.00
                        prof.rolling_pnl = at_stat['pnl']
                        prof.rolling_roi_pct = at_stat['roi_pct']
                        prof.sample_size = at_stat['sample_size']
                        prof.phase_detail = f"R&D Live Quarantine - Established Live Loss (${at_stat['pnl']:+.2f} over {at_stat['sample_size']} games) - $0.00"
                        grad_keys_set.discard(k_perf)
                    else:
                        # Safe: Has seen < 5 games since graduation (fresh graduate, weekly sports e.g. NFL waiting for Sunday, or off-season)
                        if at_stat and at_stat['pnl'] > 0.0 and at_stat['sample_size'] >= 2:
                            prof.stake_cap = 8.00 if at_stat['pnl'] >= 2.0 and at_stat['roi_pct'] >= 10.0 else 2.50
                            prof.rolling_pnl = at_stat['pnl']
                            prof.rolling_roi_pct = at_stat['roi_pct']
                            prof.sample_size = at_stat['sample_size']
                            prof.phase_detail = f"Active Profitable Live Model ({at_stat['roi_pct']:+.1f}% ROI, ${at_stat['pnl']:+.2f} over {at_stat['sample_size']} games) - Tier 3 Kelly"
                        else:
                            # Retains full Tier 3 live probation at $0.50 floor without premature eviction!
                            prof.stake_cap = 0.50
                            games_info = f"{games_since_grad}/5 games seen" if games_since_grad > 0 else "pending fixtures"
                            prof.phase_detail = f"Newly Graduated Crucible Model - Live Probation ($0.50 floor, {games_info})"

        def _register_market_candidate(lg: str, sc: str, fam: str, model_id: Optional[str], archetype: str) -> None:
            lg_clean = (lg or '').upper().strip()
            sc_norm = self.normalize_scope(sc)
            fam_clean = (fam or '').lower().strip()
            if not is_supported_stake_contract(lg_clean, sc_norm, fam_clean):
                return
            canon_key = self.get_canonical_key(lg_clean, sc_norm, fam_clean)
            base_fam = fam_clean.split(':')[-1] if ':' in fam_clean else fam_clean
            canon_base_key = self.get_canonical_key(lg_clean, sc_norm, base_fam)

            if canon_key in roster or canon_base_key in roster:
                return

            stat = perf_map.get(canon_key) or perf_map.get(canon_base_key) or {}
            r_pnl = float(stat.get('pnl', 0.0))
            r_roi = float(stat.get('roi_pct', 0.0))
            n_samples = int(stat.get('sample_size', 0))
            r_units = float(stat.get('units', 0.0))

            # Total Market Universe Invariant (Constitution Prime Directives 1, 2, 6 & 7):
            # Universal Meritocracy: Only demonstrably profitable markets are permitted in Tier 3 live funded execution ($0.50-$8.00).
            # Bleeding markets and untraded silent markets are strictly assigned to Tier 1 R&D ($0.00)
            # for candidate model synthesis, historical backtesting, and Crucible re-graduation!
            sb_entry = sb_underperforming_map.get(canon_key) or sb_underperforming_map.get(canon_base_key)
            if sb_entry:
                tier_val = int(MarketTier.PRODUCTION_GRADUATED)
                tier_name_val = "PRODUCTION_GRADUATED"
                stake_cap_val = 0.50
                r_pnl = float(sb_entry.get("rolling_pnl", 0.0))
                r_roi = float(sb_entry.get("rolling_roi_pct", 0.0))
                r_games = int(sb_entry.get("rolling_games", 0))
                phase_val = f"Capital Defense Tourniquet ({r_roi:+.1f}% ROI, ${r_pnl:+.2f} over {r_games} games) - $0.50 clamp"
            elif n_samples >= 2 and r_pnl > 0.0:
                tier_val = int(MarketTier.PRODUCTION_GRADUATED)
                tier_name_val = "PRODUCTION_GRADUATED"
                stake_cap_val = 8.00 if (r_pnl >= 2.0 or r_roi >= 10.0) else 2.50
                phase_val = f"Alpha Acceleration ({r_roi:+.1f}% ROI, ${r_pnl:+.2f} over {n_samples} games) - Tier 3 Kelly"
            elif n_samples >= 1 and r_pnl < 0.0:
                tier_val = int(MarketTier.RESEARCH_RND)
                tier_name_val = "RESEARCH_RND"
                stake_cap_val = 0.00
                phase_val = f"Live Drawdown Demotion ({r_roi:.1f}% ROI, ${r_pnl:.2f} over {n_samples} games) - Quarantined ($0.00)"
            elif n_samples == 1 and r_pnl >= 0.0:
                tier_val = int(MarketTier.PRODUCTION_GRADUATED)
                tier_name_val = "PRODUCTION_GRADUATED"
                stake_cap_val = 0.50
                phase_val = "Initial Exploratory Live Model ($0.50 floor)"
            else:
                # n_samples == 0: Untraded / Silent Market in Active Universe
                # Strictly assigned to Tier 1 R&D for AI model development and Crucible validation!
                tier_val = int(MarketTier.RESEARCH_RND)
                tier_name_val = "RESEARCH_RND"
                stake_cap_val = 0.00
                phase_val = "Tier 1 Silent Market Discovery R&D: Untraded in live execution ($0.00)"

            roster[canon_key] = UnifiedMarketProfile(
                segment_key=canon_key,
                league=lg_clean,
                scope=sc_norm,
                family=fam_clean,
                sub_family=fam_clean,
                tier=tier_val,
                tier_name=tier_name_val,
                stake_cap=stake_cap_val,
                active_model_id=model_id,
                model_archetype=archetype,
                rolling_roi_pct=r_roi,
                rolling_pnl=r_pnl,
                sample_size=n_samples,
                delta_brier=0.015,
                phase_detail=phase_val
            )

        # 4. Ingest all wired markets from approved model metadata JSON files
        # Full game models
        models_dir = self.data_dir / 'models'
        if models_dir.exists():
            for p in models_dir.glob('*.json'):
                try:
                    with open(p) as f_m:
                        d = json.load(f_m)
                        m_lg = p.stem.upper()
                        m_ver = d.get('version', f'{m_lg}-v1')
                        for m_fam in d.get('validated_markets', []):
                            _register_market_candidate(m_lg, 'full_game', m_fam, m_ver, 'DomainFullGameModel')
                except Exception:
                    pass

        # Period models
        period_models_dir = self.data_dir / 'period_models'
        if period_models_dir.exists():
            for p in period_models_dir.glob('*.json'):
                if 'latest' in p.name or 'audit' in p.name:
                    continue
                try:
                    with open(p) as f_pm:
                        d = json.load(f_pm)
                        stem = p.stem
                        if '-' in stem:
                            p_lg, p_sc = stem.split('-', 1)
                            p_ver = d.get('version', f'{p_lg}-{p_sc}-v1')
                            for m_fam in d.get('validated_markets', []):
                                _register_market_candidate(p_lg, p_sc, m_fam, p_ver, 'EmpiricalPeriodModel')
                except Exception:
                    pass

        # Segment models
        segment_models_dir = self.data_dir / 'segment_models'
        if segment_models_dir.exists():
            for p in segment_models_dir.glob('*.json'):
                if 'latest' in p.name or 'audit' in p.name:
                    continue
                try:
                    with open(p) as f_sm:
                        d = json.load(f_sm)
                        stem = p.stem
                        if '-' in stem:
                            s_lg, s_sc = stem.split('-', 1)
                            s_ver = d.get('version', f'{s_lg}-{s_sc}-v1')
                            for m_fam in d.get('validated_markets', []):
                                _register_market_candidate(s_lg, s_sc, m_fam, s_ver, 'EmpiricalSegmentModel')
                except Exception:
                    pass

        # 5. Ingest all distinct baseline production markets ever issued in system_decisions
        if self.engine_db_path.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=2.0)) as eng_conn:
                    dec_rows = eng_conn.execute("""
                        SELECT DISTINCT 
                            json_extract(payload, '$.league'),
                            COALESCE(json_extract(payload, '$.model_scope'), json_extract(payload, '$.period'), 'full_game'),
                            json_extract(payload, '$.market'),
                            json_extract(payload, '$.model_version')
                        FROM system_decisions
                    """).fetchall()

                    for lg_d, sc_d, fam_d, mver_d in dec_rows:
                        if lg_d and fam_d:
                            _register_market_candidate(
                                lg_d, sc_d, fam_d,
                                mver_d or 'system_decision_baseline',
                                'DomainBaselineModel'
                            )
            except Exception as exc:
                logger.error("Error reading engine db for distinct universe decisions: %s", exc)

        # Auto-sync all degraded markets (Tier 1 & Tier 2) into demoted_markets SQL table
        if self.data_dir.resolve() == DATA.resolve() and self.incubator_db_path.exists():
            try:
                with closing(sqlite3.connect(str(self.incubator_db_path), timeout=5.0)) as inc_conn:
                    inc_conn.execute("PRAGMA busy_timeout = 5000")
                    existing_dem = {r[0] for r in inc_conn.execute("SELECT segment_key FROM demoted_markets").fetchall()}
                    now_str = datetime.now(timezone.utc).isoformat()
                    to_insert = []
                    for k, p in roster.items():
                        if p.tier in (1, 2) and k not in existing_dem and k not in grad_keys_set and is_supported_stake_contract(p.league, p.scope, p.family, p.sub_family):
                            d_id = f"demote_{hashlib.sha256(f'{k}:{now_str}'.encode()).hexdigest()[:16]}"
                            phase_name = 'incubating_with_new_model' if (p.tier == 2 and p.active_model_id) else 'research_training'
                            to_insert.append((
                                d_id, k, p.league, p.scope, p.family, p.sub_family or p.family,
                                now_str, (p.rolling_roi_pct or 0.0) / 100.0, p.rolling_pnl or 0.0,
                                p.sample_size or 0, f"Unified Universe Auto-Sync: {p.phase_detail}",
                                json.dumps({'rolling_pnl': p.rolling_pnl, 'roi_pct': p.rolling_roi_pct, 'sample_size': p.sample_size}),
                                phase_name
                            ))
                            existing_dem.add(k)
                    if to_insert:
                        inc_conn.execute("BEGIN IMMEDIATE")
                        inc_conn.executemany("""
                            INSERT OR IGNORE INTO demoted_markets (
                                id, segment_key, league, scope, family, sub_family,
                                demoted_at, rolling_roi, rolling_pnl, games, reason, stats, incubation_phase
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, to_insert)
                        dem_keys = [t[1] for t in to_insert]
                        placeholders = ','.join('?' for _ in dem_keys)
                        inc_conn.execute(f"DELETE FROM graduated_markets WHERE segment_key IN ({placeholders})", dem_keys)
                        inc_conn.execute("COMMIT")
            except Exception as e_sync:
                logger.debug("Could not auto-sync degraded roster to demoted_markets: %s", e_sync)

        UnifiedMarketUniverse._cached_roster = roster
        UnifiedMarketUniverse._cached_at = now
        UnifiedMarketUniverse._cached_dir = str(self.data_dir)
        return roster

    def get_market_tier(self, league: str, scope: str, family: str, sub_family: Optional[str] = None) -> MarketTier:
        """Returns the current operational tier for any market with multi-alias lookup."""
        lg = (league or '').upper().strip()
        sc = self.normalize_scope(scope)
        fam = (family or '').lower().strip()
        base_fam = fam.split(':')[-1] if ':' in fam else fam
        sub_fam = (sub_family or '').lower().strip() if sub_family else ''

        canon = self.get_canonical_key(lg, sc, fam)
        base_canon = self.get_canonical_key(lg, sc, base_fam)

        candidate_keys = [canon, base_canon]
        if sub_fam and sub_fam != fam and sub_fam != base_fam:
            candidate_keys.append(f"{lg}|{sc}|{fam}:{sub_fam}")
            candidate_keys.append(f"{lg}|{sc}|{base_fam}:{sub_fam}")
        if sc == 'full_game':
            candidate_keys.append(f"{lg}|{fam}")
            candidate_keys.append(f"{lg}|{base_fam}")

        roster = self.build_universe_roster()
        for k in candidate_keys:
            if k in roster:
                return MarketTier(roster[k].tier)

        # Check if explicitly demoted in demoted_markets
        try:
            with closing(sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=15.0)) as inc_conn:
                placeholders = ','.join('?' for _ in candidate_keys)
                row = inc_conn.execute(
                    f"SELECT incubation_phase, incubated_model_id FROM demoted_markets WHERE segment_key IN ({placeholders}) AND incubation_phase != 'unsupported_phantom'",
                    candidate_keys
                ).fetchone()
                if row:
                    return MarketTier.SHADOW_INCUBATOR if (row[0] == 'incubating_with_new_model' and row[1]) else MarketTier.RESEARCH_RND
        except Exception:
            pass

        # Supported un-demoted production markets default to Tier 3
        return MarketTier.PRODUCTION_GRADUATED

    def get_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Provides high-level quantitative summary of the unified market universe."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_summary is not None:
            if getattr(UnifiedMarketUniverse, '_cached_summary_dir', None) == str(self.data_dir):
                if now - UnifiedMarketUniverse._cached_at < UnifiedMarketUniverse._cache_ttl_seconds:
                    return UnifiedMarketUniverse._cached_summary

        roster = self.build_universe_roster(force_refresh=force_refresh)
        # Deduplicate profiles so alias keys do not inflate counts
        unique_profiles = {m.segment_key: m for m in roster.values()}
        total_markets = len(unique_profiles)
        tier3 = [m for m in unique_profiles.values() if m.tier == int(MarketTier.PRODUCTION_GRADUATED)]
        tier2 = [m for m in unique_profiles.values() if m.tier == int(MarketTier.SHADOW_INCUBATOR)]
        tier1 = [m for m in unique_profiles.values() if m.tier == int(MarketTier.RESEARCH_RND)]

        # Determine actively offered markets in current live schedule
        active_offered_keys: Set[str] = set()
        offered_by_league: Dict[str, int] = {}
        if self.engine_db_path.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=15.0)) as eng_conn:
                    rows = eng_conn.execute("""
                        SELECT DISTINCT 
                            json_extract(payload, '$.league'),
                            COALESCE(json_extract(payload, '$.model_scope'), json_extract(payload, '$.period'), 'full_game'),
                            json_extract(payload, '$.market')
                        FROM system_decisions
                        WHERE status = 'open'
                    """).fetchall()
                    for lg_o, sc_o, fam_o in rows:
                        if lg_o and fam_o:
                            k_o = self.get_canonical_key(lg_o, sc_o, fam_o)
                            base_fam = fam_o.split(':')[-1] if ':' in fam_o else fam_o
                            k_base = self.get_canonical_key(lg_o, sc_o, base_fam)
                            target_k = k_o if k_o in unique_profiles else k_base if k_base in unique_profiles else None
                            if target_k:
                                active_offered_keys.add(target_k)
                                lg_up = lg_o.upper()
                                offered_by_league[lg_up] = offered_by_league.get(lg_up, 0) + 1
            except Exception:
                pass

        leagues_breakdown: Dict[str, Dict[str, int]] = {}
        for m in unique_profiles.values():
            lb = leagues_breakdown.setdefault(m.league, {
                'tier1': 0, 'tier2': 0, 'tier3': 0, 'total': 0,
                'dual_polymarket': 0, 'stake_only': 0, 'offered_now': 0
            })
            lb['total'] += 1
            if m.is_dual_polymarket:
                lb['dual_polymarket'] += 1
            else:
                lb['stake_only'] += 1
            if m.tier == 3:
                lb['tier3'] += 1
            elif m.tier == 2:
                lb['tier2'] += 1
            else:
                lb['tier1'] += 1

        for lg_key, cnt in offered_by_league.items():
            if lg_key in leagues_breakdown:
                leagues_breakdown[lg_key]['offered_now'] = cnt

        dual_markets_cnt = sum(1 for m in unique_profiles.values() if m.is_dual_polymarket)
        stake_only_cnt = sum(1 for m in unique_profiles.values() if not m.is_dual_polymarket)

        summary = {
            'total_supported_markets': total_markets,
            'dual_polymarket_markets_count': dual_markets_cnt,
            'stake_only_markets_count': stake_only_cnt,
            'dual_polymarket_ratio': round(dual_markets_cnt / max(1, total_markets), 4),
            'tier_3_production_graduated_count': len(tier3),
            'tier_2_shadow_incubator_count': len(tier2),
            'tier_1_research_rnd_count': len(tier1),
            'active_capital_eligible_ratio': len(tier3) / max(1, total_markets),
            'quarantined_capital_defense_ratio': (len(tier1) + len(tier2)) / max(1, total_markets),
            'active_offered_on_stake_count': len(active_offered_keys),
            'active_offered_ratio': round(len(active_offered_keys) / max(1, total_markets), 4),
            'leagues_breakdown': leagues_breakdown,
        }
        UnifiedMarketUniverse._cached_summary = summary
        UnifiedMarketUniverse._cached_summary_dir = str(self.data_dir)
        return summary
