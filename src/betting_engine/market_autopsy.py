"""Automated Quantitative Market Autopsy and Hyper-Targeted Synthesis Brief Generator.

Analyzes the exact historical settled performance and empirical moment distributions of
a failing/demoted market segment. Formulates a bespoke mathematical diagnosis and
quantitative brief for frontier AI models (Claude, Codex, Grok, Agy) to synthesize
calibrated, positive-alpha models.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .historical_trainer import get_default_segment_context
from .providers import DATA

DEFAULT_INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"


def _pearson_corr(xs: List[float], ys: List[float]) -> float:
    """Computes sample Pearson correlation coefficient without external dependency overhead."""
    n = len(xs)
    if n < 4 or len(ys) != n:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs)
    den_y = sum((y - my) ** 2 for y in ys)
    if den_x <= 1e-12 or den_y <= 1e-12:
        return 0.0
    return round(max(-1.0, min(1.0, num / math.sqrt(den_x * den_y))), 4)


@dataclass
class MarketAutopsy:
    segment_key: str
    league: str
    scope: str
    family: str
    sample_size: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    realized_roi: float = 0.0
    pnl_by_side: Dict[str, float] = field(default_factory=dict)
    worst_side: Optional[str] = None
    home_expected: float = 0.0
    away_expected: float = 0.0
    dispersion_index: float = 1.0  # Var / Mean; > 1.25 indicates heavy overdispersion
    zero_frequency: float = 0.0
    skewness: float = 0.0
    mean_calibration_residual: float = 0.0  # E[y - p_model]: negative = overconfident, positive = underconfident
    mean_brier_delta: float = 0.0  # E[Brier_market - Brier_model]: negative = model lags market calibration
    residual_correlations: Dict[str, float] = field(default_factory=dict)
    odds_regime_autopsy: Dict[str, Dict[str, float]] = field(default_factory=dict)
    line_regime_autopsy: Dict[str, Dict[str, float]] = field(default_factory=dict)
    side_autopsy: Dict[str, Dict[str, float]] = field(default_factory=dict)
    scientific_hypotheses: List[str] = field(default_factory=list)
    recommended_distributions: List[str] = field(default_factory=list)
    pathology_summary: str = "Standard historical evaluation"
    analyzed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketAutopsyAnalyzer:
    """Extracts ground-truth moments, residual correlations, and financial pathology for any market segment."""

    def __init__(self, db_path: Path | str = DEFAULT_INCUBATOR_DB):
        p = Path(db_path) if isinstance(db_path, str) else db_path
        if p.is_dir():
            p = p / "shadow_incubator.sqlite3"
        self.db_path = p

    def analyze_market(self, segment_key: str, max_records: int = 500) -> MarketAutopsy:
        """Runs empirical forensic scan and residual feature attribution on settled wagers."""
        parts = segment_key.split('|')
        league = parts[0] if len(parts) > 0 else 'UNKNOWN'
        scope = parts[1] if len(parts) > 1 else 'full_game'
        family = parts[2] if len(parts) > 2 else 'moneyline'
        base_family = family.split(':')[-1] if ':' in family else family

        base_ctx = get_default_segment_context(league, scope)
        h_exp = float(base_ctx.get('home_expected') or 1.5)
        a_exp = float(base_ctx.get('away_expected') or 1.2)

        autopsy = MarketAutopsy(
            segment_key=segment_key,
            league=league,
            scope=scope,
            family=family,
            home_expected=h_exp,
            away_expected=a_exp,
        )

        wagers: List[Tuple] = []
        if self.db_path.exists():
            try:
                with closing(sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=15.0)) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT selection, line, odds, model_prob, market_prob, side, result, pnl
                        FROM incubated_wagers
                        WHERE league = ? AND scope = ? AND (family IN (?, ?) OR sub_family IN (?, ?)) AND status = 'settled'
                        ORDER BY rowid DESC
                        LIMIT ?
                    """, (league, scope, family, base_family, family, base_family, max_records))
                    wagers.extend(cur.fetchall())
            except Exception:
                pass

        # Enrich with live settled wagers from engine.sqlite3 if present
        engine_db = self.db_path.parent / "engine.sqlite3"
        if engine_db.exists():
            try:
                with closing(sqlite3.connect(f"file:{engine_db.resolve()}?mode=ro", uri=True, timeout=5.0)) as e_conn:
                    rows_live = e_conn.execute("""
                        SELECT payload, status, pnl
                        FROM system_decisions
                        WHERE status IN ('won', 'lost', 'push')
                          AND json_extract(payload, '$.league') = ?
                        ORDER BY issued_at DESC
                        LIMIT 200
                    """, (league,)).fetchall()
                    for p_raw, st_l, pnl_l in rows_live:
                        p_obj = json.loads(p_raw) if isinstance(p_raw, str) else (p_raw or {})
                        p_sc = str(p_obj.get('model_scope') or p_obj.get('scope') or 'full_game')
                        p_mkt = str(p_obj.get('market') or '')
                        p_base = p_mkt.split(':')[-1] if ':' in p_mkt else p_mkt
                        if p_sc == scope and (p_mkt in (family, base_family) or p_base in (family, base_family)):
                            od_l = float(p_obj.get('reference_odds') or p_obj.get('odds') or 2.0)
                            mp_l = float(p_obj.get('probability') or 0.5)
                            stk_l = max(0.5, float(p_obj.get('stake') or 1.0))
                            norm_pnl = float(pnl_l or 0.0) / stk_l
                            mkt_p_l = float((p_obj.get('market_anchor') or {}).get('market_probability') or (1.0 / max(1.05, od_l)))
                            wagers.append((
                                p_obj.get('selection'),
                                p_obj.get('line'),
                                od_l,
                                mp_l,
                                mkt_p_l,
                                p_obj.get('side'),
                                st_l,
                                norm_pnl,
                            ))
            except Exception:
                pass

        self._fill_heuristics(autopsy, wagers, h_exp, a_exp)
        return autopsy

    def _fill_heuristics(
        self,
        autopsy: MarketAutopsy,
        wagers: List[Tuple],
        h_exp: float,
        a_exp: float
    ) -> None:
        """Computes empirical moments, residual correlations, regime breakdowns, and scientific hypotheses."""
        n = len(wagers)
        autopsy.sample_size = n

        if n > 0:
            wins = sum(1 for r in wagers if r[6] == 'won')
            losses = sum(1 for r in wagers if r[6] == 'lost')
            tot_pnl = sum(float(r[7] or 0.0) for r in wagers if r[7] is not None)

            autopsy.win_rate = round(wins / max(1, wins + losses), 4)
            autopsy.total_pnl = round(tot_pnl, 2)
            autopsy.realized_roi = round(tot_pnl / max(1, n), 4)

            sides: Dict[str, float] = {}
            side_buckets: Dict[str, List[Tuple[float, float, float]]] = {}  # side -> [(pnl, residual, brier_delta)]
            lines: List[float] = []
            line_residuals: List[Tuple[float, float, float]] = []  # (line, residual, pnl)
            odds_list: List[float] = []
            residuals_list: List[float] = []
            disagreements_list: List[float] = []
            brier_deltas: List[float] = []

            odds_buckets: Dict[str, List[Tuple[float, float, float]]] = {
                'favorite_under_1.75': [],
                'balanced_1.75_to_2.35': [],
                'underdog_over_2.35': [],
            }

            for r in wagers:
                s = str(r[5] or 'unknown').lower()
                pnl = float(r[7] or 0.0)
                sides[s] = round(sides.get(s, 0.0) + pnl, 2)

                res_str = str(r[6] or '').lower()
                if res_str in ('won', 'lost'):
                    y = 1.0 if res_str == 'won' else 0.0
                    try:
                        od = max(1.01, float(r[2] or 2.0))
                    except (ValueError, TypeError):
                        od = 2.0
                    try:
                        p_mod = max(0.01, min(0.99, float(r[3]) if r[3] is not None else (1.0 / od)))
                    except (ValueError, TypeError):
                        p_mod = 1.0 / od
                    try:
                        p_mkt = max(0.01, min(0.99, float(r[4]) if r[4] is not None else (1.0 / od)))
                    except (ValueError, TypeError):
                        p_mkt = 1.0 / od

                    resid = y - p_mod
                    disagree = p_mod - p_mkt
                    b_delta = ((p_mkt - y) ** 2) - ((p_mod - y) ** 2)

                    odds_list.append(od)
                    residuals_list.append(resid)
                    disagreements_list.append(disagree)
                    brier_deltas.append(b_delta)
                    side_buckets.setdefault(s, []).append((pnl, resid, b_delta))

                    if od < 1.75:
                        odds_buckets['favorite_under_1.75'].append((pnl, resid, b_delta))
                    elif od < 2.35:
                        odds_buckets['balanced_1.75_to_2.35'].append((pnl, resid, b_delta))
                    else:
                        odds_buckets['underdog_over_2.35'].append((pnl, resid, b_delta))

                    if r[1] is not None:
                        try:
                            lv = float(r[1])
                            lines.append(lv)
                            line_residuals.append((lv, resid, pnl))
                        except (ValueError, TypeError):
                            pass

            autopsy.pnl_by_side = sides
            if sides:
                worst_s = min(sides.items(), key=lambda kv: kv[1])
                if worst_s[1] < 0.0:
                    autopsy.worst_side = worst_s[0]

            if residuals_list:
                autopsy.mean_calibration_residual = round(sum(residuals_list) / len(residuals_list), 4)
                autopsy.mean_brier_delta = round(sum(brier_deltas) / len(brier_deltas), 5)
                autopsy.residual_correlations['odds_vs_residual'] = _pearson_corr(odds_list, residuals_list)
                autopsy.residual_correlations['disagreement_vs_residual'] = _pearson_corr(disagreements_list, residuals_list)

            for s_k, s_rows in side_buckets.items():
                if s_rows:
                    autopsy.side_autopsy[s_k] = {
                        'n': len(s_rows),
                        'roi': round(sum(x[0] for x in s_rows) / len(s_rows), 4),
                        'mean_residual': round(sum(x[1] for x in s_rows) / len(s_rows), 4),
                        'brier_delta': round(sum(x[2] for x in s_rows) / len(s_rows), 5),
                    }

            for ob_k, ob_rows in odds_buckets.items():
                if ob_rows:
                    autopsy.odds_regime_autopsy[ob_k] = {
                        'n': len(ob_rows),
                        'roi': round(sum(x[0] for x in ob_rows) / len(ob_rows), 4),
                        'mean_residual': round(sum(x[1] for x in ob_rows) / len(ob_rows), 4),
                        'brier_delta': round(sum(x[2] for x in ob_rows) / len(ob_rows), 5),
                    }

            # Calculate line dispersion, skewness, and line-regime residual attribution
            if lines and len(lines) >= 5:
                mean_l = sum(lines) / len(lines)
                var_l = sum((x - mean_l) ** 2 for x in lines) / max(1, len(lines) - 1)
                std_l = math.sqrt(max(1e-9, var_l))
                autopsy.dispersion_index = round(var_l / max(0.1, abs(mean_l)), 3)
                skew_l = sum(((x - mean_l) / std_l) ** 3 for x in lines) / len(lines) if std_l > 1e-4 else 0.0
                autopsy.skewness = round(max(-3.0, min(3.0, skew_l)), 3)

                l_xs = [x[0] for x in line_residuals]
                l_ys = [x[1] for x in line_residuals]
                autopsy.residual_correlations['line_vs_residual'] = _pearson_corr(l_xs, l_ys)

                sorted_lines = sorted(lines)
                med_line = sorted_lines[len(sorted_lines) // 2]
                low_l_rows = [x for x in line_residuals if x[0] <= med_line]
                high_l_rows = [x for x in line_residuals if x[0] > med_line]
                autopsy.line_regime_autopsy['median_line'] = {'value': round(med_line, 2)}
                if low_l_rows:
                    autopsy.line_regime_autopsy['low_line_regime'] = {
                        'n': len(low_l_rows),
                        'roi': round(sum(x[2] for x in low_l_rows) / len(low_l_rows), 4),
                        'mean_residual': round(sum(x[1] for x in low_l_rows) / len(low_l_rows), 4),
                    }
                if high_l_rows:
                    autopsy.line_regime_autopsy['high_line_regime'] = {
                        'n': len(high_l_rows),
                        'roi': round(sum(x[2] for x in high_l_rows) / len(high_l_rows), 4),
                        'mean_residual': round(sum(x[1] for x in high_l_rows) / len(high_l_rows), 4),
                    }

        # Deduce objective scientific hypotheses from empirical residual correlations & regime moments
        hypotheses: List[str] = []
        if autopsy.mean_calibration_residual <= -0.025:
            hypotheses.append(
                f"Systematic probability overconfidence detected: E[y - p_model] = {autopsy.mean_calibration_residual:+.4f}. "
                f"Predicted win probabilities exceed empirical hit frequency by {abs(autopsy.mean_calibration_residual)*100:.1f}%."
            )
        elif autopsy.mean_calibration_residual >= 0.025:
            hypotheses.append(
                f"Systematic probability underconfidence detected: E[y - p_model] = {autopsy.mean_calibration_residual:+.4f}."
            )

        dis_corr = autopsy.residual_correlations.get('disagreement_vs_residual', 0.0)
        if dis_corr <= -0.10:
            hypotheses.append(
                f"Negative divergence correlation rho(p_model - p_market, y - p_model) = {dis_corr:+.3f}: "
                f"large model deviations from implied probabilities correspond to larger calibration errors (tail hubris)."
            )

        odds_corr = autopsy.residual_correlations.get('odds_vs_residual', 0.0)
        if abs(odds_corr) >= 0.08:
            direction = "high-odds underdog overestimation" if odds_corr < 0 else "low-odds favorite overestimation"
            hypotheses.append(
                f"Odds-gradient calibration slope rho(odds, y - p_model) = {odds_corr:+.3f} ({direction})."
            )

        line_corr = autopsy.residual_correlations.get('line_vs_residual', 0.0)
        if abs(line_corr) >= 0.08:
            hypotheses.append(
                f"Line-magnitude residual dependency rho(line, y - p_model) = {line_corr:+.3f}: "
                f"calibration accuracy drifts systematically across low vs. high contract lines."
            )

        if len(autopsy.side_autopsy) >= 2:
            s_items = sorted(autopsy.side_autopsy.items(), key=lambda kv: kv[1]['mean_residual'])
            worst_sk, worst_sv = s_items[0]
            best_sk, best_sv = s_items[-1]
            if (best_sv['mean_residual'] - worst_sv['mean_residual']) >= 0.04:
                hypotheses.append(
                    f"Directional side asymmetry: '{worst_sk}' exhibits residual bias {worst_sv['mean_residual']:+.4f} "
                    f"(ROI {worst_sv['roi']*100:+.1f}%) vs '{best_sk}' residual bias {best_sv['mean_residual']:+.4f} "
                    f"(ROI {best_sv['roi']*100:+.1f}%)."
                )

        autopsy.scientific_hypotheses = hypotheses

        # Domain statistical property diagnostics
        fam = autopsy.family.lower()
        lg = autopsy.league.upper()
        sc = autopsy.scope.lower()

        recs = []
        pathologies = []

        if 'total' in fam:
            if lg in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') or autopsy.dispersion_index > 1.25:
                recs.extend(['overdispersed_variance', 'compound_scoring'])
                pathologies.append(f"Observed variance-to-mean ratio (Dispersion={autopsy.dispersion_index:.3f}, Skewness={autopsy.skewness:+.3f})")
            elif lg == 'NHL' and ('period_3' in sc or 'regulation' in sc):
                recs.extend(['time_varying_hazard', 'accelerating_rate'])
                pathologies.append("Observed non-stationary scoring rate in final period due to late empty-net situations")
            else:
                recs.extend(['bivariate_dependence', 'low_scoring_marginal'])
                pathologies.append(f"Observed low-scoring joint outcome distribution (Home mean={autopsy.home_expected:.2f}, Away mean={autopsy.away_expected:.2f})")

        elif 'spread' in fam or 'handicap' in fam:
            if lg in ('NFL', 'NCAAF'):
                recs.extend(['key_number_clustering', 'discrete_margin_mass'])
                pathologies.append("Observed score differentials concentrate on discrete scoring increments (3, 7, 10 points)")
            else:
                recs.extend(['differential_scoring', 'modal_concentration'])
                pathologies.append(f"Observed margin dispersion index {autopsy.dispersion_index:.3f} and skewness {autopsy.skewness:+.3f}")

        elif 'three_way' in fam or 'draw' in fam or 'moneyline' in fam:
            if autopsy.worst_side == 'home':
                pathologies.append(f"Observed negative realized return on Home selections (Home PnL: {autopsy.pnl_by_side.get('home', 0.0):.1f})")
            recs.extend(['bivariate_dependence', 'draw_sensitivity'])
            pathologies.append("Observed 3-way/winner outcome calibration sensitivity across home, draw, and away outcomes")

        else:
            recs.extend(['score_lattice', 'tail_variance'])
            pathologies.append(f"Observed empirical dispersion index {autopsy.dispersion_index:.3f}")

        if hypotheses:
            pathologies.extend(hypotheses[:2])

        autopsy.recommended_distributions = recs
        autopsy.pathology_summary = "; ".join(pathologies) if pathologies else "Standard out-of-sample edge deficit"


class TargetedSynthesisBriefBuilder:
    """Constructs a high-conviction, personalized quantitative prompt from empirical residual attribution."""

    @staticmethod
    def build_targeted_prompt(autopsy: MarketAutopsy, base_problem: Optional[str] = None) -> str:
        """Assembles the complete mathematical brief with zero prompt anchoring."""
        if autopsy.sample_size > 0:
            sample_desc = f"{autopsy.sample_size} settled wagers"
            perf_desc = (
                f"Realized Win Rate: {autopsy.win_rate * 100:.1f}% | Historical PnL: ${autopsy.total_pnl:.2f} | "
                f"Realized ROI: {autopsy.realized_roi * 100:.1f}% | Mean Calibration Residual E[y - p]: {autopsy.mean_calibration_residual:+.4f} | "
                f"Mean Brier Skill Delta E[B_mkt - B_mod]: {autopsy.mean_brier_delta:+.5f}"
            )
            side_breakdown = ", ".join(
                f"{k}: PnL=${autopsy.pnl_by_side.get(k, 0.0):.2f} (N={v['n']}, ROI={v['roi']*100:+.1f}%, E[y-p]={v['mean_residual']:+.4f})"
                for k, v in autopsy.side_autopsy.items()
            ) if autopsy.side_autopsy else (
                ", ".join(f"{k}: ${v:.2f}" for k, v in autopsy.pnl_by_side.items()) if autopsy.pnl_by_side else "None"
            )
            disp_desc = f"Observed Dispersion Index (Var/Mean): {autopsy.dispersion_index:.3f} | Empirical Skewness: {autopsy.skewness:+.3f}"
            worst_note = f"- CRITICAL CASH LEAK: Side '{autopsy.worst_side}' accounted for the majority of financial losses.\n" if autopsy.worst_side else ""
            pathology_desc = autopsy.pathology_summary

            corr_parts = [f"{k}={v:+.4f}" for k, v in autopsy.residual_correlations.items()]
            corr_desc = ", ".join(corr_parts) if corr_parts else "Insufficient variance for correlation matrix"

            odds_reg_parts = [
                f"{k}: N={v['n']}, ROI={v['roi']*100:+.1f}%, E[y-p]={v['mean_residual']:+.4f}"
                for k, v in autopsy.odds_regime_autopsy.items()
            ]
            odds_reg_desc = " | ".join(odds_reg_parts) if odds_reg_parts else "Unobserved"

            line_reg_parts = [
                f"{k}: N={v['n']}, ROI={v['roi']*100:+.1f}%, E[y-p]={v['mean_residual']:+.4f}"
                for k, v in autopsy.line_regime_autopsy.items() if 'n' in v
            ]
            line_reg_desc = " | ".join(line_reg_parts) if line_reg_parts else "Not applicable (non-line market)"
            hypo_block = "\n".join(f"  * {h}" for h in autopsy.scientific_hypotheses) if autopsy.scientific_hypotheses else "  * Out-of-sample calibration deficit across settled contracts."
        else:
            sample_desc = "0 wagers (Cold Start market - unobserved historical bets)"
            perf_desc = "Realized Win Rate: N/A (cold start) | Historical PnL: $0.00 | Realized ROI: N/A"
            side_breakdown = "None (unobserved cold start)"
            disp_desc = f"Dispersion Index (Var/Mean): Unobserved prior ({autopsy.dispersion_index:.3f})"
            worst_note = ""
            pathology_desc = "Cold start unmodeled market; construct first-principles quantitative architecture from domain fundamentals."
            corr_desc = "Cold start"
            odds_reg_desc = "Cold start"
            line_reg_desc = "Cold start"
            hypo_block = "  * Cold start market: derive predictive joint score/outcome probabilities directly from situational expectations and sport scoring rules."

        prompt = (
            f"TARGET QUANTITATIVE BETTING SEGMENT: '{autopsy.segment_key}'\n"
            f"- League: {autopsy.league} | Game Scope: {autopsy.scope} | Market Family: {autopsy.family}\n\n"
            f"HISTORICAL FORENSIC AUTOPSY (EMPIRICAL EVIDENCE):\n"
            f"- Settled Historical Sample: {sample_desc}\n"
            f"- {perf_desc}\n"
            f"- Side Residual & PnL Attribution: {side_breakdown}\n"
            f"{worst_note}"
            f"- Empirical Baseline Means: Home expected = {autopsy.home_expected:.2f}, Away expected = {autopsy.away_expected:.2f}\n"
            f"- {disp_desc}\n"
            f"- Residual Correlation Matrix: {corr_desc}\n"
            f"- Odds-Regime Attribution: {odds_reg_desc}\n"
            f"- Line-Regime Attribution: {line_reg_desc}\n"
            f"- Identified Empirical Pathology: {pathology_desc}\n"
            f"- Empirical Residual Findings to Resolve:\n{hypo_block}\n\n"
            f"MATHEMATICAL OBJECTIVE & OPEN QUANTITATIVE FREEDOM:\n"
            f"1. Eliminate the empirical calibration residuals E[y - p] and negative correlations identified above. Zero hardcoded constants or arbitrary static clamps.\n"
            f"2. FIRST-PRINCIPLES MATHEMATICAL DERIVATION: Do NOT anchor to standard recipes, buzzword lists, or historical templates. "
            f"Independently formulate the theoretical model structure directly from empirical moments, residual correlations, and domain dynamics.\n"
            f"3. Anchor distributions around empirical domain reality (Home: {autopsy.home_expected:.2f}, Away: {autopsy.away_expected:.2f}), "
            f"and dynamically scale with situational inputs ('home_expected', 'away_expected', attack/defense exposure, tempo/pace, rest days).\n"
            f"4. Calibrate variance and tail mass from the observed dispersion index ({autopsy.dispersion_index:.3f}), skewness ({autopsy.skewness:+.3f}), and line-regime residuals for {autopsy.league}.\n"
            f"5. Ensure margin mass and directional probabilities neutralize any directional side bias and outperform bookmaker closing Brier skill out-of-sample.\n"
        )
        if base_problem and base_problem.strip():
            prompt += f"\nADDITIONAL USER/SUPERVISOR DIAGNOSIS:\n{base_problem.strip()}\n"

        return prompt
