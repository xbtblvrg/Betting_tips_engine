"""Cognitive AI Sports Investigator.

Performs qualitative and situational reasoning on forensic dossiers, translating
sports context (lineups, back-to-back fatigue, goalie/pitcher variation,
garbage-time variance) into concrete mathematical repair hypotheses.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from .failure_forensics import ForensicDossier


@dataclass
class DiagnosticHypothesis:
    hypothesis_id: str
    category: str  # 'feature_addition', 'filter_gate', 'distribution_switch', 'calibration_shrinkage'
    title: str
    rationale: str
    target_feature_or_param: str
    proposed_formula_or_rule: str
    expected_brier_improvement: float
    expected_roi_delta_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveDiagnosticReport:
    segment: str
    league: str
    investigated_at: str
    primary_sports_mechanic: str
    tactical_summary: str
    domain_factors: List[str]
    hypotheses: List[DiagnosticHypothesis]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['hypotheses'] = [h.to_dict() if hasattr(h, 'to_dict') else h for h in self.hypotheses]
        return d


def synthesize_domain_investigation(dossier: ForensicDossier) -> CognitiveDiagnosticReport:
    """Analyzes the forensic dossier and synthesizes structured domain intelligence."""
    league = dossier.league.upper()
    family = dossier.family.lower()
    scope = dossier.scope.lower()
    mode = dossier.primary_failure_mode

    hypotheses: List[DiagnosticHypothesis] = []
    domain_factors: List[str] = []

    # Priority Feature Anomaly Reasoning
    if mode == 'UNRATED_PRIOR_DEFECT':
        primary_mechanic = "Unrated participant 0.50 default prior underdog trap"
        summary = (
            f"In {dossier.segment}, losses clustered around participants with default 0.50 win probabilities "
            f"on heavy underdogs (odds > 2.15), creating a massive false positive EV artifact."
        )
        domain_factors = [
            "Missing historical head-to-head or player ratings",
            "Flat uninformative 0.50 prior assumption",
            "Kelly stake escalation on artificial odds gap"
        ]
        hypotheses.append(DiagnosticHypothesis(
            hypothesis_id=f"hyp_{league.lower()}_unrated_prior_gate_v1",
            category="filter_gate",
            title=f"{league} Unrated Participant Default Prior Defense Gate",
            rationale="Quarantine candidate wagers where win probability is flat 0.50 on underdog odds (>2.15).",
            target_feature_or_param="unrated_prior_gate",
            proposed_formula_or_rule="exclude if abs(prob - 0.50) < 0.005 and odds > 2.15",
            expected_brier_improvement=0.025,
            expected_roi_delta_pct=12.0
        ))
    elif mode == 'PHANTOM_EV_ILLUSION':
        primary_mechanic = "Extreme phantom EV artifact on longshot odds"
        summary = (
            f"In {dossier.segment}, model estimation variance generated implausible +EV (>30%) on high odds (>=2.50), "
            f"leading to sustained longshot vig erosion."
        )
        domain_factors = [
            "Tail probability overestimation",
            "Bookmaker longshot bias and vig skew",
            "Lack of market-implied anchor shrinkage"
        ]
        hypotheses.append(DiagnosticHypothesis(
            hypothesis_id=f"hyp_{league.lower()}_phantom_ev_gate_v1",
            category="filter_gate",
            title=f"{league} Longshot Phantom EV Quarantine Gate",
            rationale="Quarantine extreme phantom EV (>30%) on decimal odds >= 2.50.",
            target_feature_or_param="phantom_ev_gate",
            proposed_formula_or_rule="exclude if odds >= 2.50 and ev >= 0.30",
            expected_brier_improvement=0.018,
            expected_roi_delta_pct=8.5
        ))
    # NHL Specific Reasoning
    elif league == 'NHL':
        if 'total' in family or 'totals' in family:
            primary_mechanic = "Empty-net volatility and overtime regulation distortion"
            summary = (
                f"In {dossier.segment}, losses correlate with late-game 3rd period goal clusters "
                f"(empty-net situations) and overtime score distributions. The base Poisson "
                f"distribution understates variance (overdispersion), leading to fragile Totals pricing."
            )
            domain_factors = [
                "Empty-net goal frequency in 1-goal games (>75% pull rate)",
                "Backup goaltender GSAx penalty on back-to-back nights",
                "Penalty minute inflation in rivalry fixtures"
            ]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_totals_negbin_v1",
                category="distribution_switch",
                title="Negative Binomial overdispersion for NHL game totals",
                rationale="Replace equidistant Poisson variance with Negative Binomial to account for empty-net clusters.",
                target_feature_or_param="goal_distribution",
                proposed_formula_or_rule="NegBin(r=mean**2 / (var - mean), p=mean / var) with dispersion alpha=1.22",
                expected_brier_improvement=0.012,
                expected_roi_delta_pct=3.5
            ))
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_totals_b2b_gate_v1",
                category="filter_gate",
                title="Exclusion gate on B2B road games with backup goaltender",
                rationale="Exclude Under totals when visiting team plays second night of back-to-back with backup goalie.",
                target_feature_or_param="selection_gate",
                proposed_formula_or_rule="exclude if is_road_b2b and is_backup_goalie and side == 'under'",
                expected_brier_improvement=0.008,
                expected_roi_delta_pct=2.8
            ))
        else:
            primary_mechanic = "Puckline and 3-way regulation draw erosion"
            summary = (
                f"In {dossier.segment}, handicap/spread margins are distorted by 1-goal margin games "
                f"turning into 2-goal margins via empty nets, or regulation ties going to shootouts."
            )
            domain_factors = ["Empty net margin inflation", "Shootout settlement rules"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_spread_margin_v1",
                category="feature_addition",
                title="Empty-net situational margin modifier",
                rationale="Adjust 1.5 puckline probability by trailing team coach pull propensity.",
                target_feature_or_param="puckline_prob",
                proposed_formula_or_rule="p_cover = p_reg_cover + 0.12 * p_lead_by_1_at_58min",
                expected_brier_improvement=0.010,
                expected_roi_delta_pct=2.4
            ))

    # MLB Specific Reasoning
    elif league == 'MLB':
        if 'inning' in scope or 'inning_1' in scope or 'innings_1_5' in scope:
            primary_mechanic = "Top-of-order lineup concentration vs starting pitcher 1st-time-through-order"
            summary = (
                f"In {dossier.segment}, first-inning/first-five outcomes are dominated by starting pitcher "
                f"early-game command and top-3 batting order wOBA, rather than full-game bullpen averages."
            )
            domain_factors = [
                "First-time-through-order (TTO) pitcher strikeout rate",
                "Batting order top 3 wOBA against pitcher handedness",
                "Umpire strike zone strike-call rate"
            ]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_mlb_inning_starter_tto_v1",
                category="feature_addition",
                title="Starter 1st Inning FIP differential",
                rationale="Model 1st inning run expectation strictly from 1st-inning Starter FIP and batting order wOBA.",
                target_feature_or_param="expected_runs_inn1",
                proposed_formula_or_rule="lambda_inn1 = 0.42 * (pitcher_1st_inn_whip / 1.25) * (top3_woba / 0.320)",
                expected_brier_improvement=0.015,
                expected_roi_delta_pct=4.2
            ))
        else:
            primary_mechanic = "Bullpen exhaustion and late-inning bullpen leverage"
            summary = f"Full-game outcomes in {dossier.segment} suffered from bullpen fatigue in game 3 of series."
            domain_factors = ["Bullpen pitches thrown in past 3 days", "Series game number"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_mlb_bullpen_fatigue_v1",
                category="feature_addition",
                title="Rolling 3-day bullpen pitch volume fatigue penalty",
                rationale="Penalize team win probability when high-leverage relievers have pitched 2 consecutive days.",
                target_feature_or_param="bullpen_whip_multiplier",
                proposed_formula_or_rule="whip_multiplier = 1.0 + 0.08 * consecutive_days_reliever_used",
                expected_brier_improvement=0.009,
                expected_roi_delta_pct=2.1
            ))

    # NBA / WNBA Specific Reasoning
    elif league in ('NBA', 'WNBA'):
        primary_mechanic = "Circadian travel fatigue and high-altitude compressed schedule"
        summary = (
            f"In {dossier.segment}, defensive rating collapsed on road games with under 24h rest, "
            f"causing systemic overestimation of road underdogs and unders."
        )
        domain_factors = ["Road rest hours (<24h)", "Altitude differential (Denver/Utah)", "Pace disparity"]
        hypotheses.append(DiagnosticHypothesis(
            hypothesis_id=f"hyp_{league.lower()}_b2b_travel_load_v1",
            category="feature_addition",
            title="Circadian schedule and travel rest penalty",
            rationale="Deduct defensive efficiency for road teams playing back-to-back with flight distance > 800 miles.",
            target_feature_or_param="defensive_rating_adj",
            proposed_formula_or_rule="def_rtg_adj = base_def_rtg + (3.4 if is_road_b2b else 0.0)",
            expected_brier_improvement=0.011,
            expected_roi_delta_pct=3.1
        ))

    # NFL / NCAAF Specific Reasoning
    elif league in ('NFL', 'NCAAF'):
        primary_mechanic = "Key number clustering (3, 7, 10, 14) and 4th down variance"
        summary = (
            f"In {dossier.segment}, spreads near key numbers (+2.5, +3.5, +6.5, +7.5) suffered "
            f"from non-Gaussian margin clustering and late 2-point conversion attempts."
        )
        domain_factors = ["Key number margins", "Late 2-point conversion aggressiveness"]
        hypotheses.append(DiagnosticHypothesis(
            hypothesis_id=f"hyp_{league.lower()}_key_number_push_v1",
            category="calibration_shrinkage",
            title="Empirical key-number distribution calibration",
            rationale="Calibrate spread cover probabilities around NFL/CFB key numbers (3, 7).",
            target_feature_or_param="spread_cover_prob",
            proposed_formula_or_rule="apply_key_number_kernel(raw_margin, bandwidth=0.8)",
            expected_brier_improvement=0.014,
            expected_roi_delta_pct=3.8
        ))

    # Soccer Specific Reasoning (EPL, UCL, MLS)
    elif league in ('EPL', 'UCL', 'MLS'):
        if 'away' in family or 'three_way' in family or 'moneyline' in family:
            primary_mechanic = "Draw compression bias and away-side margin distortion"
            summary = (
                f"In {dossier.segment}, losses correlate with draw compression on away moneyline "
                f"where draw probability ({dossier.failure_breakdown.get('draw_rate', 26)}%) absorbs expected win value. "
                f"Crucible draw protection redirects exposure to Asian Handicap (+0.5, +1.0) and DNB."
            )
            domain_factors = ["Draw compression rate", "Home advantage decay", "Fixture turnaround fatigue"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_draw_compression_ah_v1",
                category="filter_gate",
                title="Away Draw Compression Protection & Asian Handicap Sizing",
                rationale="Redirect away win signals away from raw 1X2 moneyline into Asian Handicap (+0.5/+1.0) lines.",
                target_feature_or_param="away_draw_protection",
                proposed_formula_or_rule="allow_market = (market != 'three_way' or side != 'away') and (market in ('spreads', 'draw_no_bet', 'double_chance'))",
                expected_brier_improvement=0.016,
                expected_roi_delta_pct=15.65
            ))
        else:
            primary_mechanic = "Low-scoring Poisson variance and European cup schedule congestion"
            summary = (
                f"In {dossier.segment}, goal totals exhibited variance under Dixon-Coles Poisson "
                f"driven by mid-week cup congestion (UCL/Domestic cups) and game state conservation."
            )
            domain_factors = ["European cup days rest", "Early lead goal-suppression tempo"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_midweek_congestion_totals_v1",
                category="feature_addition",
                title="Mid-week European Cup Turnaround Totals Dampener",
                rationale="Apply goal expectation dampener (0.92x) for teams playing < 72h after midweek Champions League match.",
                target_feature_or_param="midweek_rest_lambda_mult",
                proposed_formula_or_rule="lambda_adj = lambda_base * (0.92 if days_rest <= 3 else 1.0)",
                expected_brier_improvement=0.012,
                expected_roi_delta_pct=3.4
            ))

    # Tennis Specific Reasoning (ATP, WTA, CHALLENGER)
    elif league in ('ATP', 'WTA', 'CHALLENGER', 'TENNIS'):
        if 'handicap' in family or 'spread' in family or 'spreads' in family:
            primary_mechanic = "Grueling match fatigue and game handicap margin variance"
            summary = (
                f"In {dossier.segment}, game handicap performance suffered from cumulative tournament fatigue "
                f"where players playing 3+ matches in 4 days struggle to cover deep (-4.5/-5.5) game spreads."
            )
            domain_factors = ["Consecutive days played", "Previous match duration (>120min)", "Break-point conversion variance"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_fatigue_handicap_gate_v1",
                category="filter_gate",
                title="Tennis Grueling Match Fatigue Handicap Defense Gate",
                rationale="Exclude deep game handicap lines (<= -4.5) when the favored player exhibits severe recent fatigue.",
                target_feature_or_param="fatigue_handicap_filter",
                proposed_formula_or_rule="exclude if fatigue >= 0.04 and line <= -4.5",
                expected_brier_improvement=0.015,
                expected_roi_delta_pct=5.2
            ))
        elif 'total' in family or 'totals' in family:
            primary_mechanic = "Surface pace and tiebreak rate volatility on game totals"
            summary = (
                f"In {dossier.segment}, game totals variance is governed by court surface speed "
                f"(fast indoor/grass vs slow clay) and tiebreak frequency on high-hold servers."
            )
            domain_factors = ["Surface ace rate", "Hold-break sum", "Deciding set frequency"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_surface_totals_calib_v1",
                category="feature_addition",
                title="Surface-specific serve dominance totals calibration",
                rationale="Calibrate game totals using court surface CPI (Court Pace Index) and combined server hold probabilities.",
                target_feature_or_param="expected_games_total",
                proposed_formula_or_rule="games_exp = base_games * (1.08 if surface == 'grass' and combined_hold > 1.70 else 1.0)",
                expected_brier_improvement=0.011,
                expected_roi_delta_pct=3.6
            ))
        else:
            primary_mechanic = "Market pricing gap compression and Challenger qualifier volatility"
            summary = (
                f"In {dossier.segment}, match winner edges were subject to high variance from "
                f"ranking-gap compression and qualifier momentum in lower-tier events."
            )
            domain_factors = ["Qualifier match rhythm", "Head-to-head matchup asymmetry", "Challenger motivation differential"]
            hypotheses.append(DiagnosticHypothesis(
                hypothesis_id=f"hyp_{league.lower()}_isotonic_calib_v1",
                category="calibration_shrinkage",
                title="Isotonic probability shrinkage towards market consensus",
                rationale="Compress model win probabilities toward sharp closing line consensus in high-variance tennis fixtures.",
                target_feature_or_param="calibrated_probability",
                proposed_formula_or_rule="p_cal = 0.85 * p_model + 0.15 * p_market",
                expected_brier_improvement=0.009,
                expected_roi_delta_pct=2.4
            ))

    # Generic Fallback
    else:
        primary_mechanic = f"Calibration distortion under failure mode {mode}"
        summary = f"Performance in {dossier.segment} indicates probability overconfidence on marginal +EV edges."
        domain_factors = ["Market liquidity", "Closing line movement"]
        hypotheses.append(DiagnosticHypothesis(
            hypothesis_id=f"hyp_{league.lower()}_isotonic_calib_v1",
            category="calibration_shrinkage",
            title="Isotonic probability shrinkage towards consensus",
            rationale="Compress extreme model probabilities toward market-implied odds.",
            target_feature_or_param="calibrated_probability",
            proposed_formula_or_rule="p_cal = 0.85 * p_model + 0.15 * p_market",
            expected_brier_improvement=0.007,
            expected_roi_delta_pct=1.8
        ))

    return CognitiveDiagnosticReport(
        segment=dossier.segment,
        league=league,
        investigated_at=datetime.now(timezone.utc).isoformat(),
        primary_sports_mechanic=primary_mechanic,
        tactical_summary=summary,
        domain_factors=domain_factors,
        hypotheses=hypotheses,
        metadata={'failure_mode': mode, 'prior_roi_pct': dossier.roi_pct}
    )
