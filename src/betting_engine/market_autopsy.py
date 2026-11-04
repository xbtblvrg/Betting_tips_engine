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
import math
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .historical_trainer import get_default_segment_context
from .providers import DATA

DEFAULT_INCUBATOR_DB = DATA / "shadow_incubator.sqlite3"


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
    recommended_distributions: List[str] = field(default_factory=list)
    pathology_summary: str = "Standard historical evaluation"
    analyzed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarketAutopsyAnalyzer:
    """Extracts ground-truth moments and financial pathology for any specific market segment."""

    def __init__(self, db_path: Path | str = DEFAULT_INCUBATOR_DB):
        p = Path(db_path) if isinstance(db_path, str) else db_path
        if p.is_dir():
            p = p / "shadow_incubator.sqlite3"
        self.db_path = p

    def analyze_market(self, segment_key: str, max_records: int = 500) -> MarketAutopsy:
        """Runs empirical forensic scan on settled wagers and baseline context."""
        parts = segment_key.split('|')
        league = parts[0] if len(parts) > 0 else 'UNKNOWN'
        scope = parts[1] if len(parts) > 1 else 'full_game'
        family = parts[2] if len(parts) > 2 else 'moneyline'

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

        if not self.db_path.exists():
            self._fill_heuristics(autopsy, [], h_exp, a_exp)
            return autopsy

        wagers = []
        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True, timeout=15.0)
            try:
                cur = conn.cursor()
                # Query historical settled wagers for this segment
                cur.execute("""
                    SELECT selection, line, odds, model_prob, market_prob, side, result, pnl
                    FROM incubated_wagers
                    WHERE league = ? AND scope = ? AND (family = ? OR sub_family = ?) AND status = 'settled'
                    ORDER BY rowid DESC
                    LIMIT ?
                """, (league, scope, family, family, max_records))
                wagers = cur.fetchall()
            finally:
                conn.close()
        except Exception:
            wagers = []

        self._fill_heuristics(autopsy, wagers, h_exp, a_exp)
        return autopsy

    def _fill_heuristics(
        self,
        autopsy: MarketAutopsy,
        wagers: List[Tuple],
        h_exp: float,
        a_exp: float
    ) -> None:
        """Computes moments, financial loss drivers, and recommended distribution families."""
        n = len(wagers)
        autopsy.sample_size = n

        if n > 0:
            wins = sum(1 for r in wagers if r[6] == 'won')
            losses = sum(1 for r in wagers if r[6] == 'lost')
            tot_pnl = sum(r[7] for r in wagers if r[7] is not None)

            autopsy.win_rate = round(wins / max(1, wins + losses), 4)
            autopsy.total_pnl = round(tot_pnl, 2)
            autopsy.realized_roi = round(tot_pnl / max(1, n), 4)

            # Analyze side breakdowns
            sides: Dict[str, float] = {}
            side_counts: Dict[str, int] = {}
            lines: List[float] = []
            for r in wagers:
                s = str(r[5] or 'unknown').lower()
                pnl = float(r[7] or 0.0)
                sides[s] = round(sides.get(s, 0.0) + pnl, 2)
                side_counts[s] = side_counts.get(s, 0) + 1
                if r[1] is not None:
                    try:
                        lines.append(float(r[1]))
                    except (ValueError, TypeError):
                        pass

            autopsy.pnl_by_side = sides
            if sides:
                worst_s = min(sides.items(), key=lambda kv: kv[1])
                if worst_s[1] < 0.0:
                    autopsy.worst_side = worst_s[0]

            # Calculate line dispersion if available
            if lines and len(lines) >= 5:
                mean_l = sum(lines) / len(lines)
                var_l = sum((x - mean_l) ** 2 for x in lines) / max(1, len(lines) - 1)
                autopsy.dispersion_index = round(var_l / max(0.1, mean_l), 3)

        # Domain distribution recommendations
        fam = autopsy.family.lower()
        lg = autopsy.league.upper()
        sc = autopsy.scope.lower()

        recs = []
        pathologies = []

        if 'total' in fam:
            if lg in ('NBA', 'WNBA', 'NCAAM', 'NCAAW') or autopsy.dispersion_index > 1.25:
                recs.extend(['NegativeBinomialHurdle', 'PaceAdjustedGammaPoisson', 'UniversalLattice'])
                pathologies.append(f"Heavy right-tail variance (Dispersion={autopsy.dispersion_index}); candidate may consider overdispersed or mixture dynamics")
            elif lg == 'NHL' and ('period_3' in sc or 'regulation' in sc):
                recs.extend(['WeibullRenewalHazard', 'ZeroInflatedPoisson'])
                pathologies.append("Late empty-net goal surge violates Poisson homogeneity; candidate may consider time-decay or renewal hazards")
            else:
                recs.extend(['DixonColesSoccerModel', 'ZeroInflatedNegativeBinomial'])
                pathologies.append("Low-scoring marginal dependence; candidate may explore bivariate coupling or joint score lattices")

        elif 'spread' in fam or 'handicap' in fam:
            if lg in ('NFL', 'NCAAF'):
                recs.extend(['AsymmetricLaplaceDifference', 'KeyNumberDiscreteLattice'])
                pathologies.append("Score differentials cluster sharply on key numbers (3, 7, 10); candidate should account for discrete key-number mass")
            else:
                recs.extend(['AsymmetricLaplaceDifference', 'SkellamDifferentialModel'])
                pathologies.append("High modal concentration around margin; candidate may consider differential or heavy-tailed formulations")

        elif 'three_way' in fam or 'draw' in fam or 'moneyline' in fam:
            if autopsy.worst_side == 'home':
                pathologies.append(f"Systematic over-estimation of Home advantage (Home PnL: {autopsy.pnl_by_side.get('home', 0.0):.1f}); consider dampening home weighting")
            recs.extend(['SkellamDifferentialModel', 'DixonColesSoccerModel', 'ClaytonCopula'])
            pathologies.append("3-way draw probability sensitivity; candidate may explore bivariate marginal lattices")

        else:
            recs.extend(['UniversalScoreLattice', 'AsymmetricLaplaceDifference'])
            pathologies.append("Tail variance uncaptured by naive models")

        autopsy.recommended_distributions = recs
        autopsy.pathology_summary = "; ".join(pathologies) if pathologies else "Standard out-of-sample edge deficit"


class TargetedSynthesisBriefBuilder:
    """Constructs a high-conviction, personalized quantitative prompt for AI model synthesis."""

    @staticmethod
    def build_targeted_prompt(autopsy: MarketAutopsy, base_problem: Optional[str] = None) -> str:
        """Assembles the complete mathematical brief."""
        if autopsy.sample_size > 0:
            sample_desc = f"{autopsy.sample_size} settled wagers"
            perf_desc = f"Realized Win Rate: {autopsy.win_rate * 100:.1f}% | Historical PnL: ${autopsy.total_pnl:.2f} | Realized ROI: {autopsy.realized_roi * 100:.1f}%"
            side_breakdown = ", ".join(f"{k}: ${v:.2f}" for k, v in autopsy.pnl_by_side.items()) if autopsy.pnl_by_side else "None"
            disp_desc = f"Observed Dispersion Index (Var/Mean): {autopsy.dispersion_index:.3f}"
            worst_note = f"- CRITICAL CASH LEAK: Side '{autopsy.worst_side}' accounted for the majority of financial losses.\n" if autopsy.worst_side else ""
            pathology_desc = autopsy.pathology_summary
        else:
            sample_desc = "0 wagers (Cold Start market - unobserved historical bets)"
            perf_desc = "Realized Win Rate: N/A (cold start) | Historical PnL: $0.00 | Realized ROI: N/A"
            side_breakdown = "None (unobserved cold start)"
            disp_desc = f"Dispersion Index (Var/Mean): Unobserved prior (standard Poisson assumption: {autopsy.dispersion_index:.3f})"
            worst_note = ""
            pathology_desc = "Cold start unmodeled market; construct first principled quantitative architecture from domain fundamentals."

        recommended_dists = ", ".join(autopsy.recommended_distributions)

        prompt = (
            f"TARGET QUANTITATIVE BETTING SEGMENT: '{autopsy.segment_key}'\n"
            f"- League: {autopsy.league} | Game Scope: {autopsy.scope} | Market Family: {autopsy.family}\n\n"
            f"HISTORICAL FORENSIC AUTOPSY (EMPIRICAL EVIDENCE):\n"
            f"- Settled Historical Sample: {sample_desc}\n"
            f"- {perf_desc}\n"
            f"- PnL Breakdown by Side: {side_breakdown}\n"
            f"{worst_note}"
            f"- Empirical Baseline Means: Home expected = {autopsy.home_expected:.2f}, Away expected = {autopsy.away_expected:.2f}\n"
            f"- {disp_desc}\n"
            f"- Identified Empirical Pathology: {pathology_desc}\n\n"
            f"MATHEMATICAL OBJECTIVE & OPEN QUANTITATIVE FREEDOM:\n"
            f"1. Resolve the empirical cash leak and calibration defect identified above. Zero hardcoded constants or arbitrary static clamps.\n"
            f"2. FIRST-PRINCIPLES MATHEMATICAL DERIVATION: Do NOT anchor to standard recipes, buzzword lists, or historical templates. "
            f"Historical baselines tested: {recommended_dists} (benchmark only; do NOT copy or anchor to this baseline). "
            f"Independently formulate the theoretical model structure directly from empirical moments and domain dynamics.\n"
            f"3. Anchor distributions around empirical domain reality (Home: {autopsy.home_expected:.2f}, Away: {autopsy.away_expected:.2f}), "
            f"and dynamically scale with situational inputs ('home_expected', 'away_expected', attack/defense exposure, tempo/pace, rest days).\n"
            f"4. If modeling overdispersed totals, calibrate dispersion alpha from observed index ({autopsy.dispersion_index:.3f}) or domain dynamics for {autopsy.league}.\n"
            f"5. If modeling spreads or moneylines, ensure margin mass respects sport-specific key numbers and limits home-field over-confidence.\n"
        )
        if base_problem and base_problem.strip():
            prompt += f"\nADDITIONAL USER/SUPERVISOR DIAGNOSIS:\n{base_problem.strip()}\n"

        return prompt
