"""
Candidate model: NCAAM | quarter_4 | three_way

NOTE ON SEGMENT DEFINITION
NCAAM men's basketball is played in two 20-minute halves, not quarters. This model
assumes the feed's "quarter_4" means the final ~10 minutes of regulation, with
overtime excluded. Under that definition, a tie on the segment is a real settlement
outcome. If the feed defines the segment differently, set
game_context['segment_minutes'] and game_context['game_minutes']. Confirm settlement
rules before deploying.

ARCHITECTURE (shared-possession compound model)
  N        ~ count of possession pairs in the segment. Clock-bounded, so it may be
             under- or over-dispersed: Binomial / Poisson / NegBin, chosen by the
             variance ratio.
  X_h, X_a ~ team-specific categorical points-per-possession on {0,1,2,3,4}. Each is
             built from shooting mechanics (2P%, 3P%, 3PA share, TOV, FT trips,
             OREB, and-ones). Each is then minimally exponentially tilted so the
             expected points hit the situational anchors (home/away_expected).
  Margin   = sum over N pairs of (X_h - X_a), plus one parity possession to either
             side. Computed by exact lattice convolution and mixed over N, which
             gives exact draw mass.
  Fouling  regime: a continuous probability that the game is close late. It moves
             leader possessions toward free-throw trips, moves the trailer toward
             3PA, and inflates possession-count dispersion.
  Home edge enters only through the per-possession efficiency targets. There is no
  additive margin constant.

Every structural number lives in PRIORS as a documented domain prior. Each can be
overridden through game_context. Nothing is clamped to fixed odds or probability
caps.
"""

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import optimize, stats

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except ImportError:  # pragma: no cover
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution  # type: ignore
    except ImportError:  # pre-loaded into execution scope
        DiscreteDistribution = globals()["DiscreteDistribution"]  # type: ignore


class NCAAMQuarter4ThreeWayModel(BaseCandidateModel):
    """Shared-possession compound lattice model for NCAAM final-segment 3-way markets."""

    # Domain priors (NCAA D-I long-run mechanics). All overridable via game_context.
    PRIORS: Dict[str, float] = {
        # Empirical segment anchors from the supplied moments
        "home_expected": 20.00,
        "away_expected": 18.75,
        # Segment geometry
        "segment_minutes": 10.0,
        "game_minutes": 40.0,
        # Per-team possession mechanics
        "tov_rate": 0.17,          # turnovers per possession
        "ft_trip_rate": 0.11,      # non-and-one two-shot FT trips per possession
        "three_share": 0.38,       # 3PA / FGA
        "p2": 0.51,                # 2P%
        "p3": 0.34,                # 3P%
        "ft_pct": 0.71,            # FT%
        "orb_rate": 0.29,          # offensive rebound rate on missed FGA
        "and_one_rate_2": 0.06,    # P(and-one | made 2)
        "and_one_rate_3": 0.01,    # P(and-one | made 3)
        # Late-game fouling regime
        "game_margin_sd": 11.0,    # full-game margin SD, NCAAM
        "foul_window": 8.0,        # |margin| band inside which intentional fouling arises
        "intentional_foul_mass": 0.12,   # share of leader FGA possessions converted to FT trips
        "trailer_three_shift": 0.10,     # share of trailer 2PA converted to 3PA
        "foul_dispersion_gain": 1.5,     # possession-count variance inflation under fouling
        # Possession-count dispersion (Var/Mean); supplied index = 1.000
        "dispersion_index": 1.0,
        # Situational shifts: relative split only, total segment points preserved
        "rating_shrink": 0.25,     # anchors already embed ratings; avoid double counting
        "rest_coef": 0.015,        # late-game fatigue per log-day of rest differential
        # Parity possession
        "home_first_possession_prob": 0.5,
    }

    _K = np.arange(5, dtype=float)
    _TAIL = 1e-12

    def __init__(
        self,
        model_id: str = "codex_synth_ncaam_quarter_4_1107_222636",
        version: str = "1.0.0",
        league: str = "NCAAM",
        scope: str = "quarter_4",
        family: str = "three_way",
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id, version=version, league=league,
                scope=scope, family=family, **kwargs,
            )
        except TypeError:
            super().__init__()
            self.model_id, self.version = model_id, version
            self.league, self.scope, self.family = league, scope, family
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _finite(x: Any) -> Optional[float]:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    def _get(self, ctx: Dict[str, Any], key: str, side: Optional[str] = None,
             default: Optional[float] = None) -> Optional[float]:
        """Look up flat 'side_key', nested ctx[side][key], then plain key, then PRIORS."""
        candidates = []
        if side is not None:
            candidates.append(ctx.get(f"{side}_{key}"))
            nested = ctx.get(side)
            if isinstance(nested, dict):
                candidates.append(nested.get(key))
        else:
            candidates.append(ctx.get(key))
        for c in candidates:
            v = self._finite(c)
            if v is not None:
                return v
        if default is not None:
            return default
        return self.PRIORS.get(key)

    def _param(self, ctx: Dict[str, Any], key: str, side: str) -> float:
        v = self._get(ctx, key, side=side, default=None)
        if v is None:
            v = self._get(ctx, key)
        return float(v)

    # --------------------------------------------------- per-possession template
    @staticmethod
    def _possession_pmf(tov: float, ft_trip: float, s3: float, p2: float, p3: float,
                        ftp: float, orb: float, a2: float, a3: float) -> np.ndarray:
        """Exact points-per-possession pmf on {0..4} from shooting mechanics."""
        # FGA branch: one shot attempt
        make = np.zeros(5)
        make[2] += (1.0 - s3) * p2 * (1.0 - a2 * ftp)
        make[3] += (1.0 - s3) * p2 * a2 * ftp
        make[3] += s3 * p3 * (1.0 - a3 * ftp)
        make[4] += s3 * p3 * a3 * ftp
        miss = (1.0 - s3) * (1.0 - p2) + s3 * (1.0 - p3)
        # A miss is offensively rebounded with prob orb, which renews the attempt
        # (geometric closure).
        fga = make.copy()
        fga[0] += miss * (1.0 - orb)
        fga /= (1.0 - miss * orb)

        ft = np.array([(1 - ftp) ** 2, 2 * ftp * (1 - ftp), ftp ** 2, 0.0, 0.0])
        to = np.array([1.0, 0.0, 0.0, 0.0, 0.0])

        p_fga = max(1.0 - tov - ft_trip, 0.0)
        pmf = tov * to + ft_trip * ft + p_fga * fga
        return pmf / pmf.sum()

    def _tilt(self, pmf: np.ndarray, target: float) -> Tuple[np.ndarray, float]:
        """Minimum-KL (exponential) tilt of pmf so that E[X] = target."""
        k = self._K
        logp = np.where(pmf > 0, np.log(np.where(pmf > 0, pmf, 1.0)), -np.inf)

        def tilted(t: float) -> np.ndarray:
            z = logp + t * k
            z = z - np.max(z)
            w = np.exp(z)
            return w / w.sum()

        def g(t: float) -> float:
            return float(np.dot(tilted(t), k) - target)

        lo, hi = -60.0, 60.0
        m_lo, m_hi = g(lo) + target, g(hi) + target
        # Project the target onto the attainable mean interval of the support.
        tgt = min(max(target, m_lo), m_hi)
        if abs(tgt - target) > 0:
            target = tgt
        if abs(g(lo)) < 1e-14:
            return tilted(lo), lo
        if abs(g(hi)) < 1e-14:
            return tilted(hi), hi
        t_star = optimize.brentq(g, lo, hi, xtol=1e-12, maxiter=500)
        return tilted(t_star), t_star

    # --------------------------------------------------------- fouling regime
    def _foul_regime(self, ctx: Dict[str, Any], seg_diff: float,
                     seg_min: float, game_min: float) -> Tuple[float, float]:
        """Return (P(close late game), P(home leads | situation))."""
        sd_game = self._get(ctx, "game_margin_sd")
        window = self._get(ctx, "foul_window")
        m0 = None
        for key in ("margin_at_entry", "score_diff_at_entry", "current_margin"):
            m0 = self._finite(ctx.get(key))
            if m0 is not None:
                break
        if m0 is not None:
            # In-play: condition on the score at segment entry.
            mu = m0 + seg_diff
            sigma = sd_game * math.sqrt(seg_min / game_min)
        else:
            mu = None
            for key in ("expected_margin", "full_game_expected_margin"):
                mu = self._finite(ctx.get(key))
                if mu is not None:
                    break
            if mu is None:
                spread = self._finite(ctx.get("home_spread"))
                mu = -spread if spread is not None else seg_diff * game_min / seg_min
            sigma = sd_game
        sigma = max(sigma, 1e-9)
        p_close = stats.norm.cdf((window - mu) / sigma) - stats.norm.cdf((-window - mu) / sigma)
        p_home_lead = stats.norm.cdf(mu / sigma)
        return float(p_close), float(p_home_lead)

    # ------------------------------------------------------ possession count
    @staticmethod
    def _count_dist(mu: float, ratio: float):
        mu = max(mu, 1e-9)
        if abs(ratio - 1.0) < 1e-9:
            return stats.poisson(mu)
        if ratio > 1.0:
            r = mu / (ratio - 1.0)
            return stats.nbinom(r, 1.0 / ratio)
        # Under-dispersed (clock-bounded): Binomial with Var/Mean = ratio.
        p = 1.0 - ratio
        n = max(int(math.ceil(mu / p)), 1)
        return stats.binom(n, min(mu / n, 1.0))

    # ------------------------------------------------------- situational split
    def _relative_shift(self, ctx: Dict[str, Any]) -> float:
        delta = 0.0
        att_h = self._get(ctx, "attack", "home", default=float("nan"))
        def_h = self._get(ctx, "defense", "home", default=float("nan"))
        att_a = self._get(ctx, "attack", "away", default=float("nan"))
        def_a = self._get(ctx, "defense", "away", default=float("nan"))
        vals = [att_h, def_h, att_a, def_a]
        if all(v is not None and math.isfinite(v) and v > 0 for v in vals):
            # Multiplicative exposure: home scoring ∝ att_h * def_a.
            rel = math.log(att_h * def_a) - math.log(att_a * def_h)
            delta += self._get(ctx, "rating_shrink") * rel
        rh = self._finite(ctx.get("home_rest_days"))
        ra = self._finite(ctx.get("away_rest_days"))
        if rh is not None and ra is not None and rh >= 0 and ra >= 0:
            delta += self._get(ctx, "rest_coef") * (math.log1p(rh) - math.log1p(ra))
        return delta

    # ------------------------------------------------------------ prediction
    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        ctx = game_context or {}

        T_h = self._get(ctx, "home_expected")
        T_a = self._get(ctx, "away_expected")
        if T_h is None or T_h <= 0:
            T_h = self.PRIORS["home_expected"]
        if T_a is None or T_a <= 0:
            T_a = self.PRIORS["away_expected"]
        total = T_h + T_a

        seg_min = self._get(ctx, "segment_minutes")
        game_min = self._get(ctx, "game_minutes")

        # Relative situational split. The total segment expectation is preserved.
        delta = self._relative_shift(ctx)
        th, ta = T_h * math.exp(delta / 2.0), T_a * math.exp(-delta / 2.0)
        scale = total / (th + ta)
        T_h, T_a = th * scale, ta * scale

        # Fouling regime
        p_close, p_hl = self._foul_regime(ctx, T_h - T_a, seg_min, game_min)
        fmass = self._get(ctx, "intentional_foul_mass")
        tshift = self._get(ctx, "trailer_three_shift")

        templates = {}
        for side, p_lead in (("home", p_hl), ("away", 1.0 - p_hl)):
            tov = self._param(ctx, "tov_rate", side)
            ft0 = self._param(ctx, "ft_trip_rate", side)
            s30 = self._param(ctx, "three_share", side)
            # Leader is fouled: FGA possessions convert into FT trips.
            ft = ft0 + p_close * fmass * p_lead * max(1.0 - tov - ft0, 0.0)
            # Trailer chases: 2PA convert into 3PA.
            s3 = s30 + p_close * tshift * (1.0 - p_lead) * (1.0 - s30)
            templates[side] = self._possession_pmf(
                tov=tov, ft_trip=ft, s3=s3,
                p2=self._param(ctx, "p2", side), p3=self._param(ctx, "p3", side),
                ftp=self._param(ctx, "ft_pct", side), orb=self._param(ctx, "orb_rate", side),
                a2=self._param(ctx, "and_one_rate_2", side),
                a3=self._param(ctx, "and_one_rate_3", side),
            )

        # Possession pairs: from pace if supplied, otherwise inferred from anchors
        # and mechanics.
        pace = self._finite(ctx.get("pace")) or self._finite(ctx.get("tempo"))
        if pace is not None and pace > 0:
            mu_pairs = max(pace * seg_min / game_min - 0.5, 1e-9)
        else:
            ppp0 = float(np.dot(templates["home"], self._K) + np.dot(templates["away"], self._K))
            mu_pairs = max(total / ppp0 - 0.5, 1e-9)

        # Per-team PPP targets. Each team averages mu_pairs + 0.5 possessions.
        cat_h, t_h = self._tilt(templates["home"], T_h / (mu_pairs + 0.5))
        cat_a, t_a = self._tilt(templates["away"], T_a / (mu_pairs + 0.5))

        disp = self._get(ctx, "dispersion_index")
        ratio = disp * (1.0 + p_close * self._get(ctx, "foul_dispersion_gain"))
        n_dist = self._count_dist(mu_pairs, ratio)
        n_max = int(n_dist.ppf(1.0 - self._TAIL))
        n_max = max(n_max, 1)
        w = n_dist.pmf(np.arange(n_max + 1))
        w = w / w.sum()

        # Exact margin lattice mixed over the shared possession count.
        d = np.convolve(cat_h, cat_a[::-1])  # index i -> margin i - 4
        off = 4 * n_max
        mix = np.zeros(8 * n_max + 1)
        cur = np.array([1.0])
        for n in range(n_max + 1):
            if n > 0:
                cur = np.convolve(cur, d)
            start = off - 4 * n
            mix[start:start + cur.size] += w[n] * cur

        # Parity possession: one extra trip for whichever side opens the segment.
        p_first = self._get(ctx, "home_first_possession_prob")
        home_ext = np.concatenate([np.zeros(4), np.convolve(mix, cat_h)])
        away_ext = np.concatenate([np.convolve(mix, cat_a[::-1]), np.zeros(4)])
        final = p_first * home_ext + (1.0 - p_first) * away_ext
        z = off + 4
        final = np.clip(final, 0.0, None)
        final /= final.sum()

        p_away = float(final[:z].sum())
        p_draw = float(final[z])
        p_home = float(final[z + 1:].sum())
        s = p_home + p_draw + p_away
        p_home, p_draw, p_away = p_home / s, p_draw / s, p_away / s

        margins = np.arange(final.size) - z
        e_m = float(np.dot(final, margins))
        self.last_diagnostics = {
            "home_expected_adj": T_h, "away_expected_adj": T_a,
            "mu_possession_pairs": mu_pairs, "possession_var_ratio": ratio,
            "p_close_late": p_close, "p_home_leads": p_hl,
            "home_ppp": float(np.dot(cat_h, self._K)), "away_ppp": float(np.dot(cat_a, self._K)),
            "tilt_home": t_h, "tilt_away": t_a,
            "margin_mean": e_m,
            "margin_sd": float(math.sqrt(max(np.dot(final, (margins - e_m) ** 2), 0.0))),
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        }

        probs = {"home": p_home, "draw": p_draw, "away": p_away}
        try:
            return DiscreteDistribution(probabilities=probs)
        except TypeError:
            return DiscreteDistribution.from_probabilities(p_home, p_draw, p_away)