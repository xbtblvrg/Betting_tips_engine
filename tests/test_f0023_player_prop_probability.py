"""F0023: synthetic payoff cases plus read-only supplied D06 artifact checks."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
import math
from pathlib import Path

import pytest
from scipy import stats

from betting_engine.player_model_study import METRICS
from betting_engine.player_prop_probability import (
    CountForecast, evaluate_count_payoff, load_count_forecast,
)


ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "docs/d06_player_model_study_evidence"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def forecast(**changes):
    value = CountForecast("WNBA/player/points", "espn", "full_game_including_overtime",
                          "synthetic-player", "synthetic-team", "player_poisson", 2., 0.,
                          "player_team", "a" * 64, "b" * 64, "c" * 64, "d" * 64,
                          "2025-02-01T00:00:00Z", "2025-03-01T00:00:00Z",
                          ("synthetic_fixture_only",))
    return replace(value, **changes)


def scenario(**kw):
    return evaluate_count_payoff(forecast(), **kw)


def synthetic_archive(tmp_path, *, recipe="player_nb"):
    """D06's actual serialized schema; every value here is synthetic."""
    root = tmp_path / "study"
    root.mkdir()
    folder = "WNBA_player_points"
    (root / folder).mkdir()
    plan = dict(mode="retrospective_descriptive", odds_used=False,
                metrics=[asdict(METRICS[1])], count_mean_floor=1e-9,
                nb_poisson_alpha_boundary=1e-8, shrinkage=5.,
                selection_start="2025-01-01T00:00:00Z", test_start="2025-02-01T00:00:00Z",
                as_of="2025-03-01T00:00:00Z", input_sha256="d" * 64)
    selection = {r: {"game_balanced_nll": 1 if r == recipe else 2}
                 for r in ("pooled_poisson", "player_poisson", "player_nb")}
    model = dict(recipe=recipe, global_mean=2., player_team_means={'["p","t"]': 3.},
                 shrinkage=5., training_rows=200)
    if recipe == "player_nb":
        model["alpha"] = .5
    if recipe == "pooled_poisson":
        model["player_team_means"] = {}
    study = dict(metric="WNBA/player/points", state="retrospective_comparison_completed",
                 validation_gate=dict(accepted=False, reasons=["historical_availability_not_authenticated"]),
                 selected_recipe=recipe, selection=selection,
                 split_counts={s: {"rows": 100} for s in ("train", "selection", "test")})
    def write(name, value):
        (root / name).write_text(json.dumps(value))
    write("plan.json", plan)
    write("report.json", dict(plan_sha256=digest((root / "plan.json").read_bytes()), odds_used=False,
                               production_props_enabled=False, historical_availability_authenticated=False,
                               studies=[study]))
    write(folder + "/selection-lock.json", dict(selected_recipe=recipe, selection=selection,
                                                test_used_for_selection=False))
    write(folder + "/" + recipe + "-test-model.json", model)
    return root, repin(root)


def repin(root):
    index = {str(p.relative_to(root)): digest(p.read_bytes())
             for p in root.rglob("*.json") if p.name != "artifacts.json"}
    body = json.dumps(index).encode()
    (root / "artifacts.json").write_bytes(body)
    return digest(body)


def load(root, pin, **kw):
    return load_count_forecast(root, artifact_index_sha256=pin,
                               metric_key=kw.pop("metric_key", "WNBA/player/points"),
                               player_id=kw.pop("player_id", "p"), team_id=kw.pop("team_id", "t"), **kw)


def test_poisson_integer_mass_refund_and_fair_price():
    result = scenario(operator="over", line="2", decimal_odds="2")
    mass0, mass1, mass2 = math.exp(-2), 2 * math.exp(-2), 2 * math.exp(-2)
    assert result["p_push"] == pytest.approx(mass2)
    assert result["p_loss"] == pytest.approx(mass0 + mass1)
    assert result["p_win"] == pytest.approx(1 - mass0 - mass1 - mass2)
    assert result["scenario_net_ev_per_unit"] == pytest.approx(result["p_win"] - result["p_loss"])
    assert result["scenario_net_ev_per_unit"] != pytest.approx(2 * result["p_win"] - 1)
    assert scenario(operator="over", line=2, decimal_odds=result["fair_decimal_odds"])["scenario_net_ev_per_unit"] == pytest.approx(0, abs=1e-14)


def test_complements_changed_lines_and_inclusive_ladders():
    over = scenario(operator="over", line=2)
    under = scenario(operator="under", line=2)
    half = scenario(operator="over", line="1.5")
    ladder = scenario(operator="at_least", line=2)
    assert over["p_win"] == under["p_loss"]
    assert over["p_loss"] == under["p_win"]
    assert over["p_push"] == under["p_push"]
    assert ladder["p_push"] == half["p_push"] == 0
    assert ladder["p_win"] == half["p_win"] == pytest.approx(over["p_win"] + over["p_push"])
    assert over["p_win"] < half["p_win"]


def test_nb2_geometric_special_case_and_poisson_boundary():
    result = evaluate_count_payoff(forecast(recipe="player_nb", mean=3., alpha=1.), operator="over", line=1)
    assert (result["p_loss"], result["p_push"], result["p_win"]) == pytest.approx((.25, .1875, .5625))
    for alpha in (0., 1e-8):
        a = evaluate_count_payoff(forecast(recipe="player_nb", alpha=alpha), operator="over", line=2)
        assert a["p_push"] == scenario(operator="over", line=2)["p_push"]


@pytest.mark.parametrize("line", ["-2", "-0.5", "0", "0.5", "1", "2", "3.5", "1000"])
def test_full_probability_mass_without_grid_truncation(line):
    for op in ("over", "under"):
        result = scenario(operator=op, line=line)
        assert math.fsum(result[k] for k in ("p_win", "p_loss", "p_push")) == pytest.approx(1, abs=1e-14)
    if line == "1000":
        result = scenario(operator="over", line=line)
        assert result["fair_decimal_odds"] is None


def test_tiny_tail_uses_survival_not_one_minus_rounded_cdf():
    result = scenario(operator="over", line=30)
    assert stats.poisson(2).cdf(30) == 1
    assert result["p_win"] > 0


@pytest.mark.parametrize("line", [True, None, "nan", "inf", "2.25", "2.50000000000000000000000000000000001", "-0.25", "1000001"])
def test_invalid_or_split_lines_never_rounded_into_a_contract(line):
    with pytest.raises(ValueError):
        scenario(operator="over", line=line)


@pytest.mark.parametrize("odds", [True, 1, 0, -2, "nan", "inf", "1000000000001"])
def test_invalid_scenario_prices_reject(odds):
    with pytest.raises(ValueError):
        scenario(operator="over", line=2, decimal_odds=odds)


def test_odds_cannot_change_forecast_or_probabilities_and_no_activation():
    model = forecast()
    before = asdict(model)
    a = evaluate_count_payoff(model, operator="over", line=2, decimal_odds=1.5)
    b = evaluate_count_payoff(model, operator="over", line=2, decimal_odds=4)
    assert a["forecast"] == b["forecast"] == before == asdict(model)
    for key in ("p_win", "p_loss", "p_push", "fair_decimal_odds"):
        assert a[key] == b[key]
    assert a["scenario_net_ev_per_unit"] < b["scenario_net_ev_per_unit"]
    assert not a["publishable"] and not a["production_props_enabled"]
    assert a["p_void"] is None and len(a["activation_blockers"]) == 5
    with pytest.raises(FrozenInstanceError):
        model.mean = 6


@pytest.mark.parametrize("changes", [dict(namespace="sr"), dict(stat_scope="quarter_1"),
    dict(metric_key="NCAAM/player/points"), dict(recipe="player_normal"),
    dict(mean=float("nan")), dict(mean=-1), dict(mean=True), dict(alpha=-1), dict(alpha=.5)])
def test_unsupported_forecasts_reject(changes):
    with pytest.raises(ValueError):
        evaluate_count_payoff(forecast(**changes), operator="over", line=2)


def test_unknown_operators_and_noninteger_ladder_reject():
    for op, line in [("yes", 1), ("no", 1), ("at_least", 2.5), ("anything", 1)]:
        with pytest.raises(ValueError):
            scenario(operator=op, line=line)


@pytest.mark.parametrize("recipe", ["pooled_poisson", "player_poisson", "player_nb"])
def test_selection_locked_artifact_adapter_and_team_change(tmp_path, recipe):
    root, pin = synthetic_archive(tmp_path, recipe=recipe)
    f = load(root, pin)
    assert f.mean == (2 if recipe == "pooled_poisson" else 3)
    other = load(root, pin, team_id="new-team")
    assert other.mean == 2 and other.mean_basis == "pooled_fallback"
    assert other.validation_reasons == ("historical_availability_not_authenticated",)
    assert other.fit_event_start_cutoff == "2025-02-01T00:00:00Z"
    assert other.study_as_of == "2025-03-01T00:00:00Z"


@pytest.mark.parametrize("name", ["artifacts.json", "plan.json", "report.json",
    "WNBA_player_points/selection-lock.json", "WNBA_player_points/player_nb-test-model.json"])
def test_tampered_control_or_model_bytes_reject(tmp_path, name):
    root, pin = synthetic_archive(tmp_path)
    with (root / name).open("ab") as out:
        out.write(b" ")
    with pytest.raises(ValueError, match="hash_mismatch"):
        load(root, pin)


@pytest.mark.parametrize("target,change", [
    ("selection-lock.json", lambda x: x.update(test_used_for_selection=True)),
    ("selection-lock.json", lambda x: x.update(selected_recipe="player_poisson")),
    ("player_nb-test-model.json", lambda x: x.update(recipe="player_normal")),
    ("player_nb-test-model.json", lambda x: x.update(alpha=-1)),
    ("player_nb-test-model.json", lambda x: x.update(training_rows=201)),
    ("player_nb-test-model.json", lambda x: x.update(global_mean=-1)),
    ("player_nb-test-model.json", lambda x: x.update(odds=2)),
    ("player_nb-test-model.json", lambda x: x.update(player_team_means={'["p", "t"]': 3})),
])
def test_rehashed_invalid_semantics_still_reject(tmp_path, target, change):
    root, _ = synthetic_archive(tmp_path)
    p = root / "WNBA_player_points" / target
    value = json.loads(p.read_bytes()); change(value); p.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load(root, repin(root))


def test_symlink_artifact_rejects(tmp_path):
    root, pin = synthetic_archive(tmp_path)
    path = root / "plan.json"
    body = path.read_bytes(); path.unlink()
    external = tmp_path / "other.json"; external.write_bytes(body); path.symlink_to(external)
    with pytest.raises(ValueError, match="unsafe_artifact_path"):
        load(root, pin)


def test_duplicate_json_fields_reject(tmp_path):
    root, _ = synthetic_archive(tmp_path)
    path = root / "WNBA_player_points/player_nb-test-model.json"
    body = path.read_text(); path.write_text('{"alpha":0,' + body[1:])
    with pytest.raises(ValueError, match="duplicate_json_field"):
        load(root, repin(root))


@pytest.mark.parametrize("change", [
    lambda p: p.update(odds_used=True),
    lambda p: p.update(test_start=p["selection_start"]),
    lambda p: p.update(count_mean_floor=.1),
    lambda p: p["metrics"][0].update(scope="quarter_1"),
])
def test_rehashed_plan_cannot_relax_independence_or_scope(tmp_path, change):
    root, _ = synthetic_archive(tmp_path)
    path = root / "plan.json"
    plan = json.loads(path.read_bytes()); change(plan); path.write_text(json.dumps(plan))
    path = root / "report.json"
    report = json.loads(path.read_bytes())
    report["plan_sha256"] = digest((root / "plan.json").read_bytes())
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        load(root, repin(root))


def test_outer_test_scores_do_not_choose_recipe_or_change_forecast(tmp_path):
    root, pin = synthetic_archive(tmp_path)
    before = load(root, pin)
    path = root / "report.json"
    report = json.loads(path.read_bytes())
    report["studies"][0]["test"] = {"player_nb": {"game_balanced_nll": 999999},
                                   "pooled_poisson": {"game_balanced_nll": 0}}
    path.write_text(json.dumps(report))
    after = load(root, repin(root))
    assert before.recipe == after.recipe == "player_nb"
    assert before.mean == after.mean and before.alpha == after.alpha
    assert before.model_sha256 == after.model_sha256


def test_missing_artifacts_are_not_synthesized(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "absent", "a" * 64)


@pytest.mark.parametrize("spec", [s for s in METRICS if s.kind == "count"], ids=lambda s: s.key)
def test_supplied_selected_model_schema_without_refit_or_market_join(spec):
    pin = digest((STUDY / "artifacts.json").read_bytes())
    folder = STUDY / spec.key.replace("/", "_")
    lock = json.loads((folder / "selection-lock.json").read_bytes())
    model = json.loads((folder / (lock["selected_recipe"] + "-test-model.json")).read_bytes())
    key = next(iter(model["player_team_means"]))
    player, team = json.loads(key)
    result = load(STUDY, pin, metric_key=spec.key, player_id=player, team_id=team)
    assert result.mean == max(model["player_team_means"][key], 1e-9)
    assert result.namespace == spec.namespace and result.stat_scope == spec.scope
    assert "historical_availability_not_authenticated" in result.validation_reasons


def test_nfl_continuous_and_ncaa_absent_models_are_not_integer_prop_models():
    for key, reason in [("NFL/player/rushing_yards", "discrete_distribution_required"),
                        ("NCAAM/player/points", "unsupported_metric")]:
        with pytest.raises(ValueError, match=reason):
            load(STUDY, digest((STUDY / "artifacts.json").read_bytes()), metric_key=key)


@pytest.mark.parametrize("operator,line", [("over", "2"), ("under", "2"),
    ("over", "2.5"), ("under", "2.5"), ("at_least", "2")])
def test_expected_payoff_matches_existing_settlement_for_synthetic_outcomes(operator, line):
    # Reuse unchanged settlement fixtures, explicitly synthetic. The existing
    # function is a pure evaluator; no ledger or production service is opened.
    from test_player_prop_settlement import case, settle
    weighted_profit = 0.
    fixture = case(operator, line, 0)
    for count in range(31):
        args = deepcopy(fixture)
        args[1]["value"] = count
        body = json.dumps({"test_fixture": "synthetic count", "stat": count}).encode()
        args[1]["source"]["sha256"] = digest(body)
        args[3][digest(body)] = body
        original = deepcopy(args[:3])
        result = settle(args)
        assert result["state"] == "settled" and args[:3] == original
        weighted_profit += stats.poisson(2).pmf(count) * float(result["profit"]) / 2.25
    result = scenario(operator=operator, line=line, decimal_odds="1.84")
    assert result["scenario_net_ev_per_unit"] == pytest.approx(weighted_profit, abs=1e-13)


def test_dnp_void_is_separate_from_integer_push_and_has_no_invented_probability():
    from test_player_prop_settlement import case, settle
    args = case("over", "2", None)
    args[1]["participation"] = {"played": False, "final": True}
    assert settle(args)["result"] == "void"
    result = scenario(operator="over", line=2)
    assert result["p_void"] is None and result["p_push"] > 0
