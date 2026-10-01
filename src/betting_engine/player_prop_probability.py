"""Offline D06 count-distribution payoff experiment; no production caller.

Forecast loading has no market inputs. Thresholds and prices enter only the
separate scenario evaluator. These are hypothetical payoffs, not offered bets:
no event/player crosswalk, participation probability or Stake rule is inferred.
Pinned hashes identify artifacts, not authenticated historical availability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re

from scipy import stats

from .player_history import _time
from .player_model_study import METRICS
from .player_prop_contracts import number, numeric_text

VERSION = "f0023-count-payoff-v1"
_RECIPES = ("pooled_poisson", "player_poisson", "player_nb")
_HASH = re.compile(r"[a-f0-9]{64}")


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _json(body):
    return json.loads(body, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))


def _finite(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("finite_model_number_required")
    return float(value)


def _read(root, relative):
    path = root / relative
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or any(p.is_symlink() for p in (path, *path.parents))
            or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError("unsafe_artifact_path")
    with path.open("rb") as stream:
        body = stream.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("artifact_too_large")
    return body


@dataclass(frozen=True)
class CountForecast:
    metric_key: str
    namespace: str
    stat_scope: str
    player_id: str
    team_id: str
    recipe: str
    mean: float
    alpha: float
    mean_basis: str
    model_sha256: str
    artifact_index_sha256: str
    plan_sha256: str
    input_sha256: str
    fit_event_start_cutoff: str
    study_as_of: str
    validation_reasons: tuple[str, ...]


def load_count_forecast(study_root: str | Path, *, artifact_index_sha256: str,
                        metric_key: str, player_id: str, team_id: str) -> CountForecast:
    """Read the selection-locked D06 final model without fitting or reranking.

Verify selected-model/control bytes against a caller-pinned artifact index.
The complete training corpus is not reread here; its plan hash is retained.
Unknown player/team pairs use the existing pooled fallback, explicitly labeled.
Source IDs must be supplied in the study's namespace, never guessed from names.
The cutoff is an event-start split, NOT a claimed historical model fit time.
"""
    spec = next((s for s in METRICS if s.key == metric_key), None)
    if spec is None:
        raise ValueError("unsupported_metric")
    if spec.kind != "count":
        raise ValueError("discrete_distribution_required_for_integer_settlement")
    if any(not isinstance(v, str) or not v or v.strip() != v for v in (player_id, team_id)):
        raise ValueError("exact_source_player_and_team_ids_required")
    root = Path(study_root)
    index_body = _read(root, "artifacts.json")
    if not isinstance(artifact_index_sha256, str) or not _HASH.fullmatch(artifact_index_sha256) or _sha(index_body) != artifact_index_sha256:
        raise ValueError("artifact_index_hash_mismatch")
    index = _json(index_body)

    def artifact(name):
        body = _read(root, name)
        if index.get(name) != _sha(body):
            raise ValueError("artifact_hash_mismatch:" + name)
        return _json(body)

    plan, report = artifact("plan.json"), artifact("report.json")
    if report.get("plan_sha256") != index["plan.json"]:
        raise ValueError("report_plan_mismatch")
    if (plan.get("mode") != "retrospective_descriptive" or plan.get("odds_used") is not False
            or report.get("odds_used") is not False
            or report.get("production_props_enabled") is not False
            or report.get("historical_availability_authenticated") is not False):
        raise ValueError("only_retrospective_independent_studies_supported")
    if (sum(s == asdict(spec) for s in plan["metrics"]) != 1
            or plan.get("count_mean_floor") != 1e-9
            or plan.get("nb_poisson_alpha_boundary") != 1e-8
            or plan.get("shrinkage") != 5.0):
        raise ValueError("unsupported_model_semantics")
    if not _time(plan["selection_start"]) < _time(plan["test_start"]) < _time(plan["as_of"]):
        raise ValueError("invalid_chronological_cutoffs")
    if not isinstance(plan.get("input_sha256"), str) or not _HASH.fullmatch(plan["input_sha256"]):
        raise ValueError("missing_input_hash")
    studies = [s for s in report["studies"] if s.get("metric") == metric_key]
    if len(studies) != 1:
        raise ValueError("ambiguous_study_metric")
    study = studies[0]
    if (study.get("state") != "retrospective_comparison_completed"
            or study.get("validation_gate", {}).get("accepted") is not False):
        raise ValueError("unsupported_study_state")
    folder = metric_key.replace("/", "_")
    lock = artifact(folder + "/selection-lock.json")
    recipe = lock.get("selected_recipe")
    if (recipe not in _RECIPES or lock.get("test_used_for_selection") is not False
            or recipe != study.get("selected_recipe")
            or lock.get("selection") != study.get("selection")
            or set(lock["selection"]) != set(_RECIPES)):
        raise ValueError("selection_lock_mismatch")
    selected = min(_RECIPES, key=lambda r: (_finite(lock["selection"][r]["game_balanced_nll"]), _RECIPES.index(r)))
    if selected != recipe:
        raise ValueError("recipe_not_selected_on_earlier_selection")
    relative = folder + "/" + recipe + "-test-model.json"
    model = artifact(relative)
    expected_fields = {"recipe", "global_mean", "player_team_means", "shrinkage", "training_rows"}
    if recipe == "player_nb":
        expected_fields.add("alpha")
    if set(model) != expected_fields or model["recipe"] != recipe or model["shrinkage"] != 5.0:
        raise ValueError("unsupported_model_schema")
    if (type(model["training_rows"]) is not int or model["training_rows"] <= 0
            or model["training_rows"] != sum(study["split_counts"][s]["rows"] for s in ("train", "selection"))):
        raise ValueError("training_membership_count_mismatch")
    means = model["player_team_means"]
    if not isinstance(means, dict) or (recipe == "pooled_poisson" and means):
        raise ValueError("invalid_player_team_means")
    for key, value in means.items():
        ids = _json(key)
        if (not isinstance(ids, list) or len(ids) != 2
                or any(not isinstance(x, str) or not x for x in ids)
                or json.dumps(ids, separators=(",", ":")) != key or _finite(value) < 0):
            raise ValueError("invalid_player_team_mean")
    if _finite(model["global_mean"]) < 0:
        raise ValueError("negative_count_mean")
    alpha = _finite(model.get("alpha", 0.0))
    if alpha < 0:
        raise ValueError("negative_dispersion")
    key = json.dumps([player_id, team_id], separators=(",", ":"))
    mean = max(_finite(means.get(key, model["global_mean"])), 1e-9)
    reasons = study["validation_gate"].get("reasons")
    if not isinstance(reasons, list) or not reasons or any(not isinstance(r, str) for r in reasons):
        raise ValueError("missing_validation_limitations")
    return CountForecast(metric_key, spec.namespace, spec.scope, player_id, team_id,
                         recipe, mean, alpha, "player_team" if key in means else "pooled_fallback",
                         index[relative], artifact_index_sha256, index["plan.json"], plan["input_sha256"],
                         plan["test_start"], plan["as_of"], tuple(reasons))


def evaluate_count_payoff(forecast: CountForecast, *, operator: str, line,
                          decimal_odds=None) -> dict:
    """Evaluate a hypothetical full-scope count contract, with integer refunds.

Only over, under and integer at-least ladders are defined. Half-point thresholds
have no push. Quarter splits, yes/no, continuous yardage and other scopes are
rejected. No finite score-grid truncation or renormalization is performed.
Void/participation probability is unknown, not zero. EV is a scenario per unit
conditional on the supplied model and payoff assumptions, never betting edge.
"""
    if not isinstance(forecast, CountForecast):
        raise ValueError("count_forecast_required")
    spec = next((s for s in METRICS if s.key == forecast.metric_key), None)
    if (spec is None or spec.kind != "count" or spec.namespace != forecast.namespace
            or spec.scope != forecast.stat_scope or forecast.recipe not in _RECIPES):
        raise ValueError("unsupported_count_forecast")
    mu, alpha = _finite(forecast.mean), _finite(forecast.alpha)
    if mu <= 0 or alpha < 0 or (forecast.recipe != "player_nb" and alpha != 0):
        raise ValueError("invalid_count_parameters")
    threshold = number(line)
    # Decimal comparisons preserve the exact incoming line before float math.
    if threshold.copy_abs() > 1_000_000:
        raise ValueError("only_bounded_integer_or_half_lines_supported")
    if operator not in ("over", "under", "at_least"):
        raise ValueError("unsupported_payoff_operator")
    integer = threshold == threshold.to_integral_value()
    if not integer and threshold != Decimal(math.floor(threshold)) + Decimal("0.5"):
        raise ValueError("only_bounded_integer_or_half_lines_supported")
    if operator == "at_least" and not integer:
        raise ValueError("integer_ladder_required")
    dist = (stats.nbinom(1 / alpha, 1 / (1 + alpha * mu))
            if alpha > 1e-8 else stats.poisson(mu))
    value = float(threshold)
    if operator == "at_least":
        win, loss, push = float(dist.sf(value - 1)), float(dist.cdf(value - 1)), 0.0
    else:
        below = float(dist.cdf(math.ceil(value) - 1))
        above = float(dist.sf(math.floor(value)))
        push = float(dist.pmf(value)) if integer else 0.0
        win, loss = (above, below) if operator == "over" else (below, above)
    if (any(not math.isfinite(p) or not 0 <= p <= 1 for p in (win, loss, push))
            or not math.isclose(math.fsum((win, loss, push)), 1, rel_tol=0, abs_tol=1e-10)):
        raise ValueError("invalid_probability_mass")
    fair = 1 + loss / win if win > 0 else None
    if fair is not None and not math.isfinite(fair):
        fair = None
    odds_text, ev = None, None
    if decimal_odds is not None:
        odds = number(decimal_odds)
        if odds <= 1 or odds > Decimal("1e12"):
            raise ValueError("invalid_scenario_decimal_odds")
        odds_text = numeric_text(odds)
        ev = win * (float(odds) - 1) - loss
    return {"version": VERSION, "state": "hypothetical_payoff_only",
            "forecast": asdict(forecast), "operator": operator, "line": numeric_text(threshold),
            "p_win": win, "p_push": push, "p_loss": loss, "p_void": None,
            "fair_decimal_odds": fair, "scenario_decimal_odds": odds_text,
            "scenario_net_ev_per_unit": ev,
            "production_props_enabled": False, "publishable": False,
            "activation_blockers": ["retrospective_model_not_approved",
                "historical_availability_not_authenticated", "current_participation_not_verified",
                "exact_offered_contract_and_identity_bridge_not_verified",
                "sportsbook_settlement_rules_not_verified"],
            "claim": "Model/payoff arithmetic only; no offered bet, participation weight, measured profit or execution eligibility."}
