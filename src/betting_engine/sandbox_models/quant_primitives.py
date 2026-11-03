"""High-performance quantitative mathematical primitives for betting models.

Provides institutional-grade building blocks for autonomous model synthesis:
1. Archimedean & Elliptical Copulas (Clayton, Gumbel, Frank, Gaussian)
2. Bivariate Score Lattice coupling via Copula transforms
3. Stochastic Renewal Hazards (Weibull, Gamma) for goal/point timing
4. Overdispersed & Zero-Inflated Count Distributions (ZIP, ZINB)
5. Bayesian Shrinkage Estimators (James-Stein, Empirical Bayes)
6. Dynamic Kalman State Filters for team momentum
7. Temperature & Probability Calibration
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.stats import norm, poisson, nbinom


# ==============================================================================
# 1. ARCHIMEDEAN & ELLIPTICAL COPULAS
# ==============================================================================

class ClaytonCopula:
    """Clayton Archimedean Copula for asymmetric lower-tail dependence.
    
    C(u, v; theta) = [max(u^(-theta) + v^(-theta) - 1, 0)]^(-1/theta)
    Lower tail dependence: lambda_L = 2^(-1/theta) for theta > 0.
    Upper tail dependence: lambda_U = 0.
    
    Ideal for joint low-scoring matches, bad weather suppression, or mutual collapses.
    """
    def __init__(self, theta: float = 1.0):
        if theta <= 0:
            raise ValueError(f"Clayton theta must be > 0, got {theta}")
        self.theta = float(theta)

    def cdf(self, u: float, v: float) -> float:
        if u <= 0.0 or v <= 0.0:
            return 0.0
        u = min(max(float(u), 1e-9), 1.0)
        v = min(max(float(v), 1e-9), 1.0)
        val = u ** (-self.theta) + v ** (-self.theta) - 1.0
        if val <= 0.0:
            return 0.0
        return float(val ** (-1.0 / self.theta))

    @property
    def lower_tail_dependence(self) -> float:
        return 2.0 ** (-1.0 / self.theta)


class GumbelCopula:
    """Gumbel Archimedean Copula for asymmetric upper-tail dependence.
    
    C(u, v; theta) = exp(-[(-ln u)^theta + (-ln v)^theta]^(1/theta))
    Upper tail dependence: lambda_U = 2 - 2^(1/theta) for theta >= 1.
    Lower tail dependence: lambda_L = 0.
    
    Ideal for shootouts, high-scoring shootouts, garbage-time clustering, and over-pace surges.
    """
    def __init__(self, theta: float = 1.5):
        if theta < 1.0:
            raise ValueError(f"Gumbel theta must be >= 1.0, got {theta}")
        self.theta = float(theta)

    def cdf(self, u: float, v: float) -> float:
        if u <= 0.0 or v <= 0.0:
            return 0.0
        if u >= 1.0:
            return float(min(1.0, max(0.0, v)))
        if v >= 1.0:
            return float(min(1.0, max(0.0, u)))
        u = min(max(float(u), 1e-9), 1.0 - 1e-9)
        v = min(max(float(v), 1e-9), 1.0 - 1e-9)
        term1 = (-math.log(u)) ** self.theta
        term2 = (-math.log(v)) ** self.theta
        return float(math.exp(-((term1 + term2) ** (1.0 / self.theta))))

    @property
    def upper_tail_dependence(self) -> float:
        return 2.0 - (2.0 ** (1.0 / self.theta))


class FrankCopula:
    """Frank Archimedean Copula for symmetric non-linear association without tail clustering.
    
    C(u, v; theta) = -1/theta * ln(1 + (exp(-theta*u) - 1)*(exp(-theta*v) - 1)/(exp(-theta) - 1))
    
    Covers both positive (theta > 0) and negative (theta < 0) dependence with radial symmetry.
    """
    def __init__(self, theta: float = 2.0):
        if abs(theta) < 1e-6:
            raise ValueError("Frank theta must not be 0.0")
        self.theta = float(theta)

    def cdf(self, u: float, v: float) -> float:
        if u <= 0.0 or v <= 0.0:
            return 0.0
        if u >= 1.0:
            return float(min(1.0, max(0.0, v)))
        if v >= 1.0:
            return float(min(1.0, max(0.0, u)))
        u = min(max(float(u), 1e-9), 1.0 - 1e-9)
        v = min(max(float(v), 1e-9), 1.0 - 1e-9)
        th = self.theta
        num = (math.expm1(-th * u)) * (math.expm1(-th * v))
        den = math.expm1(-th)
        inner = 1.0 + (num / den)
        inner = max(inner, 1e-12)
        return float(-1.0 / th * math.log(inner))


class GaussianCopula:
    """Bivariate Gaussian Copula parameterized by linear correlation rho.
    
    C(u, v; rho) = Phi_rho(Phi^-1(u), Phi^-1(v))
    """
    def __init__(self, rho: float = 0.0):
        if not (-0.999 <= rho <= 0.999):
            raise ValueError(f"rho must be between -0.999 and 0.999, got {rho}")
        self.rho = float(rho)

    def cdf(self, u: float, v: float) -> float:
        if u <= 0.0 or v <= 0.0:
            return 0.0
        if u >= 1.0:
            return float(min(1.0, max(0.0, v)))
        if v >= 1.0:
            return float(min(1.0, max(0.0, u)))
        u = min(max(float(u), 1e-9), 1.0 - 1e-9)
        v = min(max(float(v), 1e-9), 1.0 - 1e-9)
        if abs(self.rho) < 1e-6:
            return u * v
        z_u = norm.ppf(u)
        z_v = norm.ppf(v)
        # Bivariate normal CDF approximation via Owen's T / numerical integration
        # or decomposition: V | U=u ~ N(rho * z_u, 1 - rho^2)
        mean_v_given_u = self.rho * z_u
        std_v_given_u = math.sqrt(max(1e-6, 1.0 - self.rho ** 2))
        # Integrate using Gauss-Legendre quadrature over [1e-9, u]
        nodes = 16
        x_pts, w_pts = np.polynomial.legendre.leggauss(nodes)
        # Transform [-1, 1] to [1e-6, u]
        t_pts = 0.5 * (x_pts + 1.0) * (u - 1e-6) + 1e-6
        dt = 0.5 * (u - 1e-6)
        
        # Integrand: P(Z_v <= z_v | Z_u = Phi^-1(t))
        z_t = norm.ppf(t_pts)
        cond_probs = norm.cdf((z_v - self.rho * z_t) / std_v_given_u)
        res = float(np.sum(w_pts * cond_probs) * dt)
        return min(max(res, 0.0), min(u, v))


def build_copula_score_lattice(
    marginal_pmf_h: Sequence[float],
    marginal_pmf_a: Sequence[float],
    copula: Union[ClaytonCopula, GumbelCopula, FrankCopula, GaussianCopula, Callable[[float, float], float]],
) -> Dict[Tuple[int, int], float]:
    """Couples two arbitrary marginal discrete distributions using a copula transform.
    
    Uses 2D finite difference on discrete cumulative distribution boundaries:
    P(H = h, A = a) = C(F_H(h), F_A(a)) - C(F_H(h-1), F_A(a)) - C(F_H(h), F_A(a-1)) + C(F_H(h-1), F_A(a-1))
    
    Guarantees that the resulting discrete joint distribution strictly preserves
    marginal probabilities while honoring the nonlinear dependence structure.
    """
    cdf_fn = copula.cdf if hasattr(copula, 'cdf') else copula
    
    # Compute discrete CDFs
    cdf_h = [0.0] + list(np.cumsum(marginal_pmf_h))
    cdf_a = [0.0] + list(np.cumsum(marginal_pmf_a))
    # Normalize to 1.0 at upper boundary
    cdf_h[-1] = 1.0
    cdf_a[-1] = 1.0

    scores: Dict[Tuple[int, int], float] = {}
    len_h = len(marginal_pmf_h)
    len_a = len(marginal_pmf_a)

    # Precompute C(u_i, v_j) grid
    c_grid = np.zeros((len_h + 1, len_a + 1), dtype=float)
    for i in range(len_h + 1):
        u = cdf_h[i]
        for j in range(len_a + 1):
            v = cdf_a[j]
            c_grid[i, j] = cdf_fn(u, v)

    # 2D Finite Difference
    total_mass = 0.0
    for h in range(len_h):
        for a in range(len_a):
            # C(h+1, a+1) - C(h, a+1) - C(h+1, a) + C(h, a)
            cell_prob = (
                c_grid[h + 1, a + 1]
                - c_grid[h, a + 1]
                - c_grid[h + 1, a]
                + c_grid[h, a]
            )
            prob = max(0.0, float(cell_prob))
            scores[(h, a)] = prob
            total_mass += prob

    # Invariant: Normalize so total probability sums strictly to 1.0
    if total_mass > 0.0:
        for k in scores:
            scores[k] /= total_mass

    return scores


# ==============================================================================
# 2. STOCHASTIC RENEWAL PROCESSES & HAZARD MODELS
# ==============================================================================

class WeibullRenewalHazard:
    """Weibull renewal hazard process for inter-event scoring times.
    
    Hazard: h(t) = (shape / scale) * (t / scale)^(shape - 1)
    Survival: S(t) = exp(-(t / scale)^shape)
    
    shape > 1.0: Positive duration dependence (aging/fatigue/intensification).
    shape < 1.0: Negative duration dependence (declining rate).
    shape == 1.0: Pure exponential Poisson memoryless process.
    """
    def __init__(self, shape: float = 1.2, scale: float = 30.0):
        if shape <= 0 or scale <= 0:
            raise ValueError("shape and scale must be positive")
        self.shape = float(shape)
        self.scale = float(scale)

    def hazard(self, t: float) -> float:
        t = max(1e-6, float(t))
        return (self.shape / self.scale) * ((t / self.scale) ** (self.shape - 1.0))

    def cumulative_hazard(self, t: float) -> float:
        t = max(0.0, float(t))
        return (t / self.scale) ** self.shape

    def survival(self, t: float) -> float:
        return float(math.exp(-self.cumulative_hazard(t)))

    def expected_events_in_interval(self, t_start: float, t_end: float) -> float:
        """Computes expected event count in interval [t_start, t_end] via integrated hazard."""
        t_start = max(0.0, float(t_start))
        t_end = max(t_start, float(t_end))
        return max(0.0, self.cumulative_hazard(t_end) - self.cumulative_hazard(t_start))


# ==============================================================================
# 3. OVERDISPERSED & ZERO-INFLATED COUNT DISTRIBUTIONS
# ==============================================================================

class ZeroInflatedPoisson:
    """Zero-Inflated Poisson (ZIP) mixture distribution for high-zero periods.
    
    P(X = 0) = pi + (1 - pi) * exp(-mu)
    P(X = k) = (1 - pi) * (mu^k * exp(-mu)) / k!   for k >= 1
    """
    def __init__(self, mu: float, pi: float = 0.15):
        if mu <= 0:
            raise ValueError(f"mu must be > 0, got {mu}")
        if not (0.0 <= pi < 1.0):
            raise ValueError(f"pi must be in [0, 1), got {pi}")
        self.mu = float(mu)
        self.pi = float(pi)

    def pmf(self, k: int) -> float:
        if k < 0:
            return 0.0
        if k == 0:
            return float(self.pi + (1.0 - self.pi) * math.exp(-self.mu))
        return float((1.0 - self.pi) * poisson.pmf(k, self.mu))

    def generate_pmf_vector(self, max_k: int) -> List[float]:
        return [self.pmf(k) for k in range(max_k + 1)]

    @property
    def expected_value(self) -> float:
        return (1.0 - self.pi) * self.mu

    @property
    def variance(self) -> float:
        return (1.0 - self.pi) * self.mu * (1.0 + self.pi * self.mu)


class ZeroInflatedNegativeBinomial:
    """Zero-Inflated Negative Binomial (ZINB) distribution.
    
    Handles both structural excess zeros (pi) and count overdispersion (alpha).
    """
    def __init__(self, mu: float, alpha: float = 0.2, pi: float = 0.10):
        if mu <= 0 or alpha <= 0:
            raise ValueError("mu and alpha must be positive")
        if not (0.0 <= pi < 1.0):
            raise ValueError(f"pi must be in [0, 1), got {pi}")
        self.mu = float(mu)
        self.alpha = float(alpha)
        self.pi = float(pi)
        self.r = 1.0 / self.alpha
        self.p = self.r / (self.r + self.mu)

    def pmf(self, k: int) -> float:
        if k < 0:
            return 0.0
        nb_p0 = float(nbinom.pmf(0, self.r, self.p))
        if k == 0:
            return float(self.pi + (1.0 - self.pi) * nb_p0)
        return float((1.0 - self.pi) * nbinom.pmf(k, self.r, self.p))

    def generate_pmf_vector(self, max_k: int) -> List[float]:
        return [self.pmf(k) for k in range(max_k + 1)]


# ==============================================================================
# 4. BAYESIAN SHRINKAGE ESTIMATORS
# ==============================================================================

class JamesSteinShrinkage:
    """James-Stein Shrinkage Estimator for vectors of team performance parameters.
    
    Shrinks noisy high-dimensional MLE parameter estimates toward an empirical mean:
    theta_hat = target + max(0, 1 - (p - 2) * sigma^2 / ||y - target||^2) * (y - target)
    
    Mathematically proven to strictly dominate raw MLE in dimensions p >= 3
    under mean squared error loss (Stein's Phenomenon).
    """
    @staticmethod
    def shrink(
        estimates: Sequence[float],
        target: Optional[float] = None,
        sigma_sq: Optional[float] = None
    ) -> List[float]:
        y = np.array(estimates, dtype=float)
        p = len(y)
        if p < 3:
            # For p < 3, return raw estimates or empirical mean
            return list(y)
        
        m0 = float(np.mean(y)) if target is None else float(target)
        diff = y - m0
        norm_sq = float(np.sum(diff ** 2))
        
        if norm_sq < 1e-12:
            return list(y)
            
        var = float(np.var(y, ddof=1)) if sigma_sq is None else float(sigma_sq)
        shrinkage_factor = max(0.0, 1.0 - ((p - 2) * var) / norm_sq)
        shrunk = m0 + shrinkage_factor * diff
        return [float(x) for x in shrunk]


class EmpiricalBayesPoissonRate:
    """Conjugate Gamma-Poisson Empirical Bayes shrinkage for team event rates.
    
    Prior: Lambda ~ Gamma(alpha, beta)
    Posterior: Lambda | (k, t) ~ Gamma(alpha + k, beta + t)
    Posterior Mean = (alpha + k) / (beta + t)
    """
    def __init__(self, prior_events: float = 3.0, prior_games: float = 2.0):
        self.alpha_prior = max(0.1, float(prior_events))
        self.beta_prior = max(0.1, float(prior_games))

    def update(self, observed_events: float, observed_games: float) -> float:
        alpha_post = self.alpha_prior + max(0.0, float(observed_events))
        beta_post = self.beta_prior + max(0.0, float(observed_games))
        return float(alpha_post / beta_post)


# ==============================================================================
# 5. DYNAMIC KALMAN STATE TRACKER FOR FORM & MOMENTUM
# ==============================================================================

class KalmanRatingTracker:
    """1D Recursive Kalman Filter for dynamic team rating & situational momentum tracking.
    
    Tracks a latent rating state x_t with state noise Q and observation noise R.
    Optimal for adjusting ratings given rest differentials, injuries, or blowouts.
    """
    def __init__(
        self,
        initial_rating: float = 1500.0,
        initial_uncertainty: float = 100.0,
        process_variance: float = 16.0,
        observation_variance: float = 100.0
    ):
        self.x = float(initial_rating)
        self.p = float(initial_uncertainty)
        self.q = float(process_variance)
        self.r = float(observation_variance)

    def predict(self, rest_decay_factor: float = 1.0) -> None:
        """Time update: uncertainty expands with passage of time/rest."""
        self.p = self.p + self.q * max(0.5, float(rest_decay_factor))

    def update(self, observed_performance: float) -> float:
        """Measurement update: assimilates new game outcome."""
        y = float(observed_performance)
        # Kalman gain
        k_gain = self.p / (self.p + self.r)
        # State update
        self.x = self.x + k_gain * (y - self.x)
        # Covariance update
        self.p = (1.0 - k_gain) * self.p
        return self.x

    @property
    def current_state(self) -> Tuple[float, float]:
        """Returns (rating_estimate, uncertainty)."""
        return self.x, self.p


# ==============================================================================
# 6. PROBABILITY CALIBRATION & TEMPERATURE SCALING
# ==============================================================================

class TemperatureScaler:
    """Applies temperature scaling to a discrete probability distribution.
    
    p_i = exp(log(p_i) / T) / sum_j exp(log(p_j) / T)
    T > 1.0: Damps overconfidence (increases entropy).
    T < 1.0: Sharpens confidence (decreases entropy).
    T == 1.0: Identity.
    """
    def __init__(self, temperature: float = 1.0):
        if temperature <= 0.0:
            raise ValueError(f"Temperature must be > 0.0, got {temperature}")
        self.temperature = float(temperature)

    def scale_probabilities(self, probs: Sequence[float]) -> List[float]:
        arr = np.array(probs, dtype=float)
        arr = np.clip(arr, 1e-12, 1.0)
        logits = np.log(arr) / self.temperature
        # Subtract max for numerical stability
        logits -= np.max(logits)
        exp_logits = np.exp(logits)
        scaled = exp_logits / np.sum(exp_logits)
        return [float(x) for x in scaled]


def continuous_fractional_kelly(
    win_probability: float,
    decimal_odds: float,
    uncertainty_penalty: float = 0.0,
    fractional_multiplier: float = 0.25,
    min_stake: float = 0.50,
    max_stake: float = 8.00,
) -> float:
    """Calculates continuous risk-adjusted fractional Kelly stake.
    
    f* = (b * p - q) / b = p - (1 - p)/(b - 1)
    Adjusted by uncertainty penalty and bounded within [min_stake, max_stake].
    """
    p = min(max(float(win_probability), 0.0), 1.0)
    odds = float(decimal_odds)
    if odds <= 1.0:
        return 0.0
    
    b = odds - 1.0
    q = 1.0 - p
    edge = p * odds - 1.0
    if edge <= 0.0:
        return 0.0

    raw_kelly = (b * p - q) / b
    # Apply continuous uncertainty damping
    damped_fraction = raw_kelly * (1.0 - min(0.9, max(0.0, float(uncertainty_penalty))))
    continuous_stake = damped_fraction * fractional_multiplier * 100.0  # scaled to dollar units

    # Clamp strictly within system bounds [$0.50, $8.00]
    return float(min(max(continuous_stake, min_stake), max_stake))
