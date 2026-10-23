"""Automated pre-flight verification gate for sandbox candidate models.

Ensures candidate models strictly satisfy mathematical axioms, latency constraints,
and EvidenceGuard invariants before entering parallel shadow incubation.
"""
from __future__ import annotations
import math
import time
from typing import Any, Dict, List, Tuple

from .base import BaseCandidateModel, DiscreteDistribution


def validate_candidate_model(
    model: BaseCandidateModel,
    sample_context: Dict[str, Any],
    max_latency_ms: float = 50.0,
) -> Tuple[bool, List[str]]:
    """Runs automated pre-flight checks against a candidate model instance.
    
    Returns:
        (passed, failure_reasons)
    """
    errors: List[str] = []

    # 1. Interface check
    if not isinstance(model, BaseCandidateModel):
        return False, ["Model does not inherit from BaseCandidateModel."]

    # 2. Execution latency test
    latencies = []
    try:
        for _ in range(5):
            t0 = time.perf_counter()
            dist = model.predict_distribution(sample_context)
            dt = (time.perf_counter() - t0) * 1000.0
            latencies.append(dt)
        avg_latency = sum(latencies) / len(latencies)
        if avg_latency > max_latency_ms:
            errors.append(f"Latency check failed: average {avg_latency:.2f}ms exceeds {max_latency_ms:.1f}ms limit.")
    except Exception as exc:
        return False, [f"Execution error during latency test: {exc}"]

    # 3. Probability axioms check
    try:
        scores = dist.scores
        if not scores:
            errors.append("Distribution contains empty scores mapping.")
        else:
            total_prob = sum(scores.values())
            if not math.isclose(total_prob, 1.0, abs_tol=1e-3):
                errors.append(f"Probability sum check failed: sum is {total_prob:.6f}, expected 1.0.")

            for (h, a), p in scores.items():
                if math.isnan(p) or math.isinf(p):
                    errors.append(f"Score ({h}, {a}) has invalid float value: {p}.")
                    break
                if p < 0.0:
                    errors.append(f"Score ({h}, {a}) has negative probability: {p}.")
                    break

        # Check marginals
        for attr, val in (('p_home', dist.p_home), ('p_away', dist.p_away), ('p_draw', dist.p_draw)):
            if math.isnan(val) or math.isinf(val):
                errors.append(f"Marginal {attr} is invalid float: {val}.")
            if val < 0.0 or val > 1.0:
                errors.append(f"Marginal {attr} out of bounds: {val}.")

    except Exception as exc:
        errors.append(f"Distribution validation error: {exc}")

    # 4. Market evaluation checks
    try:
        # Check Total
        p_over, ev_over = model.evaluate_market('totals', 'over', 5.5, 1.95, sample_context)
        if not (0.01 <= p_over <= 0.99):
            errors.append(f"Total probability out of bounds [0.01, 0.99]: {p_over}")
        expected_ev = round(p_over * 1.95 - 1.0, 4)
        if not math.isclose(ev_over, expected_ev, abs_tol=1e-3):
            errors.append(f"EV calculation mismatch: got {ev_over}, expected {expected_ev}")

        # Check Spread
        p_spread, ev_spread = model.evaluate_market('spreads', 'home', -1.5, 2.10, sample_context)
        if not (0.01 <= p_spread <= 0.99):
            errors.append(f"Spread probability out of bounds [0.01, 0.99]: {p_spread}")

        # Check Moneyline
        p_ml, ev_ml = model.evaluate_market('moneyline', 'home', None, 1.80, sample_context)
        if not (0.01 <= p_ml <= 0.99):
            errors.append(f"Moneyline probability out of bounds [0.01, 0.99]: {p_ml}")

    except Exception as exc:
        errors.append(f"Market evaluation test failed: {exc}")

    # 5. Determinism check
    try:
        dist1 = model.predict_distribution(sample_context)
        dist2 = model.predict_distribution(sample_context)
        if not math.isclose(dist1.p_home, dist2.p_home, abs_tol=1e-5):
            errors.append("Model is non-deterministic: repeated runs with same context yielded different outputs.")
    except Exception as exc:
        errors.append(f"Determinism check error: {exc}")

    return (len(errors) == 0), errors
