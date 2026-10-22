"""Experimental signed-integer NFL yard payoffs, with no production caller.

The rounded latent distribution is a NEW, unvalidated assumption, not D06's
continuous-density validation. Loading never sees odds, refits, or selects a
recipe on test results. Collector timestamps are not authenticated availability.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import math
from pathlib import Path

from scipy.special import log_ndtr

from .player_history import _time
from .player_model_study import METRICS
from .player_prop_contracts import number, numeric_text
from .player_prop_probability import _finite, _HASH, _json, _read, _sha

VERSION = "f0026-rounded-signed-yard-v1"
METRIC = "NFL/player/rushing_yards"
RECIPES = ("pooled_normal", "player_normal", "player_laplace")
DISCRETIZATION = "unit_bins_centered_on_all_signed_integers_v1"


@dataclass(frozen=True)
class YardForecast:
    metric_key: str
    namespace: str
    stat_scope: str
    player_id: str
    team_id: str
    recipe: str
    location: float
    scale: float
    location_basis: str
    model_sha256: str
    artifact_index_sha256: str
    plan_sha256: str
    input_sha256: str
    fit_event_start_cutoff: str
    study_as_of: str
    validation_reasons: tuple[str, ...]
    discretization: str = DISCRETIZATION


def load_yard_forecast(study_root, *, artifact_index_sha256, player_id, team_id,
                       metric_key=METRIC):
    """Load only D06's pinned, selection-locked NFL rushing-yard model.

    Hashes authenticate bytes only. Full corpus replay and historical source
    availability are outside this reader. Unknown player/team pairs retain the
    original pooled fallback and cannot establish participation or current role.
    """
    if metric_key != METRIC:
        raise ValueError("unsupported_yard_metric")
    if any(not isinstance(v, str) or not v or v.strip() != v for v in (player_id, team_id)):
        raise ValueError("exact_source_player_and_team_ids_required")
    root = Path(study_root)
    body = _read(root, "artifacts.json")
    if (not isinstance(artifact_index_sha256, str) or not _HASH.fullmatch(artifact_index_sha256)
            or _sha(body) != artifact_index_sha256):
        raise ValueError("artifact_index_hash_mismatch")
    index = _json(body)
    if not isinstance(index, dict):
        raise ValueError("invalid_artifact_index")

    def artifact(name):
        content = _read(root, name)
        if index.get(name) != _sha(content):
            raise ValueError("artifact_hash_mismatch:" + name)
        result = _json(content)
        if not isinstance(result, dict):
            raise ValueError("invalid_artifact_object")
        return result

    plan, report = artifact("plan.json"), artifact("report.json")
    if report.get("plan_sha256") != index["plan.json"]:
        raise ValueError("report_plan_mismatch")
    if (plan.get("mode") != "retrospective_descriptive" or plan.get("odds_used") is not False
            or report.get("odds_used") is not False
            or report.get("production_props_enabled") is not False
            or report.get("historical_availability_authenticated") is not False):
        raise ValueError("only_retrospective_independent_studies_supported")
    spec = next(s for s in METRICS if s.key == METRIC)
    if (sum(s == asdict(spec) for s in plan["metrics"]) != 1
            or plan.get("continuous_scale_floor") != 1.0 or plan.get("shrinkage") != 5.0
            or plan.get("selection_score") != "game_balanced_nll"):
        raise ValueError("unsupported_model_semantics")
    if not _time(plan["selection_start"]) < _time(plan["test_start"]) < _time(plan["as_of"]):
        raise ValueError("invalid_chronological_cutoffs")
    if not isinstance(plan.get("input_sha256"), str) or not _HASH.fullmatch(plan["input_sha256"]):
        raise ValueError("missing_input_hash")
    studies = [s for s in report["studies"] if s.get("metric") == METRIC]
    if len(studies) != 1:
        raise ValueError("ambiguous_study_metric")
    study = studies[0]
    if (study.get("state") != "retrospective_comparison_completed"
            or study.get("validation_gate", {}).get("accepted") is not False):
        raise ValueError("unsupported_study_state")
    folder = METRIC.replace("/", "_")
    lock = artifact(folder + "/selection-lock.json")
    recipe = lock.get("selected_recipe")
    if (recipe not in RECIPES or lock.get("test_used_for_selection") is not False
            or recipe != study.get("selected_recipe")
            or lock.get("selection") != study.get("selection")
            or set(lock["selection"]) != set(RECIPES)):
        raise ValueError("selection_lock_mismatch")
    selected = min(RECIPES, key=lambda r: (_finite(lock["selection"][r]["game_balanced_nll"]), RECIPES.index(r)))
    if selected != recipe:
        raise ValueError("recipe_not_selected_on_earlier_selection")
    relative = folder + "/" + recipe + "-test-model.json"
    model = artifact(relative)
    if (set(model) != {"recipe", "global_mean", "player_team_means", "shrinkage", "training_rows", "scale"}
            or model["recipe"] != recipe or model["shrinkage"] != 5.0):
        raise ValueError("unsupported_model_schema")
    counts = [study["split_counts"][s]["rows"] for s in ("train", "selection")]
    if (any(type(n) is not int or n <= 0 for n in counts)
            or type(model["training_rows"]) is not int or model["training_rows"] != sum(counts)):
        raise ValueError("training_membership_count_mismatch")
    means = model["player_team_means"]
    if not isinstance(means, dict) or (recipe == "pooled_normal" and means):
        raise ValueError("invalid_player_team_means")
    for key, value in means.items():
        ids = _json(key)
        if (not isinstance(ids, list) or len(ids) != 2
                or any(not isinstance(x, str) or not x or x.strip() != x for x in ids)
                or json.dumps(ids, separators=(",", ":")) != key):
            raise ValueError("invalid_player_team_mean")
        _parameters(value, model["scale"])
    _parameters(model["global_mean"], model["scale"])
    reasons = study["validation_gate"].get("reasons")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(r, str) or not r for r in reasons):
        raise ValueError("missing_validation_limitations")
    key = json.dumps([player_id, team_id], separators=(",", ":"))
    return YardForecast(METRIC, spec.namespace, spec.scope, player_id, team_id, recipe,
                        float(means.get(key, model["global_mean"])), float(model["scale"]),
                        "player_team" if key in means else "pooled_fallback",
                        index[relative], artifact_index_sha256, index["plan.json"], plan["input_sha256"],
                        plan["test_start"], plan["as_of"], tuple(reasons))


def _parameters(location, scale):
    location, scale = _finite(location), _finite(scale)
    # Explicit arithmetic domain; these are not betting/validation thresholds.
    if abs(location) > 1_000_000 or not 1 <= scale <= 1_000_000:
        raise ValueError("unsupported_yard_parameters")
    return location, scale


def _distribution(forecast):
    if not isinstance(forecast, YardForecast):
        raise ValueError("yard_forecast_required")
    if (forecast.metric_key != METRIC or forecast.namespace != "nflverse-gsis"
            or forecast.stat_scope != "full_game_including_overtime"
            or forecast.recipe not in RECIPES or forecast.discretization != DISCRETIZATION):
        raise ValueError("unsupported_yard_forecast")
    location, scale = _parameters(forecast.location, forecast.scale)

    def logcdf(x):
        z = (x - location) / scale
        if forecast.recipe.endswith("normal"):
            return float(log_ndtr(z))
        return z - math.log(2) if z <= 0 else math.log1p(-0.5 * math.exp(-z))

    def logsf(x):
        z = (x - location) / scale
        if forecast.recipe.endswith("normal"):
            return float(log_ndtr(-z))
        return -z - math.log(2) if z >= 0 else math.log1p(-0.5 * math.exp(z))

    def logmass(k):
        lo, hi = k - .5, k + .5
        # Subtract the small tail; subtracting rounded CDFs can erase push mass.
        a, b = (logsf(lo), logsf(hi)) if lo >= location else (logcdf(hi), logcdf(lo))
        if not b < a:
            raise ValueError("unresolved_integer_mass")
        return a + math.log(-math.expm1(b - a))
    return logcdf, logsf, logmass


def yard_log_mass(forecast, value):
    """Log P(Y=k) for signed integer k, including underflowed ordinary mass.

    Y=k iff latent X lies in [k-.5,k+.5). No clipping at zero, finite grid,
    density-as-probability substitution or tail renormalization is used.
    """
    k = number(value)
    if k != k.to_integral_value() or abs(k) > 1_000_000:
        raise ValueError("bounded_integer_yard_target_required")
    return _distribution(forecast)[2](float(k))


def evaluate_yard_payoff(forecast, *, operator, line, decimal_odds=None):
    """Conditional experimental Over/Under/ladder arithmetic, never an offer.

    Integer equality refunds Over/Under, ladders include equality. Negative
    yard totals remain possible. DNP/void mass is unknown, not set to zero.
    """
    cdf, sf, mass = _distribution(forecast)
    threshold = number(line)
    if operator not in ("over", "under", "at_least"):
        raise ValueError("unsupported_payoff_operator")
    integer = threshold == threshold.to_integral_value()
    if (threshold.copy_abs() > 1_000_000 or
            (not integer and threshold != Decimal(math.floor(threshold)) + Decimal("0.5"))):
        raise ValueError("only_bounded_integer_or_half_lines_supported")
    if operator == "at_least" and not integer:
        raise ValueError("integer_ladder_required")
    value = float(threshold)
    if operator == "at_least":
        win, loss, push = math.exp(sf(value - .5)), math.exp(cdf(value - .5)), 0.
    else:
        below = math.exp(cdf(math.ceil(value) - .5))
        above = math.exp(sf(math.floor(value) + .5))
        push = math.exp(mass(value)) if integer else 0.
        win, loss = (above, below) if operator == "over" else (below, above)
    if (any(not math.isfinite(p) or not 0 <= p <= 1 for p in (win, loss, push))
            or not math.isclose(math.fsum((win, loss, push)), 1., rel_tol=0., abs_tol=1e-10)):
        raise ValueError("invalid_probability_mass")
    fair = 1 + loss / win if win else None
    if fair is not None and not math.isfinite(fair):
        fair = None
    odds_text, ev = None, None
    if decimal_odds is not None:
        odds = number(decimal_odds)
        if odds <= 1 or odds > Decimal("1e12"):
            raise ValueError("invalid_scenario_decimal_odds")
        odds_text = numeric_text(odds)
        ev = win * (float(odds) - 1) - loss
    result = dict(version=VERSION, state="hypothetical_signed_yard_payoff_only",
                  forecast=asdict(forecast), operator=operator, line=numeric_text(threshold),
                  p_win=win, p_push=push, p_loss=loss, p_void=None,
                  fair_decimal_odds=fair, scenario_decimal_odds=odds_text,
                  scenario_net_ev_per_unit=ev, production_props_enabled=False,
                  publishable=False, execution_eligible=False,
                  activation_blockers=["rounded_integer_distribution_not_validated",
                      "retrospective_model_not_approved", "historical_availability_not_authenticated",
                      "current_participation_and_role_not_verified",
                      "exact_offered_contract_and_identity_bridge_not_verified",
                      "sportsbook_settlement_rules_not_verified"],
                  claim="New discretization assumption; no offered bet, measured edge or profit.")
    result["assessment_sha256"] = _sha(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    return result
