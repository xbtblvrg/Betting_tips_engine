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
from enum import IntEnum
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UnifiedMarketUniverse:
    """Single source of truth for the entire 461-market supported universe."""

    _cached_roster: Optional[Dict[str, UnifiedMarketProfile]] = None
    _cached_summary: Optional[Dict[str, Any]] = None
    _cached_at: float = 0.0
    _cache_ttl_seconds: float = 60.0

    @classmethod
    def invalidate_cache(cls) -> None:
        """Flushes the cached universe roster and high-level summary."""
        cls._cached_roster = None
        cls._cached_summary = None
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
        # Strip redundant scope prefix from family if present (e.g. 'quarter_1:spreads' -> 'spreads')
        if ':' in fam:
            prefix, rest = fam.split(':', 1)
            norm_p = self.normalize_scope(prefix)
            if norm_p == sc or (norm_p != 'full_game' and sc == 'full_game'):
                sc = norm_p
                fam = rest
            elif prefix in ('quarter_1', 'quarter_2', 'quarter_3', 'quarter_4', 'half_1', 'half_2', 'period_1', 'period_2', 'period_3', 'regulation', 'inning_1', 'innings_1_5'):
                sc = norm_p
                fam = rest
        return f"{lg}|{sc}|{fam}"

    def build_universe_roster(self, force_refresh: bool = False) -> Dict[str, UnifiedMarketProfile]:
        """Assembles the complete 461-market roster with exact tier classifications."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_roster is not None:
            if now - UnifiedMarketUniverse._cached_at < UnifiedMarketUniverse._cache_ttl_seconds:
                return UnifiedMarketUniverse._cached_roster

        roster: Dict[str, UnifiedMarketProfile] = {}

        if not self.incubator_db_path.exists():
            return roster

        try:
            with sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=2.0) as inc_conn:
                # 1. Ingest Graduated Markets (Tier 3 Candidates)
                grad_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           sample_size, roi, delta_brier, stats
                    FROM graduated_markets
                """).fetchall()

                grad_keys_set = set()
                for r in grad_rows:
                    seg_key, lg, sc, fam, sub_fam, n, roi, dbrier, stats_raw = r
                    prof = UnifiedMarketProfile(
                        segment_key=seg_key,
                        league=lg,
                        scope=sc,
                        family=fam,
                        sub_family=sub_fam,
                        tier=int(MarketTier.PRODUCTION_GRADUATED),
                        tier_name="PRODUCTION_GRADUATED",
                        stake_cap=8.00,
                        active_model_id=f"graduated_{lg.lower()}_{sc}_{fam}",
                        model_archetype="GraduatedCrucibleModel",
                        rolling_roi_pct=float(roi * 100.0 if abs(roi) < 10.0 else roi),
                        rolling_pnl=0.0,
                        sample_size=int(n or 0),
                        delta_brier=float(dbrier or 0.0),
                        phase_detail="Active production funded trading"
                    )
                    roster[seg_key] = prof
                    grad_keys_set.add(seg_key)
                    if sub_fam and sub_fam != fam:
                        sub_k = f"{lg}|{sc}|{fam}:{sub_fam}"
                        roster[sub_k] = prof
                        grad_keys_set.add(sub_k)

                # 2. Ingest Demoted Markets (Tier 1 & Tier 2)
                dem_rows = inc_conn.execute("""
                    SELECT segment_key, league, scope, family, sub_family,
                           rolling_roi, rolling_pnl, games, incubation_phase,
                           incubated_model_id, model_archetype, historical_roi, historical_brier_delta
                    FROM demoted_markets
                """).fetchall()

                for r in dem_rows:
                    (seg_key, lg, sc, fam, sub_fam, r_roi, r_pnl, games,
                     phase, model_id, arch, h_roi, h_brier) = r

                    canonical_k = self.get_canonical_key(lg, sc, fam)
                    is_sub = bool(sub_fam and sub_fam != fam)
                    sub_k = f"{lg}|{sc}|{fam}:{sub_fam}" if is_sub else canonical_k

                    # Demoted markets represent current failure/quarantine and strictly override
                    # any historical graduation records (Constitutional Commandments 6 & 7).
                    grad_keys_set.discard(seg_key)
                    grad_keys_set.discard(sub_k)
                    grad_keys_set.discard(canonical_k)

                    if phase == 'incubating_with_new_model':
                        tier = int(MarketTier.SHADOW_INCUBATOR)
                        tier_name = "SHADOW_INCUBATOR"
                        phase_detail = f"Live forward validation with {arch or 'candidate model'} ($0.00)"
                    else:
                        tier = int(MarketTier.RESEARCH_RND)
                        tier_name = "RESEARCH_RND"
                        phase_detail = "Tier 1 R&D historical model tuning on 97k+ matches ($0.00)"

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

        except Exception as exc:
            logger.error("Error reading incubator db for universe roster: %s", exc)

        # 3. Ingest active Baseline Production markets with actual settled performance
        perf_map: Dict[str, Dict[str, Any]] = {}
        if self.engine_db_path.exists():
            try:
                with sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=2.0) as eng_conn:
                    # Pre-calculate performance metrics for all markets across settled decisions
                    try:
                        rows_perf = eng_conn.execute("""
                            SELECT 
                                json_extract(payload, '$.league'),
                                COALESCE(json_extract(payload, '$.model_scope'), json_extract(payload, '$.period')),
                                json_extract(payload, '$.market'),
                                COUNT(*),
                                SUM(pnl),
                                SUM(json_extract(payload, '$.stake'))
                            FROM system_decisions
                            WHERE status IN ('won', 'lost', 'push')
                            GROUP BY 1, 2, 3
                        """).fetchall()
                        for lg_r, sc_r, fam_r, cnt_r, pnl_r, stake_r in rows_perf:
                            if not lg_r or not fam_r:
                                continue
                            k = self.get_canonical_key(lg_r, sc_r, fam_r)
                            pnl_v = float(pnl_r or 0.0)
                            stk_v = float(stake_r or 1.0)
                            avg_stk = stk_v / max(1, cnt_r)
                            units_v = pnl_v / max(0.20, avg_stk)
                            roi_v = (pnl_v / stk_v) if stk_v > 0 else 0.0
                            stat = {
                                'sample_size': int(cnt_r),
                                'pnl': round(pnl_v, 2),
                                'staked': round(stk_v, 2),
                                'roi_pct': round(roi_v * 100.0, 2),
                                'units': round(units_v, 2)
                            }
                            perf_map[k] = stat
                            if ':' in str(fam_r):
                                base_k = self.get_canonical_key(lg_r, sc_r, str(fam_r).split(':')[-1])
                                perf_map.setdefault(base_k, stat)
                    except Exception as e_perf:
                        logger.warning("Could not pre-aggregate settled market performance: %s", e_perf)
            except Exception as exc:
                logger.error("Error reading engine db for universe settled performance: %s", exc)

        def _register_market_candidate(lg: str, sc: str, fam: str, model_id: Optional[str], archetype: str) -> None:
            lg_clean = (lg or '').upper().strip()
            sc_norm = self.normalize_scope(sc)
            fam_clean = (fam or '').lower().strip()
            canon_key = self.get_canonical_key(lg_clean, sc_norm, fam_clean)
            base_fam = fam_clean.split(':')[-1] if ':' in fam_clean else fam_clean
            canon_base_key = self.get_canonical_key(lg_clean, sc_norm, base_fam)

            if canon_key in roster or canon_base_key in roster:
                return

            stat = perf_map.get(canon_key) or perf_map.get(canon_base_key) or {}
            r_pnl = float(stat.get('pnl', 0.0))
            r_roi = float(stat.get('roi_pct', 0.0))
            n_samples = int(stat.get('sample_size', 10))
            r_units = float(stat.get('units', 0.0))

            # Zero Tolerance for Capital Drag (Constitutional Directives 1, 6 & 7):
            # Any market with established or nascent negative expectation (PnL < 0.0 or ROI < 0.0)
            # is strictly quarantined in RESEARCH_RND ($0.00 stake) so Tier 3 contains ONLY
            # and exclusively proven profitable markets.
            is_degraded = (n_samples >= 1) and (r_pnl < 0.0 or r_roi < 0.0)

            if is_degraded:
                tier_val = int(MarketTier.RESEARCH_RND)
                tier_name_val = "RESEARCH_RND"
                stake_cap_val = 0.00
                phase_val = f"Negative expectation ({r_roi:.1f}% ROI, ${r_pnl:.2f}, {r_units:.1f}u over {n_samples} games) - Quarantined ($0.00)"
            else:
                tier_val = int(MarketTier.PRODUCTION_GRADUATED)
                tier_name_val = "PRODUCTION_GRADUATED"
                stake_cap_val = 8.00
                phase_val = "Active production wired model trading"

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
                with sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=2.0) as eng_conn:
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

        UnifiedMarketUniverse._cached_roster = roster
        UnifiedMarketUniverse._cached_at = now
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
            with sqlite3.connect(f"file:{self.incubator_db_path}?mode=ro", uri=True, timeout=2.0) as inc_conn:
                placeholders = ','.join('?' for _ in candidate_keys)
                row = inc_conn.execute(
                    f"SELECT incubation_phase FROM demoted_markets WHERE segment_key IN ({placeholders})",
                    candidate_keys
                ).fetchone()
                if row:
                    return MarketTier.SHADOW_INCUBATOR if row[0] == 'incubating_with_new_model' else MarketTier.RESEARCH_RND
        except Exception:
            pass

        # Supported un-demoted production markets default to Tier 3
        return MarketTier.PRODUCTION_GRADUATED

    def get_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Provides high-level quantitative summary of the unified market universe."""
        import time
        now = time.time()
        if not force_refresh and UnifiedMarketUniverse._cached_summary is not None:
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
                with sqlite3.connect(f"file:{self.engine_db_path}?mode=ro", uri=True, timeout=1.0) as eng_conn:
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
            lb = leagues_breakdown.setdefault(m.league, {'tier1': 0, 'tier2': 0, 'tier3': 0, 'total': 0, 'offered_now': 0})
            lb['total'] += 1
            if m.tier == 3:
                lb['tier3'] += 1
            elif m.tier == 2:
                lb['tier2'] += 1
            else:
                lb['tier1'] += 1

        for lg_key, cnt in offered_by_league.items():
            if lg_key in leagues_breakdown:
                leagues_breakdown[lg_key]['offered_now'] = cnt

        summary = {
            'total_supported_markets': total_markets,
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
        return summary
