from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Optional

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import gammaln
from scipy.stats import poisson

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice


class EPLBothScoreCandidateModel(BaseCandidateModel):
    """
    Independent, market-price-free EPL full-game BTTS model.

    Construction
    ------------
    Goal-count marginals are Poisson, consistent with the supplied dispersion
    index of 1.000. Their baseline means are the supplied empirical estimates,
    1.60 and 1.20, unless pre-match expected goals are provided.

    Let a=P(H>0), b=P(A>0), and q=P(H>0,A>0). The four scoring-status masses are

        P00 = 1-a-b+q
        P10 = a-q
        P01 = b-q
        P11 = q.

    One fitted parameter specifies their log odds ratio:

        eta = log(P00 * P11 / (P10 * P01)).

    Solving this equation inside the Frechet bounds guarantees a valid joint
    distribution while preserving both goal-count marginals. Conditional on
    scoring status, positive counts follow their zero-truncated Poisson laws.
    Thus dependence changes the zero cells without inventing overdispersion.

    Means and dispersion alone do not identify eta. Until fitted to actual
    match scores, eta=0 is the explicitly identified independence fallback.

    Features
    --------
    Expected goals must be pre-match forecasts, never realized scores.
    Nested home/away attack and defense fields are positive multiplicative
    exposures centered on one. Higher defense means MORE goals conceded.
    tempo or pace is likewise a positive exposure centered on one.

    Defaults multiply expected goals by the relevant attack, opponent defense,
    and tempo exposures. Rest effects start at zero and are learned. Do not
    provide exposures already incorporated into expected goals unless training
    has established how they should be used.

    Fit requires chronological, unselected match-level training and validation
    samples. It must not be trained only on accepted wagers. Each row requires
    match_id, kickoff, home_goals, and away_goals. kickoff is a finite numeric
    timestamp. Exact settled scores, not token payout labels, define BTTS.

    Validation is a conservative operational check, not a statistical proof
    of profitability. No historical ROI target is guaranteed.

    Integration
    -----------
    Pass distribution_type if DiscreteDistribution is not exported by the
    engine's base module or injected into this module's execution scope.
    Its from_scores method must accept {(home_goals, away_goals): probability}.

    All computation is deterministic. No persistence, network, process, or
    operating-system access is performed.
    """

    FEATURE_NAMES = (
        "intercept",
        "log_attack_exposure",
        "log_opponent_defense_exposure",
        "log_tempo",
        "relative_log_rest",
    )

    def __init__(
        self,
        model_id="codex_synth_epl_full_game_1108_172314",
        version="1.0.0",
        league="EPL",
        scope="full_game",
        family="both_score",
        *,
        empirical_home_mean: float = 1.60,
        empirical_away_mean: float = 1.20,
        distribution_type: Any = None,
        numerical_tail_budget: float = np.finfo(float).eps,
        max_score_cells: Optional[int] = None,
    ):
        if (league, scope, family) != ("EPL", "full_game", "both_score"):
            raise ValueError("This candidate supports EPL|full_game|both_score only.")

        super().__init__(
            model_id=model_id,
            version=version,
            league=league,
            scope=scope,
            family=family,
        )
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family

        self.empirical_home_mean = self._positive(
            empirical_home_mean, "empirical_home_mean"
        )
        self.empirical_away_mean = self._positive(
            empirical_away_mean, "empirical_away_mean"
        )
        self.numerical_tail_budget = self._finite(
            numerical_tail_budget, "numerical_tail_budget"
        )
        if not 0.0 < self.numerical_tail_budget < 1.0:
            raise ValueError("numerical_tail_budget must lie strictly between 0 and 1.")
        if max_score_cells is not None:
            if (
                isinstance(max_score_cells, bool)
                or not isinstance(max_score_cells, int)
                or max_score_cells <= 0
            ):
                raise ValueError("max_score_cells must be a positive integer.")
        self.max_score_cells = max_score_cells
        self.distribution_type = distribution_type

        # Unit coefficients express multiplicative exposure definitions.
        # Zero intercept/rest coefficients introduce no unsupported effect.
        self.home_coefficients = np.array([0.0, 1.0, 1.0, 1.0, 0.0])
        self.away_coefficients = self.home_coefficients.copy()
        self.log_scoring_odds_ratio = 0.0

        self.is_fitted = False
        self.validation_passed = False
        self.validation_report: Dict[str, Any] = {}
        self.training_end: Optional[float] = None
        self.validation_end: Optional[float] = None

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f"{name} must be numeric, not Boolean.")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number.") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} must be finite.")
        return result

    @classmethod
    def _positive(cls, value: Any, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0.0:
            raise ValueError(f"{name} must be strictly positive.")
        return result

    @classmethod
    def _goal_count(cls, value: Any, name: str) -> int:
        result = cls._finite(value, name)
        if result < 0.0 or not result.is_integer():
            raise ValueError(f"{name} must be a nonnegative integer.")
        return int(result)

    def _features(self, context: Mapping[str, Any]):
        if not isinstance(context, Mapping):
            raise ValueError("game_context must be a mapping.")
        for key, expected in (
            ("league", self.league),
            ("scope", self.scope),
            ("family", self.family),
        ):
            if key in context and context[key] != expected:
                raise ValueError(f"Incompatible {key}: {context[key]!r}.")

        home = context.get("home", {})
        away = context.get("away", {})
        if not isinstance(home, Mapping) or not isinstance(away, Mapping):
            raise ValueError("home and away must be exposure mappings.")

        mh = self._positive(
            context.get("home_expected", self.empirical_home_mean),
            "home_expected",
        )
        ma = self._positive(
            context.get("away_expected", self.empirical_away_mean),
            "away_expected",
        )
        tempo = self._positive(
            context.get("tempo", context.get("pace", 1.0)), "tempo"
        )
        if "tempo" in context and "pace" in context:
            pace = self._positive(context["pace"], "pace")
            if pace != tempo:
                raise ValueError("Conflicting tempo and pace exposures.")

        ha = self._positive(home.get("attack", 1.0), "home.attack")
        hd = self._positive(home.get("defense", 1.0), "home.defense")
        aa = self._positive(away.get("attack", 1.0), "away.attack")
        ad = self._positive(away.get("defense", 1.0), "away.defense")

        rest = {}
        for key in ("home_rest_days", "away_rest_days"):
            if context.get(key) is not None:
                rest[key] = self._finite(context[key], key)
                if rest[key] < 0.0:
                    raise ValueError(f"{key} cannot be negative.")
        relative_rest = 0.0
        if len(rest) == 2:
            relative_rest = (
                math.log1p(rest["home_rest_days"])
                - math.log1p(rest["away_rest_days"])
            )

        xh = np.array(
            [1.0, math.log(ha), math.log(ad), math.log(tempo), relative_rest]
        )
        xa = np.array(
            [1.0, math.log(aa), math.log(hd), math.log(tempo), -relative_rest]
        )
        return math.log(mh), math.log(ma), xh, xa

    def expected_goals(self, game_context: Dict[str, Any]):
        oh, oa, xh, xa = self._features(game_context)
        try:
            mh = math.exp(oh + float(xh @ self.home_coefficients))
            ma = math.exp(oa + float(xa @ self.away_coefficients))
        except OverflowError as exc:
            raise ValueError("Expected goals exceed floating-point range.") from exc
        return (
            self._positive(mh, "modeled_home_expected"),
            self._positive(ma, "modeled_away_expected"),
        )

    @staticmethod
    def _status_table(mh: float, ma: float, eta: float) -> np.ndarray:
        """Rows indicate home scoring; columns indicate away scoring."""
        zh, za = math.exp(-mh), math.exp(-ma)
        ah, aa = -math.expm1(-mh), -math.expm1(-ma)

        if eta == 0.0:
            return np.array([[zh * za, zh * aa], [ah * za, ah * aa]])

        # Solve for P00. Representing the rare zero events directly avoids
        # subtracting nearly equal probabilities when scoring is likely.
        lower = max(0.0, zh - aa)
        upper = min(zh, za)

        def table(z):
            return np.array(
                [[z, zh - z], [za - z, ah - (za - z)]], dtype=float
            )

        if lower == upper:
            result = table(lower)
        else:
            scale = math.exp(-abs(eta))

            def equation(z):
                cells = table(z)
                diagonal = cells[0, 0] * cells[1, 1]
                off_diagonal = cells[0, 1] * cells[1, 0]
                if eta >= 0.0:
                    return scale * diagonal - off_diagonal
                return diagonal - scale * off_diagonal

            z = brentq(
                equation,
                lower,
                upper,
                xtol=np.nextafter(0.0, 1.0),
                rtol=4.0 * np.finfo(float).eps,
            )
            result = table(z)

        if np.any(result < 0.0) or not np.all(np.isfinite(result)):
            raise FloatingPointError("Invalid scoring-status probabilities.")
        return result

    def predict_btts_probability(self, game_context: Dict[str, Any]) -> float:
        mh, ma = self.expected_goals(game_context)
        return float(
            self._status_table(mh, ma, self.log_scoring_odds_ratio)[1, 1]
        )

    def _support_limit(self, mean: float) -> int:
        limit = float(poisson.isf(self.numerical_tail_budget, mean))
        if not math.isfinite(limit):
            raise FloatingPointError(
                "Poisson quantile failed; choose a representable tail budget."
            )
        return max(1, int(limit))

    @staticmethod
    def _positive_count_weights(mean: float, limit: int) -> np.ndarray:
        """
        Last positive-count bucket includes the remaining upper tail.
        This preserves BTTS exactly, while score-tail coarsening is explicit.
        """
        counts = np.arange(1, limit + 1, dtype=float)
        weights = np.exp(
            -mean + counts * math.log(mean) - gammaln(counts + 1.0)
        )
        weights[-1] = poisson.sf(limit - 1, mean)
        total = float(weights.sum())
        if total <= 0.0 or not math.isfinite(total):
            raise FloatingPointError("Could not construct positive-count law.")
        return weights / total

    def predict_distribution(
        self, game_context: Dict[str, Any]
    ) -> DiscreteDistribution:
        mh, ma = self.expected_goals(game_context)
        status = self._status_table(mh, ma, self.log_scoring_odds_ratio)
        nh, na = self._support_limit(mh), self._support_limit(ma)

        if (
            self.max_score_cells is not None
            and (nh + 1) * (na + 1) > self.max_score_cells
        ):
            raise MemoryError(
                "Score lattice exceeds the configured resource budget; "
                "use predict_btts_probability for exact BTTS pricing."
            )

        wh = self._positive_count_weights(mh, nh)
        wa = self._positive_count_weights(ma, na)
        matrix = np.empty((nh + 1, na + 1), dtype=float)
        matrix[0, 0] = status[0, 0]
        matrix[0, 1:] = status[0, 1] * wa
        matrix[1:, 0] = status[1, 0] * wh
        matrix[1:, 1:] = status[1, 1] * np.outer(wh, wa)

        scores = {
            (h, a): float(matrix[h, a])
            for h in range(nh + 1)
            for a in range(na + 1)
        }

        distribution_type = self.distribution_type
        if distribution_type is None:
            distribution_type = globals().get("DiscreteDistribution")
        if distribution_type is None:
            from betting_engine.sandbox_models import base

            distribution_type = getattr(base, "DiscreteDistribution", None)
        if distribution_type is None:
            raise RuntimeError(
                "Supply the engine's DiscreteDistribution as distribution_type."
            )
        return distribution_type.from_scores(scores)

    def _prepare_rows(self, rows: Sequence[Mapping[str, Any]]):
        rows = list(rows)
        if not rows:
            raise ValueError("A nonempty match sample is required.")

        ids, times, offsets_h, offsets_a, features_h, features_a = (
            [], [], [], [], [], []
        )
        goals_h, goals_a = [], []
        for row in rows:
            match_id = row.get("match_id")
            if not isinstance(match_id, str) or not match_id.strip():
                raise ValueError("Each match requires a nonempty string match_id.")
            ids.append(match_id)
            times.append(self._finite(row["kickoff"], "kickoff"))
            oh, oa, xh, xa = self._features(row)
            offsets_h.append(oh)
            offsets_a.append(oa)
            features_h.append(xh)
            features_a.append(xa)
            goals_h.append(self._goal_count(row["home_goals"], "home_goals"))
            goals_a.append(self._goal_count(row["away_goals"], "away_goals"))

        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate matches would overweight observations.")
        return {
            "ids": set(ids),
            "times": np.asarray(times),
            "oh": np.asarray(offsets_h),
            "oa": np.asarray(offsets_a),
            "xh": np.asarray(features_h),
            "xa": np.asarray(features_a),
            "gh": np.asarray(goals_h),
            "ga": np.asarray(goals_a),
        }

    @staticmethod
    def _fit_poisson(offset, design, goals, initial):
        """
        Estimate only identifiable feature directions. Unobserved directions
        retain their declared structural defaults.
        """
        _, singular_values, vt = np.linalg.svd(design, full_matrices=False)
        tolerance = (
            np.finfo(float).eps * max(design.shape) * singular_values[0]
        )
        rank = int(np.count_nonzero(singular_values > tolerance))
        if len(goals) <= rank or goals.sum() == 0:
            raise ValueError("Insufficient information for a finite mean fit.")

        basis = vt[:rank].T
        reduced = design @ basis
        initial_predictor = offset + design @ initial

        def objective(delta):
            predictor = initial_predictor + reduced @ delta
            with np.errstate(over="ignore", invalid="ignore"):
                means = np.exp(predictor)
                value = np.mean(means - goals * predictor)
                gradient = reduced.T @ (means - goals) / len(goals)
            if not math.isfinite(float(value)) or not np.all(
                np.isfinite(gradient)
            ):
                return math.inf, np.zeros_like(delta)
            return float(value), gradient

        result = minimize(
            objective,
            np.zeros(rank),
            method="BFGS",
            jac=True,
        )
        if not result.success or not np.all(np.isfinite(result.x)):
            raise RuntimeError(f"Goal-mean fit failed: {result.message}")

        coefficients = initial + basis @ result.x
        means = np.exp(offset + design @ coefficients)
        if np.any(means <= 0.0) or not np.all(np.isfinite(means)):
            raise FloatingPointError("Fitted means are not representable.")
        return coefficients, means, rank

    def fit(
        self,
        training_matches: Sequence[Mapping[str, Any]],
        validation_matches: Sequence[Mapping[str, Any]],
        *,
        settlement_mapping_verified: bool,
        optimizer_options: Optional[Dict[str, Any]] = None,
    ) -> "EPLBothScoreCandidateModel":
        """
        Fit marginal count likelihoods, then conditional scoring association.

        settlement_mapping_verified is an external audit attestation: it must
        cover full-game scope, BTTS proposition wording, YES/NO token identity,
        void handling, and score-to-settlement mapping. This model cannot
        establish those facts from aggregate win rate or PnL.

        Validation must remain untouched during model selection. Repeatedly
        trying candidates against the same validation sample compromises it.
        """
        if settlement_mapping_verified is not True:
            raise ValueError("Verified settlement and token mapping is required.")

        train = self._prepare_rows(training_matches)
        valid = self._prepare_rows(validation_matches)
        if train["ids"] & valid["ids"]:
            raise ValueError("Training and validation matches overlap.")
        if train["times"].max() >= valid["times"].min():
            raise ValueError("Every validation match must follow training.")

        initial = np.array([0.0, 1.0, 1.0, 1.0, 0.0])
        bh, mh, rank_h = self._fit_poisson(
            train["oh"], train["xh"], train["gh"], initial
        )
        ba, ma, rank_a = self._fit_poisson(
            train["oa"], train["xa"], train["ga"], initial
        )
        sh = (train["gh"] > 0).astype(int)
        sa = (train["ga"] > 0).astype(int)

        # Interior observations in all four cells prevent the ordinary
        # boundary/separation case from masquerading as a finite estimate.
        observed_cells = np.bincount(2 * sh + sa, minlength=4)
        if np.any(observed_cells == 0):
            raise ValueError(
                "All four scoring-status cells must be observed for this "
                "unregularized dependence fit."
            )

        def dependence_loss(parameters):
            eta = float(parameters[0])
            if not math.isfinite(eta):
                return math.inf
            total = 0.0
            for hmean, amean, hscored, ascored in zip(mh, ma, sh, sa):
                mass = self._status_table(
                    float(hmean), float(amean), eta
                )[hscored, ascored]
                if mass <= 0.0:
                    return math.inf
                total -= math.log(float(mass))
            return total / len(sh)

        association_fit = minimize(
            dependence_loss,
            np.zeros(1),
            method="BFGS",
            options=dict(optimizer_options or {}),
        )
        if not association_fit.success:
            raise RuntimeError(
                f"Scoring-dependence fit failed: {association_fit.message}"
            )
        eta = float(association_fit.x[0])
        if not math.isfinite(eta):
            raise FloatingPointError("Nonfinite fitted scoring association.")

        vmh = np.exp(valid["oh"] + valid["xh"] @ bh)
        vma = np.exp(valid["oa"] + valid["xa"] @ ba)
        if (
            not np.all(np.isfinite(vmh))
            or not np.all(np.isfinite(vma))
            or np.any(vmh <= 0.0)
            or np.any(vma <= 0.0)
        ):
            raise FloatingPointError("Validation means are not representable.")

        predicted = np.array(
            [
                self._status_table(float(h), float(a), eta)[1, 1]
                for h, a in zip(vmh, vma)
            ]
        )
        independent = -np.expm1(-vmh) * -np.expm1(-vma)
        anchor_h = np.exp(valid["oh"] + valid["xh"] @ initial)
        anchor_a = np.exp(valid["oa"] + valid["xa"] @ initial)
        anchored = -np.expm1(-anchor_h) * -np.expm1(-anchor_a)
        outcomes = ((valid["gh"] > 0) & (valid["ga"] > 0)).astype(float)
        if np.unique(outcomes).size != 2:
            raise ValueError("Validation must contain both BTTS outcomes.")

        brier = float(np.mean((predicted - outcomes) ** 2))
        independent_brier = float(np.mean((independent - outcomes) ** 2))
        anchored_brier = float(np.mean((anchored - outcomes) ** 2))

        def pearson_dispersion(goals, means):
            return float(np.mean((goals - means) ** 2 / means))

        report = {
            "training_matches": len(sh),
            "validation_matches": len(outcomes),
            "home_identifiable_parameters": rank_h,
            "away_identifiable_parameters": rank_a,
            "training_status_cell_counts": observed_cells.tolist(),
            "brier": brier,
            "fitted_independent_brier": independent_brier,
            "unfitted_anchor_brier": anchored_brier,
            "brier_improvement_over_fitted_independence": (
                independent_brier - brier
            ),
            "mean_predicted_btts": float(predicted.mean()),
            "observed_btts_rate": float(outcomes.mean()),
            "calibration_bias": float(np.mean(predicted - outcomes)),
            "home_validation_pearson_dispersion": pearson_dispersion(
                valid["gh"], vmh
            ),
            "away_validation_pearson_dispersion": pearson_dispersion(
                valid["ga"], vma
            ),
            # Unbinned observations support externally chosen reliability
            # curves without arbitrary internal binning.
            "reliability_probabilities": predicted.tolist(),
            "reliability_outcomes": outcomes.astype(int).tolist(),
            "settlement_mapping_verified": True,
            "validation_passed": (
                brier <= independent_brier and brier <= anchored_brier
            ),
            "dispersion_note": (
                "Predictive residual dispersion is diagnostic; it does not "
                "automatically justify adding a latent frailty distribution."
            ),
        }

        # Commit only after all estimation and validation calculations succeed.
        self.home_coefficients = bh
        self.away_coefficients = ba
        self.log_scoring_odds_ratio = eta
        self.is_fitted = True
        self.validation_passed = bool(report["validation_passed"])
        self.validation_report = report
        self.training_end = float(train["times"].max())
        self.validation_end = float(valid["times"].max())
        return self

    def evaluate_contract(
        self,
        game_context: Dict[str, Any],
        *,
        contract_side: str,
        token_represents: str,
        executable_ask: float,
        entry_fee_per_share: float,
        winning_settlement_fee_per_share: float,
        slippage_per_share: float,
        mapping_verified: bool,
        externally_calibration_approved: bool,
    ) -> Dict[str, Any]:
        """
        Value a binary contract paying one currency unit on success.

        token_represents is the actual proposition paid by this token:
        'both_score' or 'not_both_score'. It is deliberately independent of
        an exchange's YES/NO token label.

        executable_ask must be executable for the contemplated order size.
        Spread is already included in the ask. Fees and incremental slippage
        must be supplied from the actual venue/order economics.

        The user's 1.40 minimum refers to quoted gross decimal odds, 1/ask.
        Economic eligibility otherwise uses continuous, net expected value.
        There is no side ban, odds ceiling, market-probability blending, or
        arbitrary probability/edge clamp.
        """
        if contract_side not in ("yes", "no"):
            raise ValueError("contract_side must be 'yes' or 'no'.")
        if token_represents not in ("both_score", "not_both_score"):
            raise ValueError("Explicit token payout semantics are required.")

        ask = self._positive(executable_ask, "executable_ask")
        if ask >= 1.0:
            raise ValueError("A binary contract ask must be below its unit payout.")

        fees = {}
        for name, value in (
            ("entry_fee_per_share", entry_fee_per_share),
            ("winning_settlement_fee_per_share", winning_settlement_fee_per_share),
            ("slippage_per_share", slippage_per_share),
        ):
            fees[name] = self._finite(value, name)
            if fees[name] < 0.0:
                raise ValueError(f"{name} cannot be negative.")

        winning_receipt = 1.0 - fees["winning_settlement_fee_per_share"]
        if winning_receipt <= 0.0:
            raise ValueError("Winning settlement fees consume the entire payout.")

        probability_yes = self.predict_btts_probability(game_context)
        probability = (
            probability_yes
            if token_represents == "both_score"
            else 1.0 - probability_yes
        )
        cost = ask + fees["entry_fee_per_share"] + fees["slippage_per_share"]
        expected_profit = probability * winning_receipt - cost
        quoted_decimal_odds = 1.0 / ask
        economically_eligible = (
            quoted_decimal_odds >= 1.40 and expected_profit > 0.0
        )

        reasons = []
        if not self.is_fitted:
            reasons.append("unfitted_independence_fallback")
        if not self.validation_passed:
            reasons.append("held_out_predictive_check_not_passed")
        if mapping_verified is not True:
            reasons.append("contract_mapping_not_verified")
        if externally_calibration_approved is not True:
            reasons.append("held_out_calibration_not_approved")

        if self.validation_end is None:
            reasons.append("no_validation_cutoff")
        elif "kickoff" not in game_context:
            reasons.append("prediction_timestamp_missing")
        elif self._finite(game_context["kickoff"], "kickoff") <= self.validation_end:
            reasons.append("match_not_after_validation_cutoff")

        return {
            "model_id": self.model_id,
            "contract_side": contract_side,
            "token_represents": token_represents,
            "btts_probability": probability_yes,
            "contract_win_probability": probability,
            "quoted_decimal_odds": quoted_decimal_odds,
            "all_in_cost_per_share": cost,
            "net_winning_receipt_per_share": winning_receipt,
            "break_even_probability": cost / winning_receipt,
            "expected_profit_per_share": expected_profit,
            "expected_return_on_cost": expected_profit / cost,
            "economically_eligible": economically_eligible,
            "operationally_ready": not reasons,
            "eligible": economically_eligible and not reasons,
            "operational_blockers": reasons,
        }