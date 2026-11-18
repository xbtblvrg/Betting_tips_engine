"""
Candidate model for NCAAW | quarter_2 | exact_total.

Each team's second-quarter score is built as a compound sum of per-possession
point packets rather than a smooth Poisson count:

    S_team = sum_{i=1}^{N_team} X_i,   X_i in {0, 1, 2, 3}

1. Shared possessions.
   T, the total possession count in the 10-minute quarter, is drawn from an
   underdispersed Binomial. Possessions strictly alternate, so N_home and
   N_away differ by at most one. Which team gets the extra possession is set
   by who starts Q2 (the alternating-possession arrow). Because both teams
   draw from the same T, the model produces positive home/away correlation
   without any extra parameter.

2. Mechanical packets.
   X is not a free multinomial. It is derived from the possession mechanics:
   turnovers, shooting-foul free-throw trips (binomial over FT%), and-ones,
   and field-goal chains with offensive-rebound renewal (a geometric series).
   This keeps the parity (lattice) structure of real basketball scoring.

3. Quarter-reset bonus.
   NCAAW team fouls reset every quarter, and the 5th foul triggers two shots.
   Opponent fouls arrive as a Poisson process, so the time of the 5th foul is
   Erlang(5, lambda). The fraction of the quarter played in the bonus is
   integrated by quadrature. Bonus possessions use a separate packet
   distribution that includes non-shooting free-throw trips, which
   concentrates free throws late in the quarter.

4. End-of-half possession.
   The final possession of the half is deterministic given (T, starter). It
   uses a heave-contaminated packet distribution with fatter 3/0 mass.

5. Mean anchoring without clamps.
   Each team's packet distributions are exponentially tilted,
   p_k(theta) proportional to p_k * exp(theta * k). This is the minimum-KL
   adjustment that preserves mechanical shape. A root solve then makes
   E[S_team] exactly equal the situational expectation (anchors 20.00 /
   18.75). Attack/defense exposure, tempo, and rest change the shape and
   tempo split of the distribution. The tilt keeps the means anchored, so
   these inputs never double-count against home/away expected.

Dispersion is endogenous:
    Var(S) = Var(N) * E[X]^2 + E[N] * Var(X)
plus the lattice, bonus-mixture, and end-possession terms.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:  # Resolve DiscreteDistribution whether it lives in base or is pre-loaded.
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except Exception:  # pragma: no cover
        DiscreteDistribution = globals().get("DiscreteDistribution")  # type: ignore


# --------------------------------------------------------------------------- #
# Domain priors (NCAAW Division I mechanics). These are structural rates, not
# output clamps. Every one can be overridden from game_context.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NCAAWQuarterPriors:
    # Segment anchors (empirical baseline means for Q2)
    anchor_home: float = 20.00
    anchor_away: float = 18.75

    # Clock / structure
    game_quarters: int = 4
    tempo_per_team_game: float = 70.0  # possessions per team per 40 minutes
    tempo_prior_weight: float = 0.5    # log-blend: league tempo vs anchor-implied tempo
    possession_retention: float = 0.85  # Binomial q -> Var(T) = (1-q) * E[T]

    # Possession mechanics
    turnover_rate: float = 0.19
    shooting_foul_rate: float = 0.075
    and_one_rate: float = 0.020
    three_rate: float = 0.30  # share of FG attempts from three
    three_pct: float = 0.31
    two_pct: float = 0.45
    oreb_rate: float = 0.33
    ft_pct: float = 0.72

    # Quarter-reset bonus (5th team foul -> two shots)
    bonus_foul_threshold: int = 5
    team_fouls_per_game: float = 16.5
    bonus_nonshooting_rate: float = 0.09
    bonus_quadrature_bins: int = 10

    # End-of-half possession
    heave_share: float = 0.35
    heave_make: float = 0.04

    # Situational sensitivities (shape only; means are re-anchored)
    exposure_three_gain: float = 0.35
    exposure_foul_gain: float = 0.25
    fatigue_scale_days: float = 2.0
    fatigue_baseline_rest: float = 3.0
    fatigue_pace_gain: float = 0.03

    # Input-scale detection & numerics
    full_game_detection_ratio: float = 2.0
    tail_tol: float = 1e-13


# --------------------------------------------------------------------------- #
# Small numerical helpers
# --------------------------------------------------------------------------- #
def _num(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _prob(value: Any, default: float) -> float:
    v = _num(value, None)
    return v if (v is not None and 0.0 < v < 1.0) else default


def _tilt(x: np.ndarray, theta: float) -> np.ndarray:
    k = np.arange(x.size, dtype=float)
    logw = np.log(np.where(x > 0, x, np.finfo(float).tiny)) + theta * k
    logw -= logw.max()
    w = np.exp(logw) * (x > 0)
    return w / w.sum()


def _mean(x: np.ndarray) -> float:
    return float(np.dot(np.arange(x.size), x))


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #
class NCAAWQuarter2ExactTotalModel(BaseCandidateModel):
    """Compound possession-packet model for NCAAW second-quarter exact totals."""

    def __init__(
        self,
        model_id: str = "codex_synth_ncaaw_quarter_2_1107_235513",
        version: str = "1.0.0",
        league: str = "NCAAW",
        scope: str = "quarter_2",
        family: str = "exact_total",
        priors: Optional[NCAAWQuarterPriors] = None,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league, scope=scope, family=family
            )
        except TypeError:
            super().__init__()
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors = priors or NCAAWQuarterPriors()
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}
        params = self._situational_parameters(ctx)
        joint = self._joint_pmf(params)
        self._record_diagnostics(joint, params)
        return self._emit(joint)

    def total_pmf(self, game_context: Dict[str, Any]) -> Dict[int, float]:
        """Exact-total PMF (convenience accessor for pricing / auditing)."""
        params = self._situational_parameters(game_context or {})
        joint = self._joint_pmf(params)
        h, a = joint.shape
        tot = np.zeros(h + a - 1)
        for i in range(h):
            tot[i:i + a] += joint[i]
        tot /= tot.sum()
        return {int(t): float(p) for t, p in enumerate(tot) if p > self.priors.tail_tol}

    # ------------------------------------------------------------------ #
    # Situational parameterisation
    # ------------------------------------------------------------------ #
    def _expected_points(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        P = self.priors
        h = _num(ctx.get("home_expected"), None)
        a = _num(ctx.get("away_expected"), None)
        h = h if (h is not None and h > 0) else P.anchor_home
        a = a if (a is not None and a > 0) else P.anchor_away
        # Inputs on a full-game scale are mapped to a single quarter.
        if h + a > P.full_game_detection_ratio * (P.anchor_home + P.anchor_away):
            h /= P.game_quarters
            a /= P.game_quarters
        return h, a

    def _ratings(self, ctx: Dict[str, Any]) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {}
        for side in ("home", "away"):
            sub = ctx.get(side) if isinstance(ctx.get(side), dict) else {}
            for key in ("attack", "defense"):
                v = _num(ctx.get(f"{side}_{key}"), None)
                if v is None:
                    v = _num(sub.get(key, sub.get(f"{key}_rating", sub.get(f"{key}_strength"))), None)
                out[f"{side}_{key}"] = v if (v is not None and v > 0) else None
        return out

    def _exposures(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        """Scale-free Dixon-Coles style exposure: log(att_off / g) + log(def_opp / g).

        g is the geometric mean of all supplied ratings, so 1.0-centred
        multipliers and 100-centred ratings behave identically. The defense
        rating is read as defensive permissiveness (>g concedes more).
        """
        r = self._ratings(ctx)
        vals = [v for v in r.values() if v is not None]
        if not vals:
            return {"home": 0.0, "away": 0.0}
        lg = float(np.mean(np.log(vals)))

        def e(off: str, opp: str) -> float:
            parts = []
            if r[f"{off}_attack"] is not None:
                parts.append(math.log(r[f"{off}_attack"]) - lg)
            if r[f"{opp}_defense"] is not None:
                parts.append(math.log(r[f"{opp}_defense"]) - lg)
            return math.tanh(sum(parts)) if parts else 0.0

        return {"home": e("home", "away"), "away": e("away", "home")}

    def _packet_distributions(self, ctx: Dict[str, Any], side: str, exposure: float) -> Dict[str, Any]:
        P = self.priors
        g = lambda key, d: _prob(ctx.get(f"{side}_{key}"), d)  # noqa: E731

        tov = g("turnover_rate", P.turnover_rate)
        and1 = g("and_one_rate", P.and_one_rate)
        sf = _sigmoid(_logit(g("shooting_foul_rate", P.shooting_foul_rate)) + P.exposure_foul_gain * exposure)
        r3 = _sigmoid(_logit(g("three_rate", P.three_rate)) + P.exposure_three_gain * exposure)
        p3 = g("three_pct", P.three_pct)
        p2 = g("two_pct", P.two_pct)
        oreb = g("oreb_rate", P.oreb_rate)
        ft = g("ft_pct", P.ft_pct)

        dead = tov + sf + and1
        if dead >= 1.0:  # incoherent override -> revert to structural priors
            tov, sf, and1 = P.turnover_rate, P.shooting_foul_rate, P.and_one_rate
            dead = tov + sf + and1
        fga = 1.0 - dead

        # Field-goal chain with offensive-rebound renewal (geometric series)
        a3, a2 = r3 * p3, (1.0 - r3) * p2
        miss = 1.0 - a3 - a2
        denom = 1.0 - miss * oreb
        c3, c2, c0 = a3 / denom, a2 / denom, miss * (1.0 - oreb) / denom

        ft2 = stats.binom.pmf(np.arange(3), 2, ft)

        early = np.zeros(4)
        early[0] += tov + fga * c0
        early[:3] += sf * ft2
        early[2] += and1 * (1.0 - ft) + fga * c2
        early[3] += and1 * ft + fga * c3
        early /= early.sum()

        nsf = g("bonus_nonshooting_rate", P.bonus_nonshooting_rate)
        bonus = (1.0 - nsf) * early
        bonus[:3] += nsf * ft2
        bonus /= bonus.sum()

        heave = np.array([1.0 - P.heave_make, 0.0, 0.0, P.heave_make])
        end = P.heave_share * heave + (1.0 - P.heave_share) * early
        end /= end.sum()

        return {"early": early, "bonus": bonus, "end": end}

    def _bonus_bins(self, lam: float) -> Tuple[np.ndarray, np.ndarray]:
        """Quadrature over fraction of quarter in bonus: b = 1 - tau_5, tau_5 ~ Erlang(k, lam)."""
        P = self.priors
        dist = stats.gamma(a=P.bonus_foul_threshold, scale=1.0 / lam)
        edges = np.linspace(0.0, 1.0, P.bonus_quadrature_bins + 1)
        cdf = dist.cdf(edges)
        w = np.diff(cdf)
        b = 1.0 - 0.5 * (edges[:-1] + edges[1:])
        weights = np.append(w, 1.0 - cdf[-1])
        fracs = np.append(b, 0.0)
        return weights / weights.sum(), fracs

    def _opponent_foul_rate(self, ctx: Dict[str, Any], opp: str, exposure: float) -> float:
        P = self.priors
        per_game = _num(ctx.get(f"{opp}_fouls_per_game"), None)
        per_game = per_game if (per_game is not None and per_game > 0) else P.team_fouls_per_game
        return per_game / P.game_quarters * math.exp(P.exposure_foul_gain * exposure)

    def _pace_multiplier(self, ctx: Dict[str, Any]) -> float:
        P = self.priors
        f0 = math.exp(-P.fatigue_baseline_rest / P.fatigue_scale_days)
        fat = []
        for side in ("home", "away"):
            rest = _num(ctx.get(f"{side}_rest_days"), None)
            rest = rest if (rest is not None and rest >= 0) else P.fatigue_baseline_rest
            fat.append(math.exp(-rest / P.fatigue_scale_days))
        return math.exp(-P.fatigue_pace_gain * (float(np.mean(fat)) - f0))

    def _context_tempo(self, ctx: Dict[str, Any]) -> Optional[float]:
        for key in ("pace", "tempo", "expected_possessions", "possessions"):
            v = _num(ctx.get(key), None)
            if v is not None and v > 0:
                return v
        side_vals = [_num(ctx.get(f"{s}_{k}"), None) for s in ("home", "away") for k in ("pace", "tempo")]
        side_vals = [v for v in side_vals if v is not None and v > 0]
        return float(np.mean(side_vals)) if side_vals else None

    def _q2_starter_probs(self, ctx: Dict[str, Any]) -> Dict[str, float]:
        start = str(ctx.get("q2_first_possession", "")).lower()
        if start in ("home", "away"):
            return {start: 1.0}
        tip = str(ctx.get("opening_tip_winner", "")).lower()
        if tip in ("home", "away"):  # arrow: tip loser starts Q2
            return {"away" if tip == "home" else "home": 1.0}
        return {"home": 0.5, "away": 0.5}

    def _situational_parameters(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        P = self.priors
        mu_h, mu_a = self._expected_points(ctx)
        expo = self._exposures(ctx)

        packets = {
            "home": self._packet_distributions(ctx, "home", expo["home"]),
            "away": self._packet_distributions(ctx, "away", expo["away"]),
        }
        bins = {
            "home": self._bonus_bins(self._opponent_foul_rate(ctx, "away", expo["home"])),
            "away": self._bonus_bins(self._opponent_foul_rate(ctx, "home", expo["away"])),
        }

        # Untilted points per possession incl. expected bonus share -> anchor-implied tempo
        ppp = []
        for s in ("home", "away"):
            w, b = bins[s]
            eb = float(np.dot(w, b))
            ppp.append((1 - eb) * _mean(packets[s]["early"]) + eb * _mean(packets[s]["bonus"]))
        anchor_T = (mu_h + mu_a) / float(np.mean(ppp))

        tempo = self._context_tempo(ctx)
        if tempo is not None:
            mu_T = 2.0 * tempo / P.game_quarters
        else:
            league_T = 2.0 * P.tempo_per_team_game / P.game_quarters
            wgt = P.tempo_prior_weight
            mu_T = math.exp(wgt * math.log(league_T) + (1.0 - wgt) * math.log(anchor_T))
        mu_T *= self._pace_multiplier(ctx)

        return {
            "mu": {"home": mu_h, "away": mu_a},
            "packets": packets,
            "bins": bins,
            "mu_T": mu_T,
            "starter": self._q2_starter_probs(ctx),
            "exposure": expo,
        }

    # ------------------------------------------------------------------ #
    # Joint PMF construction
    # ------------------------------------------------------------------ #
    def _possession_states(self, mu_T: float, starter: Dict[str, float]) -> List[Tuple[float, Dict[str, int], Optional[str]]]:
        P = self.priors
        q = P.possession_retention
        n = max(1, int(round(mu_T / q)))
        while mu_T / n >= 1.0:
            n += 1
        q_eff = mu_T / n
        pT = stats.binom.pmf(np.arange(n + 1), n, q_eff)

        states = []
        for T, p in enumerate(pT):
            if p < P.tail_tol:
                continue
            for s, ps in starter.items():
                other = "away" if s == "home" else "home"
                counts = {s: (T + 1) // 2, other: T // 2}
                last = None if T == 0 else (s if T % 2 == 1 else other)
                states.append((p * ps, counts, last))
        tot = sum(st[0] for st in states)
        return [(p / tot, c, l) for p, c, l in states]

    @staticmethod
    def _bonus_split(r: int, weights: np.ndarray, fracs: np.ndarray) -> Dict[int, float]:
        split: Dict[int, float] = {}
        for w, b in zip(weights, fracs):
            L = int(round(b * r))
            split[L] = split.get(L, 0.0) + float(w)
        return split

    def _solve_tilt(self, side: str, params: Dict[str, Any], counts: Tuple[float, float, float]) -> float:
        pk = params["packets"][side]
        target = params["mu"][side]
        e_early, e_bonus, e_end = counts

        def f(theta: float) -> float:
            return (
                e_early * _mean(_tilt(pk["early"], theta))
                + e_bonus * _mean(_tilt(pk["bonus"], theta))
                + e_end * _mean(_tilt(pk["end"], theta))
                - target
            )

        lo, hi = -1.0, 1.0
        for _ in range(60):
            if f(lo) <= 0:
                break
            lo *= 2.0
        for _ in range(60):
            if f(hi) >= 0:
                break
            hi *= 2.0
        return float(optimize.brentq(f, lo, hi, xtol=1e-12))

    def _joint_pmf(self, params: Dict[str, Any]) -> np.ndarray:
        P = self.priors
        mu_T = params["mu_T"]

        # Guarantee feasibility: E[S] must be < 3 * E[N] for each team.
        for _ in range(8):
            states = self._possession_states(mu_T, params["starter"])
            exp_n = {s: sum(p * c[s] for p, c, _ in states) for s in ("home", "away")}
            ratio = max(params["mu"][s] / max(exp_n[s], 1e-9) for s in exp_n)
            if ratio < 2.5:
                break
            mu_T *= ratio / 2.0
        params["mu_T_effective"] = mu_T

        # Expected counts of each packet type per team (theta-independent)
        team_counts: Dict[str, Tuple[float, float, float]] = {}
        for side in ("home", "away"):
            w, b = params["bins"][side]
            e_early = e_bonus = e_end = 0.0
            for p, c, last in states:
                end_flag = 1 if last == side else 0
                r = c[side] - end_flag
                split = self._bonus_split(r, w, b)
                Lbar = sum(L * wt for L, wt in split.items())
                e_early += p * (r - Lbar)
                e_bonus += p * Lbar
                e_end += p * end_flag
            team_counts[side] = (e_early, e_bonus, e_end)

        thetas = {s: self._solve_tilt(s, params, team_counts[s]) for s in ("home", "away")}
        params["theta"] = thetas

        # Build per-team conditional PMFs
        team_pmf: Dict[str, Dict[Tuple[int, int], np.ndarray]] = {"home": {}, "away": {}}
        max_n = {s: max(c[s] for _, c, _ in states) for s in ("home", "away")}
        for side in ("home", "away"):
            pk = {k: _tilt(v, thetas[side]) for k, v in params["packets"][side].items()}
            w, b = params["bins"][side]
            pe, pb = [np.array([1.0])], [np.array([1.0])]
            for _ in range(max_n[side]):
                pe.append(np.convolve(pe[-1], pk["early"]))
                pb.append(np.convolve(pb[-1], pk["bonus"]))
            size = 3 * max_n[side] + 1
            for _, c, last in states:
                end_flag = 1 if last == side else 0
                key = (c[side], end_flag)
                if key in team_pmf[side]:
                    continue
                r = c[side] - end_flag
                acc = np.zeros(size)
                for L, wt in self._bonus_split(r, w, b).items():
                    v = np.convolve(pe[r - L], pb[L])
                    acc[: v.size] += wt * v
                if end_flag:
                    v = np.convolve(acc, pk["end"])[:size]
                    acc = np.zeros(size)
                    acc[: v.size] = v
                team_pmf[side][key] = acc

        joint = np.zeros((3 * max_n["home"] + 1, 3 * max_n["away"] + 1))
        for p, c, last in states:
            ph = team_pmf["home"][(c["home"], 1 if last == "home" else 0)]
            pa = team_pmf["away"][(c["away"], 1 if last == "away" else 0)]
            joint += p * np.outer(ph, pa)

        joint[joint < P.tail_tol] = 0.0
        return joint / joint.sum()

    # ------------------------------------------------------------------ #
    # Diagnostics & emission
    # ------------------------------------------------------------------ #
    def _record_diagnostics(self, joint: np.ndarray, params: Dict[str, Any]) -> None:
        h = np.arange(joint.shape[0], dtype=float)
        a = np.arange(joint.shape[1], dtype=float)
        ph, pa = joint.sum(axis=1), joint.sum(axis=0)
        mh, ma = float(h @ ph), float(a @ pa)
        vh = float((h - mh) ** 2 @ ph)
        va = float((a - ma) ** 2 @ pa)
        cov = float((h - mh) @ joint @ (a - ma))
        var_t = vh + va + 2 * cov
        self.last_diagnostics = {
            "home_mean": mh,
            "away_mean": ma,
            "total_mean": mh + ma,
            "total_var": var_t,
            "dispersion_index": var_t / (mh + ma) if mh + ma > 0 else float("nan"),
            "home_away_corr": cov / math.sqrt(vh * va) if vh > 0 and va > 0 else 0.0,
            "possessions_mean": params.get("mu_T_effective", params["mu_T"]),
            "theta": params.get("theta"),
            "exposure": params.get("exposure"),
        }

    def _emit(self, joint: np.ndarray) -> "DiscreteDistribution":
        tol = self.priors.tail_tol
        idx = np.argwhere(joint > tol)
        scores = {(int(i), int(j)): float(joint[i, j]) for i, j in idx}
        z = sum(scores.values())
        scores = {k: v / z for k, v in scores.items()}

        builder = getattr(DiscreteDistribution, "from_scores", None)
        if builder is None:
            raise RuntimeError("DiscreteDistribution.from_scores is unavailable in this runtime.")
        last_err: Optional[Exception] = None
        for payload in (scores, joint / joint.sum(), (joint / joint.sum()).tolist()):
            try:
                return builder(payload)
            except (TypeError, ValueError, AttributeError) as err:
                last_err = err
        raise RuntimeError(f"Unable to construct DiscreteDistribution from score lattice: {last_err}")


# Engine-discoverable aliases
CandidateModel = NCAAWQuarter2ExactTotalModel
Model = NCAAWQuarter2ExactTotalModel