import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import binom

from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice

try:
    from betting_engine.sandbox_models.base import BaseCandidateModel, DiscreteDistribution, build_universal_score_lattice
except Exception:  # pragma: no cover - resolved from execution scope
    try:
        from betting_engine.sandbox_models.distributions import DiscreteDistribution
    except Exception:
        DiscreteDistribution = globals().get("DiscreteDistribution")


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    return math.log(p) - math.log1p(-p)


def _softplus(x: float) -> float:
    return math.log1p(math.exp(-abs(x))) + max(x, 0.0)


def _to_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# Each event: (points to home, points to away, probability)
Event = Tuple[int, int, float]


class NFLQuarter3CompoundPossessionModel(BaseCandidateModel):
    """
    NFL 3rd-quarter winner/total model built as a compound possession process.

    Structure
    ---------
    * Total Q3 drive starts D follow a clock-constrained (under-dispersed)
      shifted binomial whose mean responds smoothly to pace.
    * Possessions alternate starting with the second-half receiver, so the
      receiver owns ceil(D/2) drives and the kicker floor(D/2). The receiver is
      taken from context if known, otherwise mixed 50/50.
    * The final drive of the quarter is truncated: only a fraction of it is
      played in Q3, so its scoring probabilities are scaled down. This is the
      main mechanical source of empty/low-scoring quarter mass.
    * Each drive yields a lattice outcome: TD (6 + XP / 2pt -> 6/7/8),
      FG (3, make prob driven by wind/dome/cold), safety (+2 to defense),
      defensive/return TD (+6/7 to defense), or empty.
    * Per-drive offensive expected points are solved in closed form so that
      E[S_home] and E[S_away] reproduce the adjusted situational means exactly.
    * The joint score matrix is the exact discrete convolution over all
      possession scenarios; winner (incl. tie) and total ladders read off it.

    All structural priors are named, documented, and overridable via `priors`.
    """

    DEFAULT_PRIORS: Dict[str, float] = {
        # Empirical Q3 anchors for this segment
        "anchor_home": 6.12,
        "anchor_away": 5.38,
        # If supplied means look like full-game means, convert with Q3 share
        "q3_share_of_game": 0.245,
        "full_game_detect_ratio": 2.0,
        # Drive-count process: D = d_min + Binomial(d_span, q)
        "mean_total_drives": 5.3,
        "d_min": 3,
        "d_span": 6,
        "pace_elasticity": 1.0,
        # Fraction of the quarter-ending drive played inside Q3 (uniform cut)
        "trunc_fraction": 0.5,
        # Conversions after TD
        "p_xp_make": 0.955,
        "p_two_attempt": 0.09,
        "p_two_make": 0.48,
        # Field goal make probability (logit model)
        "fg_make_base": 0.85,
        "wind_pivot_mph": 10.0,
        "wind_fg_logit_slope": 0.06,
        "dome_fg_logit_bonus": 0.25,
        "cold_pivot_f": 40.0,
        "cold_fg_logit_slope": 0.02,
        # TD share among scoring attempts
        "td_share_base": 0.57,
        "td_share_ref_ppd": 2.0,
        "td_share_elasticity": 1.0,
        "wind_td_share_logit_slope": 0.015,
        # Defensive scoring per opponent drive
        "p_safety": 0.006,
        "p_def_td": 0.012,
        # Situational adjustments (partially pooled; means usually pre-adjusted)
        "exposure_weight": 0.25,
        "rest_beta": 0.03,
        "rest_scale_days": 7.0,
        # Numerical grid (max points per team per quarter)
        "grid_max_points": 60,
    }

    def __init__(
        self,
        model_id: str = "codex_synth_nfl_quarter_3_1107_150758",
        version: str = "1.0.0",
        league: str = "NFL",
        scope: str = "quarter_3",
        family: str = "winner_total",
        priors: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        try:
            super().__init__(
                model_id=model_id,
                version=version,
                league=league,
                scope=scope,
                family=family,
                **kwargs,
            )
        except TypeError:
            super().__init__(model_id, version, league, scope, family)
        self.model_id = model_id
        self.version = version
        self.league = league
        self.scope = scope
        self.family = family
        self.priors: Dict[str, float] = dict(self.DEFAULT_PRIORS)
        if priors:
            self.priors.update(priors)
        self.last_diagnostics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Context parsing
    # ------------------------------------------------------------------ #
    def _team_rating(self, ctx: Dict[str, Any], side: str, kind: str) -> Optional[float]:
        team = ctx.get(side)
        if isinstance(team, dict):
            for key in (kind, f"{kind}_rating", f"{kind}_strength"):
                v = _to_float(team.get(key))
                if v is not None:
                    return v
        for key in (f"{side}_{kind}", f"{side}_{kind}_rating"):
            v = _to_float(ctx.get(key))
            if v is not None:
                return v
        return None

    def _exposure_log(self, ctx: Dict[str, Any], attacker: str, defender: str) -> float:
        """Log scoring multiplier from attack vs. opposing defense.

        Defense is interpreted as a conceding index (1 = league average,
        >1 = leaky). Positive indices are treated multiplicatively; any
        non-positive value implies the ratings are already on a log scale.
        """
        att = self._team_rating(ctx, attacker, "attack")
        dfn = self._team_rating(ctx, defender, "defense")
        vals = [v for v in (att, dfn) if v is not None]
        if not vals:
            return 0.0
        log_scale = any(v <= 0 for v in vals)

        def to_log(v: Optional[float]) -> float:
            if v is None:
                return 0.0
            return v if log_scale else math.log(v)

        return self.priors["exposure_weight"] * (to_log(att) + to_log(dfn))

    def _rest_log(self, ctx: Dict[str, Any]) -> float:
        rh = _to_float(ctx.get("home_rest_days"))
        ra = _to_float(ctx.get("away_rest_days"))
        if rh is None or ra is None:
            return 0.0
        return self.priors["rest_beta"] * math.tanh((rh - ra) / self.priors["rest_scale_days"])

    def _pace_log(self, ctx: Dict[str, Any]) -> float:
        vals: List[float] = []
        for key in ("pace", "tempo", "home_pace", "away_pace", "home_tempo", "away_tempo"):
            v = _to_float(ctx.get(key))
            if v is not None and v > 0:
                vals.append(v)
        for side in ("home", "away"):
            team = ctx.get(side)
            if isinstance(team, dict):
                for key in ("pace", "tempo"):
                    v = _to_float(team.get(key))
                    if v is not None and v > 0:
                        vals.append(v)
        if not vals:
            return 0.0
        # Relative indices (~1.0) used directly; raw plays/game normalized by
        # their own geometric mean against the league reference of ~63.5 plays.
        logs = []
        for v in vals:
            logs.append(math.log(v) if v < 5.0 else math.log(v / 63.5))
        return float(np.mean(logs))

    def _receiver_prob_home(self, ctx: Dict[str, Any]) -> float:
        for key in ("q3_receiver", "second_half_receiver", "second_half_receiving_team"):
            v = ctx.get(key)
            if isinstance(v, str):
                if v.lower().startswith("h"):
                    return 1.0
                if v.lower().startswith("a"):
                    return 0.0
        v = ctx.get("opening_kickoff_receiver")
        if isinstance(v, str):
            # Team that received the opening kickoff kicks off the second half
            if v.lower().startswith("h"):
                return 0.0
            if v.lower().startswith("a"):
                return 1.0
        return 0.5

    def _weather(self, ctx: Dict[str, Any]) -> Tuple[float, bool, Optional[float]]:
        weather = ctx.get("weather") if isinstance(ctx.get("weather"), dict) else {}
        venue = ctx.get("venue") if isinstance(ctx.get("venue"), dict) else {}

        def pick(key: str) -> Any:
            for src in (ctx, weather, venue):
                if key in src:
                    return src[key]
            return None

        dome_raw = pick("is_dome")
        is_dome = bool(dome_raw) if dome_raw is not None else False
        wind = _to_float(pick("wind_speed"))
        temp = _to_float(pick("temp"))
        if temp is None:
            temp = _to_float(pick("temperature"))
        if is_dome:
            wind, temp = 0.0, None
        return (wind if wind is not None else 0.0), is_dome, temp

    # ------------------------------------------------------------------ #
    # Mean construction
    # ------------------------------------------------------------------ #
    def _situational_means(self, ctx: Dict[str, Any]) -> Tuple[float, float]:
        P = self.priors
        lh = _to_float(ctx.get("home_expected"))
        la = _to_float(ctx.get("away_expected"))
        if lh is None or lh <= 0:
            lh = P["anchor_home"]
        if la is None or la <= 0:
            la = P["anchor_away"]
        anchor_sum = P["anchor_home"] + P["anchor_away"]
        if (lh + la) > P["full_game_detect_ratio"] * anchor_sum:
            lh *= P["q3_share_of_game"]
            la *= P["q3_share_of_game"]
        rest = self._rest_log(ctx)
        lh *= math.exp(self._exposure_log(ctx, "home", "away") + rest)
        la *= math.exp(self._exposure_log(ctx, "away", "home") - rest)
        return lh, la

    # ------------------------------------------------------------------ #
    # Drive mechanics
    # ------------------------------------------------------------------ #
    def _fg_make(self, wind: float, is_dome: bool, temp: Optional[float]) -> float:
        P = self.priors
        z = _logit(P["fg_make_base"])
        z -= P["wind_fg_logit_slope"] * _softplus(wind - P["wind_pivot_mph"])
        if is_dome:
            z += P["dome_fg_logit_bonus"]
        if temp is not None:
            z -= P["cold_fg_logit_slope"] * _softplus(P["cold_pivot_f"] - temp)
        return _sigmoid(z)

    def _td_conversion_pmf(self) -> Dict[int, float]:
        P = self.priors
        p2 = P["p_two_attempt"]
        p7 = (1.0 - p2) * P["p_xp_make"]
        p8 = p2 * P["p_two_make"]
        return {6: 1.0 - p7 - p8, 7: p7, 8: p8}

    def _td_share(self, m: float, wind: float) -> float:
        P = self.priors
        z = _logit(P["td_share_base"])
        z += P["td_share_elasticity"] * math.log(max(m, 1e-9) / P["td_share_ref_ppd"])
        z += P["wind_td_share_logit_slope"] * _softplus(wind - P["wind_pivot_mph"])
        return _sigmoid(z)

    def _drive_probs(self, m: float, wind: float, fg_make: float) -> Tuple[float, float, bool]:
        """Return (p_td, p_fga, feasible) for offensive points-per-drive m."""
        conv = self._td_conversion_pmf()
        td_val = sum(k * v for k, v in conv.items())
        fg_val = 3.0 * fg_make
        s = self._td_share(m, wind)
        p_att = m / (s * td_val + (1.0 - s) * fg_val)
        p_td, p_fga = p_att * s, p_att * (1.0 - s)
        room = 1.0 - self.priors["p_safety"] - self.priors["p_def_td"]
        return p_td, p_fga, (p_td + p_fga) < room

    def _events(self, offense: str, p_td: float, p_fga: float, fg_make: float,
                scale: float) -> List[Event]:
        P = self.priors
        conv = self._td_conversion_pmf()
        raw: List[Tuple[int, int, float]] = []  # (offense pts, defense pts, prob)
        for pts, pc in conv.items():
            raw.append((pts, 0, scale * p_td * pc))
        raw.append((3, 0, scale * p_fga * fg_make))
        raw.append((0, 2, scale * P["p_safety"]))
        for pts, pc in conv.items():
            raw.append((0, pts, scale * P["p_def_td"] * pc))
        p_empty = 1.0 - sum(r[2] for r in raw)
        raw.append((0, 0, p_empty))
        if offense == "h":
            return [(o, d, p) for o, d, p in raw if p > 0]
        return [(d, o, p) for o, d, p in raw if p > 0]

    def _defensive_points_per_drive(self) -> float:
        conv = self._td_conversion_pmf()
        td_val = sum(k * v for k, v in conv.items())
        return 2.0 * self.priors["p_safety"] + td_val * self.priors["p_def_td"]

    # ------------------------------------------------------------------ #
    # Possession scenarios
    # ------------------------------------------------------------------ #
    def _scenarios(self, q: float, p_home_recv: float) -> List[Tuple[float, int, int, str]]:
        d_min = int(self.priors["d_min"])
        span = int(self.priors["d_span"])
        out: List[Tuple[float, int, int, str]] = []
        for k in range(span + 1):
            pD = float(binom.pmf(k, span, q))
            if pD <= 0:
                continue
            D = d_min + k
            for recv, wr in (("h", p_home_recv), ("a", 1.0 - p_home_recv)):
                if wr <= 0:
                    continue
                other = "a" if recv == "h" else "h"
                n_recv, n_other = (D + 1) // 2, D // 2
                n_h = n_recv if recv == "h" else n_other
                n_a = n_recv if recv == "a" else n_other
                last = recv if D % 2 == 1 else other
                out.append((pD * wr, n_h, n_a, last))
        return out

    def _effective_drives(self, scen: List[Tuple[float, int, int, str]]) -> Tuple[float, float]:
        tau = self.priors["trunc_fraction"]
        eh = ea = 0.0
        for w, n_h, n_a, last in scen:
            eh += w * (n_h - (1.0 - tau) * (last == "h"))
            ea += w * (n_a - (1.0 - tau) * (last == "a"))
        return eh, ea

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #
    def _calibrate(self, lam_h: float, lam_a: float, pace_log: float, p_home_recv: float,
                   wind: float, fg_make: float) -> Dict[str, Any]:
        P = self.priors
        d_min, span = P["d_min"], P["d_span"]
        q0 = (P["mean_total_drives"] - d_min) / span
        z = _logit(q0) + P["pace_elasticity"] * pace_log
        delta = self._defensive_points_per_drive()

        def smooth_pos(x: float, scale: float) -> float:
            # smooth positive floor for the offensive component
            return scale * _softplus(x / scale)

        result: Dict[str, Any] = {}
        for _ in range(60):
            q = _sigmoid(z)
            scen = self._scenarios(q, p_home_recv)
            eh, ea = self._effective_drives(scen)
            m_h = smooth_pos(lam_h - delta * ea, 0.05) / eh
            m_a = smooth_pos(lam_a - delta * eh, 0.05) / ea
            td_h, fga_h, ok_h = self._drive_probs(m_h, wind, fg_make)
            td_a, fga_a, ok_a = self._drive_probs(m_a, wind, fg_make)
            result = dict(q=q, scen=scen, eh=eh, ea=ea, m_h=m_h, m_a=m_a,
                          td_h=td_h, fga_h=fga_h, td_a=td_a, fga_a=fga_a,
                          feasible=ok_h and ok_a)
            if ok_h and ok_a:
                break
            # Extreme scoring environment: more drives are needed to carry mass
            z += 0.15
        if not result["feasible"]:
            room = 1.0 - P["p_safety"] - P["p_def_td"]
            for side in ("h", "a"):
                tot = result[f"td_{side}"] + result[f"fga_{side}"]
                if tot >= room:
                    f = (room * (1.0 - 1e-6)) / tot
                    result[f"td_{side}"] *= f
                    result[f"fga_{side}"] *= f
        return result

    # ------------------------------------------------------------------ #
    # Exact discrete convolution
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply(M: np.ndarray, events: List[Event]) -> np.ndarray:
        G = M.shape[0]
        out = np.zeros_like(M)
        for dh, da, p in events:
            if dh == 0 and da == 0:
                out += p * M
            elif dh < G and da < G:
                out[dh:, da:] += p * M[: G - dh, : G - da]
        return out

    def _joint_matrix(self, cal: Dict[str, Any], fg_make: float) -> np.ndarray:
        tau = self.priors["trunc_fraction"]
        G = int(self.priors["grid_max_points"]) + 1
        ev = {
            ("h", False): self._events("h", cal["td_h"], cal["fga_h"], fg_make, 1.0),
            ("h", True): self._events("h", cal["td_h"], cal["fga_h"], fg_make, tau),
            ("a", False): self._events("a", cal["td_a"], cal["fga_a"], fg_make, 1.0),
            ("a", True): self._events("a", cal["td_a"], cal["fga_a"], fg_make, tau),
        }
        base = np.zeros((G, G))
        base[0, 0] = 1.0
        home_cache: Dict[Tuple[int, bool], np.ndarray] = {}

        def home_part(n_full: int, trunc: bool) -> np.ndarray:
            key = (n_full, trunc)
            if key not in home_cache:
                M = base
                for _ in range(n_full):
                    M = self._apply(M, ev[("h", False)])
                if trunc:
                    M = self._apply(M, ev[("h", True)])
                home_cache[key] = M
            return home_cache[key]

        joint = np.zeros((G, G))
        for w, n_h, n_a, last in cal["scen"]:
            h_tr, a_tr = last == "h", last == "a"
            M = home_part(n_h - int(h_tr), h_tr)
            for _ in range(n_a - int(a_tr)):
                M = self._apply(M, ev[("a", False)])
            if a_tr:
                M = self._apply(M, ev[("a", True)])
            joint += w * M
        total = joint.sum()
        return joint / total

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def score_matrix(self, game_context: Dict[str, Any]) -> np.ndarray:
        ctx = game_context or {}
        lam_h, lam_a = self._situational_means(ctx)
        wind, is_dome, temp = self._weather(ctx)
        fg_make = self._fg_make(wind, is_dome, temp)
        cal = self._calibrate(lam_h, lam_a, self._pace_log(ctx),
                              self._receiver_prob_home(ctx), wind, fg_make)
        M = self._joint_matrix(cal, fg_make)

        idx = np.arange(M.shape[0])
        mh = float((M.sum(axis=1) * idx).sum())
        ma = float((M.sum(axis=0) * idx).sum())
        tot_pmf = np.array([np.trace(np.fliplr(M), offset=M.shape[0] - 1 - t)
                            for t in range(M.shape[0])])
        tmean = float((tot_pmf * idx).sum())
        tvar = float((tot_pmf * idx ** 2).sum() - tmean ** 2)
        self.last_diagnostics = {
            "target_home": lam_h, "target_away": lam_a,
            "model_home_mean": mh, "model_away_mean": ma,
            "p_home": float(np.tril(M, -1).sum()),
            "p_draw": float(np.trace(M)),
            "p_away": float(np.triu(M, 1).sum()),
            "p_scoreless": float(M[0, 0]),
            "total_dispersion_index": tvar / tmean if tmean > 0 else float("nan"),
            "fg_make": fg_make, "drive_q": cal["q"],
            "eff_drives_home": cal["eh"], "eff_drives_away": cal["ea"],
            "ppd_home": cal["m_h"], "ppd_away": cal["m_a"],
        }
        return M

    def predict_distribution(self, game_context: Dict[str, Any]) -> "DiscreteDistribution":
        M = self.score_matrix(game_context)
        scores: Dict[Tuple[int, int], float] = {}
        nz = np.argwhere(M > 1e-12)
        for h, a in nz:
            scores[(int(h), int(a))] = float(M[h, a])
        s = sum(scores.values())
        scores = {k: v / s for k, v in scores.items()}
        try:
            return DiscreteDistribution.from_scores(scores)
        except Exception:
            d = self.last_diagnostics
            try:
                return DiscreteDistribution.from_probabilities(d["p_home"], d["p_draw"], d["p_away"])
            except Exception:
                return DiscreteDistribution(
                    probabilities={"home": d["p_home"], "draw": d["p_draw"], "away": d["p_away"]}
                )

    def market_probabilities(self, game_context: Dict[str, Any],
                             total_lines: Optional[List[float]] = None) -> Dict[str, float]:
        """Winner (3-way) and total over/under/push probabilities."""
        M = self.score_matrix(game_context)
        d = self.last_diagnostics
        out = {"home": d["p_home"], "draw": d["p_draw"], "away": d["p_away"]}
        G = M.shape[0]
        tot = np.add.outer(np.arange(G), np.arange(G))
        for line in (total_lines or [9.5, 10.5, 11.5, 13.5]):
            out[f"over_{line}"] = float(M[tot > line].sum())
            out[f"under_{line}"] = float(M[tot < line].sum())
            out[f"push_{line}"] = float(M[tot == line].sum())
        return out