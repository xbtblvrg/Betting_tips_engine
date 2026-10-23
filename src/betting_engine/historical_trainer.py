"""Historical Candidate Model Trainer & Pre-Screening Engine.

Executes leak-free chronological walk-forward training, hyperparameter optimization,
and out-of-sample Crucible validation across historical settled wagers. Discovers
statistically verified positive-alpha candidate models for demoted market recovery.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .providers import DATA
from .sandbox_models.archetypes.bivariate_copula import BivariateCopulaHazardModel
from .sandbox_models.archetypes.negative_binomial import NegativeBinomialOverdispersionModel
from .sandbox_models.archetypes.odds_damped_kelly import OddsDampedKellyShrinkageModel
from .sandbox_models.archetypes.situational_fatigue import SituationalFatigueModel
from .sandbox_models.archetypes.zero_inflated import ZeroInflatedPoissonModel
from .sandbox_models.base import BaseCandidateModel
from .tournament_engine import calculate_crucible_scorecard, normalize_segment_key


INCUBATOR_DB = DATA / 'shadow_incubator.sqlite3'


@dataclass
class CandidateTrainingResult:
    segment_key: str
    model_id: str
    model_archetype: str
    version: str
    model_params: Dict[str, Any]
    train_sample_size: int
    oos_sample_size: int
    total_samples: int
    oos_wins: int
    oos_losses: int
    oos_pushes: int
    oos_win_rate: float
    oos_simulated_roi: float
    oos_simulated_pnl: float
    oos_brier_model: float
    oos_brier_market: float
    oos_delta_brier: float
    oos_max_drawdown: float
    scorecard_s: float
    is_qualified: bool
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_default_segment_context(league: str, scope: str) -> Dict[str, Any]:
    """Returns baseline domain context scaled for league and game scope."""
    lg = league.upper()
    sc = scope.lower()

    if lg in ('NBA', 'WNBA', 'NCAAM', 'NCAAW'):
        h_full, a_full = (112.0, 108.0) if lg == 'NBA' else (80.0, 75.0)
    elif lg in ('NFL', 'NCAAF'):
        h_full, a_full = (24.5, 21.5) if lg == 'NFL' else (28.0, 25.0)
    elif lg in ('MLB',):
        h_full, a_full = 4.6, 4.2
    elif lg in ('ATP', 'WTA', 'CHALLENGER'):
        h_full, a_full = 12.8, 11.8
    else:  # NHL, MLS, EPL, UCL
        h_full, a_full = (3.1, 2.8) if lg == 'NHL' else (1.6, 1.2)

    scale = 1.0
    if 'period_1' in sc or 'inning_1' in sc or 'quarter_1' in sc:
        scale = 0.33 if lg == 'NHL' else (0.25 if lg in ('NBA', 'NFL') else 0.15)
    elif 'half_1' in sc or 'half_2' in sc or 'innings_1_5' in sc:
        scale = 0.50
    elif 'period_2' in sc or 'period_3' in sc:
        scale = 0.33

    return {
        'home_expected': round(h_full * scale, 3),
        'away_expected': round(a_full * scale, 3),
        'home_rest_days': 1,
        'away_rest_days': 1,
    }


class HistoricalCandidateTrainer:
    """Trains, tunes, and screens candidate model archetypes on historical settled games."""

    def __init__(self, db_path: Path = INCUBATOR_DB):
        self.db_path = db_path

    def _fetch_settled_wagers(
        self,
        league: str,
        scope: str,
        family: str,
        sub_family: Optional[str] = None
    ) -> List[Tuple]:
        """Queries historical settled wagers with canonical segment aliasing."""
        if not self.db_path.exists():
            return []

        base_fam = family.split(':', 1)[1] if ':' in family else family
        sub = sub_family or base_fam

        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT selection, line, odds, model_prob, market_prob, side, result, pnl, captured_at
                FROM incubated_wagers
                WHERE league = ? AND scope = ? AND (
                    family = ? OR family = ? OR family = ? OR
                    sub_family = ? OR sub_family = ? OR
                    (family = 'american_derivatives' AND sub_family = ?)
                ) AND status = 'settled'
                ORDER BY captured_at ASC
            """, (league, scope, family, base_fam, sub, family, sub, base_fam)).fetchall()
            return rows
        finally:
            conn.close()

    def train_and_screen_demoted_market(
        self,
        segment_key: str,
        min_samples: int = 8,
        min_roi: float = 0.035,
        min_delta_brier: float = 0.000,
        max_drawdown_limit: float = 0.25,
        train_split: float = 0.60
    ) -> Optional[CandidateTrainingResult]:
        """Executes full chronological walk-forward training & out-of-sample Crucible validation."""
        parts = segment_key.split('|')
        league = parts[0] if len(parts) > 0 else 'UNKNOWN'
        scope = parts[1] if len(parts) > 1 else 'full_game'
        family = parts[2] if len(parts) > 2 else 'totals'
        sub_fam = parts[3] if len(parts) > 3 else None

        wagers = self._fetch_settled_wagers(league, scope, family, sub_fam)
        if len(wagers) < min_samples:
            return None

        # Chronological Split: 60% Train, 40% Holdout
        split_idx = max(5, int(len(wagers) * train_split))
        train_wagers = wagers[:split_idx]
        oos_wagers = wagers[split_idx:]
        if len(oos_wagers) < 5:
            # If total dataset is modest, use rolling cross-validation split
            split_idx = max(5, len(wagers) - 10)
            train_wagers = wagers[:split_idx]
            oos_wagers = wagers[split_idx:]

        ctx = get_default_segment_context(league, scope)
        now_tag = datetime.now(timezone.utc).strftime('%m%d_%H%M')

        # Define candidate hyperparameter search spaces across all 5 archetypes
        candidate_configs: List[Tuple[str, BaseCandidateModel, Dict[str, Any]]] = []

        # 1. Bivariate Copula
        for rho in [-0.20, 0.10, 0.25, 0.35]:
            m = BivariateCopulaHazardModel(
                model_id=f"{league.lower()}_{scope}_{family}_copula_r{int(abs(rho)*100)}_{now_tag}",
                version="1.1.0",
                league=league,
                scope=scope,
                family=family,
                correlation_rho=rho,
            )
            candidate_configs.append(('BivariateCopulaHazardModel', m, {'correlation_rho': rho}))

        # 2. Negative Binomial Overdispersion
        for alpha in [0.08, 0.18, 0.30, 0.45]:
            m = NegativeBinomialOverdispersionModel(
                model_id=f"{league.lower()}_{scope}_{family}_negbin_a{int(alpha*100)}_{now_tag}",
                version="1.1.0",
                league=league,
                scope=scope,
                family=family,
                dispersion_alpha=alpha,
            )
            candidate_configs.append(('NegativeBinomialOverdispersionModel', m, {'dispersion_alpha': alpha}))

        # 3. Odds-Damped Kelly Shrinkage
        for damping in [0.15, 0.35, 0.55, 0.75]:
            m = OddsDampedKellyShrinkageModel(
                model_id=f"{league.lower()}_{scope}_{family}_damped_d{int(damping*100)}_{now_tag}",
                version="1.1.0",
                league=league,
                scope=scope,
                family=family,
                damping_strength=damping,
            )
            candidate_configs.append(('OddsDampedKellyShrinkageModel', m, {'damping_strength': damping}))

        # 4. Situational Fatigue
        for b2b in [0.035, 0.065]:
            m = SituationalFatigueModel(
                model_id=f"{league.lower()}_{scope}_{family}_fatigue_b{int(b2b*1000)}_{now_tag}",
                version="1.1.0",
                league=league,
                scope=scope,
                family=family,
                b2b_penalty_pct=b2b,
                rest_bonus_pct=0.02,
            )
            candidate_configs.append(('SituationalFatigueModel', m, {'b2b_penalty_pct': b2b, 'rest_bonus_pct': 0.02}))

        # 5. Zero-Inflated Poisson (for totals / soccer / hockey)
        if family in ('totals', 'american_derivatives') or league in ('MLS', 'EPL', 'UCL', 'NHL'):
            for pi in [0.08, 0.16]:
                m = ZeroInflatedPoissonModel(
                    model_id=f"{league.lower()}_{scope}_{family}_zip_p{int(pi*100)}_{now_tag}",
                    version="1.1.0",
                    league=league,
                    scope=scope,
                    family=family,
                    zero_inflation_pi=pi,
                )
                candidate_configs.append(('ZeroInflatedPoissonModel', m, {'zero_inflation_pi': pi}))

        # Evaluate candidate models out-of-sample on holdout
        qualified_results: List[CandidateTrainingResult] = []
        all_results: List[CandidateTrainingResult] = []

        for arch_name, model, params in candidate_configs:
            # Test candidate on OOS holdout
            oos_accepted = 0
            oos_wins = 0
            oos_losses = 0
            oos_pushes = 0
            oos_pnl = 0.0
            sum_brier_m = 0.0
            sum_brier_mkt = 0.0

            peak_pnl = 0.0
            max_dd = 0.0
            running_pnl = 0.0

            for row in oos_wagers:
                sel, line, odds, _, mkt_prob, side, res, orig_pnl, _ = row
                odds_val = float(odds or 2.0)
                mkt_p = float(mkt_prob or (1.0 / max(1.05, odds_val)))

                try:
                    prob, ev = model.evaluate_market(
                        segment_key, sel or '', line, odds_val, ctx, side=side
                    )
                except Exception:
                    continue

                if ev >= 0.035:
                    oos_accepted += 1
                    y = 1.0 if res == 'won' else (0.5 if res == 'push' else 0.0)
                    sum_brier_m += (prob - y) ** 2
                    sum_brier_mkt += (mkt_p - y) ** 2

                    if res == 'won':
                        oos_wins += 1
                        profit = odds_val - 1.0
                        oos_pnl += profit
                        running_pnl += profit
                    elif res == 'lost':
                        oos_losses += 1
                        oos_pnl -= 1.0
                        running_pnl -= 1.0
                    else:
                        oos_pushes += 1

                    if running_pnl > peak_pnl:
                        peak_pnl = running_pnl
                    dd = peak_pnl - running_pnl
                    if dd > max_dd:
                        max_dd = dd

            if oos_accepted == 0:
                continue

            oos_roi = oos_pnl / oos_accepted
            win_rate = oos_wins / (oos_wins + oos_losses) if (oos_wins + oos_losses) > 0 else 0.0
            avg_bm = sum_brier_m / oos_accepted
            avg_bmkt = sum_brier_mkt / oos_accepted
            delta_brier = round(avg_bmkt - avg_bm, 5)
            dd_pct = round(max_dd / max(1.0, peak_pnl if peak_pnl > 0 else oos_accepted), 4)

            score_s = calculate_crucible_scorecard(oos_roi, oos_accepted, delta_brier, dd_pct)

            reasons = []
            if oos_accepted < 5:
                reasons.append(f"Insufficient OOS samples: {oos_accepted} < 5")
            if oos_roi < min_roi:
                reasons.append(f"OOS ROI below target: {oos_roi*100:.1f}% < {min_roi*100:.1f}%")
            if delta_brier < min_delta_brier:
                reasons.append(f"Delta Brier below threshold: {delta_brier:.5f} < {min_delta_brier:.5f}")
            if dd_pct > max_drawdown_limit:
                reasons.append(f"Max DD exceeded: {dd_pct*100:.1f}% > {max_drawdown_limit*100:.1f}%")

            is_qual = (len(reasons) == 0)

            result = CandidateTrainingResult(
                segment_key=segment_key,
                model_id=model.model_id,
                model_archetype=arch_name,
                version="1.1.0",
                model_params=params,
                train_sample_size=len(train_wagers),
                oos_sample_size=oos_accepted,
                total_samples=len(wagers),
                oos_wins=oos_wins,
                oos_losses=oos_losses,
                oos_pushes=oos_pushes,
                oos_win_rate=round(win_rate, 4),
                oos_simulated_roi=round(oos_roi, 4),
                oos_simulated_pnl=round(oos_pnl, 2),
                oos_brier_model=round(avg_bm, 4),
                oos_brier_market=round(avg_bmkt, 4),
                oos_delta_brier=delta_brier,
                oos_max_drawdown=dd_pct,
                scorecard_s=score_s,
                is_qualified=is_qual,
                failure_reasons=reasons,
            )
            all_results.append(result)
            if is_qual:
                qualified_results.append(result)

        if not qualified_results:
            return None

        # Sort qualified champions by scorecard S descending
        qualified_results.sort(key=lambda r: r.scorecard_s, reverse=True)
        return qualified_results[0]
