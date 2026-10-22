"""D06 offline retrospective player baseline experiment; never a prop model API.

Only run_study is the entry point: it archives exact bytes and the fixed plan
before any fit. No market features, source fetching, promotion or runtime hooks.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
import pyarrow
import pyarrow.parquet as pq
import scipy
from scipy import stats

from .player_history import _identifier, _json_object, _number, _time


@dataclass(frozen=True)
class Metric:
    league: str
    group: str
    field: str
    kind: str
    namespace: str
    scope: str

    @property
    def key(self):
        return f"{self.league}/{self.group}/{self.field}"


# Deliberately bounded, exact source fields, not a generic arbitrary-feature API.
METRICS = (
    Metric("NBA", "player", "points", "count", "espn", "full_game_including_overtime"),
    Metric("WNBA", "player", "points", "count", "espn", "full_game_including_overtime"),
    Metric("NHL", "player", "shots_on_goal", "count", "nhl",
           "full_game_including_overtime_excluding_shootout"),
    Metric("MLB", "hitting", "hits", "count", "mlbam", "full_game_including_extra_innings"),
    Metric("MLB", "pitching", "strikeOuts", "count", "mlbam", "full_game_including_extra_innings"),
    Metric("NFL", "player", "rushing_yards", "continuous", "nflverse-gsis",
           "full_game_including_overtime"),
)
CARD_NAMES = ("K02-fuggetlen-modell.md", "K03-adat-ido-forras.md", "K08-validacio.md")
# These are experiment sufficiency checks, not betting/promotion thresholds.
MIN_ROWS, MIN_GAMES = 100, 10
SHRINKAGE = 5.0


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(body):
    return hashlib.sha256(body).hexdigest()


def _write(path, value):
    path.write_text(_json(value) + "\n", encoding="utf-8")


def _jsonl(path, rows):
    # Deterministic gzip bytes; every record retains its original parquet index.
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
        for row in rows:
            stream.write((_json(row) + "\n").encode())


def _validate(row, spec, as_of):
    for name in ("player_id", "team_id", "game_id"):
        if not _identifier(row.get(name)):
            return "invalid_" + name
    if not row["game_id"].startswith(spec.league + ":") or row["game_id"] == spec.league + ":":
        return "invalid_game_id"
    if row.get("identity_namespace") != spec.namespace:
        return "identity_namespace_mismatch"
    if row.get("scope") != "player_full_game" or row.get("stat_scope") != spec.scope:
        return "scope_mismatch"
    for name in ("source_sha256", "game_identity_sha256"):
        if not isinstance(row.get(name), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", row[name]):
            return "invalid_" + name
    if not isinstance(row.get("source_url"), str) or not row["source_url"].startswith("https://"):
        return "missing_source_url"
    try:
        start = _time(row.get("start"))
    except (ValueError, TypeError, OverflowError):
        return "invalid_start"
    if start >= as_of:
        return "start_not_before_as_of"
    # Observation time is a conservative as-of bound, never backdated publication.
    for name in ("available_at", "source_fetched_at", "game_identity_fetched_at", "source_published_at"):
        if row.get(name) is not None:
            try:
                stamp = _time(row[name])
            except (ValueError, TypeError, OverflowError):
                return "invalid_" + name
            if stamp > as_of:
                return name + "_after_as_of"
            if name == "available_at" and stamp <= start:
                return "availability_not_after_start"
    if row.get("played") is not None and type(row["played"]) is not bool:
        return "invalid_played"
    if row.get("played") is False:
        return "recorded_dnp"
    try:
        values = json.loads(row["stats"], object_pairs_hook=_json_object) if isinstance(row.get("stats"), str) else row.get("stats")
    except (ValueError, TypeError):
        return "invalid_stats"
    if not isinstance(values, dict):
        return "invalid_stats"
    y = values.get(spec.field)
    if y is None:
        return "missing_target"
    if not _number(y):
        return "invalid_target"
    if spec.kind == "count" and (y < 0 or y != int(y)):
        return "invalid_count_target"
    return None


def _prepare(table, specs, selection_start, test_start, as_of):
    lookup = {(s.league, s.group): s for s in specs}
    groups, audit = defaultdict(list), []
    duplicate_keys, game_starts = defaultdict(list), defaultdict(set)
    for batch in table.to_batches(max_chunksize=8192):
        for row in batch.to_pylist():
            index = len(audit)
            spec = lookup.get((row.get("league"), row.get("stat_group")))
            entry = dict(row_index=index, game_id=row.get("game_id"), player_id=row.get("player_id"),
                         metric=spec.key if spec else None, disposition="excluded", reason=None,
                         start=row.get("start"), available_at=row.get("available_at"),
                         source_published_at=row.get("source_published_at"),
                         source_fetched_at=row.get("source_fetched_at"),
                         game_identity_fetched_at=row.get("game_identity_fetched_at"))
            audit.append(entry)
            reason = _validate(row, spec, as_of) if spec else "outside_requested_metrics"
            if reason:
                entry["reason"] = reason
                continue
            start = _time(row["start"])
            values = json.loads(row["stats"], object_pairs_hook=_json_object) if isinstance(row["stats"], str) else row["stats"]
            split = "train" if start < selection_start else "selection" if start < test_start else "test"
            limitations = []
            available = _time(row["available_at"]) if row.get("available_at") else None
            if available is None:
                limitations.append("missing_availability")
            if row.get("availability_basis") not in ("source_publication", "archived_observation") or not _identifier(row.get("availability_reference")):
                limitations.append("missing_availability_evidence")
            if row.get("source_published_at") is None:
                limitations.append("publication_time_unknown")
            if available and available >= (selection_start if split == "train" else test_start if split == "selection" else as_of):
                limitations.append("observation_not_available_before_fit_cutoff" if split != "test" else "observation_at_as_of_boundary")
            identity_time = row.get("game_identity_fetched_at")
            if identity_time is None or _time(identity_time) >= (selection_start if split == "train" else test_start if split == "selection" else as_of):
                limitations.append("identity_not_observed_before_fit_cutoff")
            entry.update(disposition=split, timestamp_limitations=limitations,
                         available_at=row.get("available_at"), source_published_at=row.get("source_published_at"),
                         played=row.get("played"))
            item = dict(index=index, game=row["game_id"], player=row["player_id"], team=row["team_id"],
                        start=start.isoformat(), y=float(values[spec.field]), split=split)
            groups[spec.key].append(item)
            duplicate_keys[(spec.key, row["game_id"], row["player_id"])].append(index)
            game_starts[row["game_id"]].add(start)
    duplicates = {i for indices in duplicate_keys.values() if len(indices) > 1 for i in indices}
    for items in groups.values():
        for item in items:
            reason = "conflicting_game_start" if len(game_starts[item["game"]]) > 1 else "duplicate_player_game" if item["index"] in duplicates else None
            if reason:
                audit[item["index"]].update(disposition="excluded", reason=reason)
    groups = {key: sorted((r for r in rows if audit[r["index"]]["disposition"] != "excluded"),
                         key=lambda r: (r["start"], r["game"], r["player"], r["index"]))
              for key, rows in groups.items()}
    return groups, audit


def _fit(rows, recipe):
    y = np.array([r["y"] for r in rows])
    global_mean = float(y.mean())
    sums, counts = defaultdict(float), Counter()
    for row in rows:
        # Team changes are not silently joined into current-role histories.
        key = _json([row["player"], row["team"]])
        sums[key] += row["y"]
        counts[key] += 1
    means = {key: (sums[key] + SHRINKAGE * global_mean) / (counts[key] + SHRINKAGE) for key in counts}
    if recipe.startswith("pooled_"):
        means = {}
    mu = np.array([means.get(_json([r["player"], r["team"]]), global_mean) for r in rows])
    residual = y - mu
    model = dict(recipe=recipe, global_mean=global_mean, player_team_means=means,
                 shrinkage=SHRINKAGE, training_rows=len(rows))
    if recipe.endswith("nb"):
        # NB2 moment dispersion: Var(Y|mu)=mu+alpha*mu^2. Alpha=0 uses Poisson.
        model["alpha"] = max(0.0, float(np.sum(residual ** 2 - y) / max(float(np.sum(mu ** 2)), 1e-12)))
    elif recipe.endswith("normal"):
        model["scale"] = max(1.0, float(np.sqrt(np.mean(residual ** 2))))
    elif recipe.endswith("laplace"):
        model["scale"] = max(1.0, float(np.mean(np.abs(residual))))
    return model


def _score(model, rows):
    y = np.array([r["y"] for r in rows])
    mu = np.array([model["player_team_means"].get(_json([r["player"], r["team"]]), model["global_mean"]) for r in rows])
    recipe = model["recipe"]
    if recipe.endswith(("poisson", "nb")):
        mu = np.maximum(mu, 1e-9)  # numeric zero-mean floor, recorded in plan
        alpha = model.get("alpha", 0)
        dist = stats.nbinom(1 / alpha, 1 / (1 + alpha * mu)) if alpha > 1e-8 else stats.poisson(mu)
        loss = -dist.logpmf(y)
    else:
        dist = (stats.norm if recipe.endswith("normal") else stats.laplace)(loc=mu, scale=model["scale"])
        loss = -dist.logpdf(y)
    low, high = dist.ppf(.1), dist.ppf(.9)
    if not all(np.isfinite(v).all() for v in (loss, low, high, mu)):
        raise ValueError("nonfinite_model_score")
    event_loss = defaultdict(list)
    for row, value in zip(rows, loss):
        event_loss[row["game"]].append(float(value))
    metrics = dict(rows=len(rows), games=len(event_loss),
                   game_balanced_nll=float(np.mean([np.mean(v) for v in event_loss.values()])),
                   row_nll=float(np.mean(loss)), mae=float(np.mean(np.abs(y - mu))),
                   rmse=float(np.sqrt(np.mean((y - mu) ** 2))),
                   interval_80_coverage=float(np.mean((y >= low) & (y <= high))))
    if not all(np.isfinite(v) for v in metrics.values()):
        raise ValueError("nonfinite_model_score")
    predictions = [dict(row_index=r["index"], game_id=r["game"], mean=float(m),
                        actual=r["y"], nll=float(n), lower_80=float(l), upper_80=float(h))
                   for r, m, n, l, h in zip(rows, mu, loss, low, high)]
    return metrics, predictions


def _evaluate(spec, rows, audit, output):
    splits = {name: [r for r in rows if r["split"] == name] for name in ("train", "selection", "test")}
    result = dict(metric=spec.key, kind=spec.kind, production_props_enabled=False,
                  split_counts={k: dict(rows=len(v), games=len({r["game"] for r in v}),
                                        first_start=v[0]["start"] if v else None,
                                        last_start=v[-1]["start"] if v else None)
                                for k, v in splits.items()})
    limitations = Counter(t for r in rows for t in audit[r["index"]]["timestamp_limitations"])
    result["timestamp_limitations"] = dict(limitations)
    reasons = [f"insufficient_{name}_rows_or_games" for name, data in result["split_counts"].items()
               if data["rows"] < MIN_ROWS or data["games"] < MIN_GAMES]
    result["validation_gate"] = dict(accepted=False, reasons=["retrospective_study_only", "historical_availability_not_authenticated"] + reasons + sorted(limitations))
    if reasons:
        result["state"] = "rejected_insufficient_evidence"
        return result
    candidates = ("pooled_poisson", "player_poisson", "player_nb") if spec.kind == "count" else ("pooled_normal", "player_normal", "player_laplace")
    result["selection"] = {}
    folder = output / spec.key.replace("/", "_")
    folder.mkdir()
    try:
        for name in candidates:
            # Membership + all raw inputs + source code were durably archived before entry.
            model = _fit(splits["train"], name)
            _write(folder / (name + "-selection-model.json"), model)
            result["selection"][name], _ = _score(model, splits["selection"])
        selected = min(candidates, key=lambda n: (result["selection"][n]["game_balanced_nll"], candidates.index(n)))
        result["selected_recipe"] = selected
        # Commit choice BEFORE evaluating any outer-test target.
        _write(folder / "selection-lock.json", dict(selected_recipe=selected, selection=result["selection"],
                                                     test_used_for_selection=False))
        result["test"] = {}
        for name in dict.fromkeys((candidates[0], selected)):
            model = _fit(splits["train"] + splits["selection"], name)
            _write(folder / (name + "-test-model.json"), model)
            metrics, predictions = _score(model, splits["test"])
            result["test"][name] = metrics
            _jsonl(folder / (name + "-test-predictions.jsonl.gz"), predictions)
        result["state"] = "retrospective_comparison_completed"
    except (ValueError, FloatingPointError, OverflowError) as exc:
        result["state"] = "rejected_numerical_evidence"
        result["validation_gate"]["reasons"].append(str(exc))
    return result


def run_study(snapshot: Path, output: Path, *, selection_start: str, test_start: str,
              as_of: str, metric_keys=None, knowledge_root: Path | None = None):
    """Archive a supplied verified-history run and execute a fixed shadow plan.

    output must be a NEW directory. Existing studies/holdouts are never overwritten.
    Even apparently sufficient fixtures cannot authenticate historical availability.
    """
    snapshot, output = Path(snapshot), Path(output)
    cuts = tuple(_time(v) for v in (selection_start, test_start, as_of))
    if not cuts[0] < cuts[1] < cuts[2]:
        raise ValueError("require selection_start < test_start < as_of")
    keys = tuple(metric_keys) if metric_keys is not None else tuple(s.key for s in METRICS)
    if not keys or len(set(keys)) != len(keys) or set(keys) - {s.key for s in METRICS}:
        raise ValueError("only unique allowlisted metric keys are supported")
    specs = tuple(s for s in METRICS if s.key in keys)
    manifest_bytes = (snapshot / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    observation_bytes = (snapshot / "observations.parquet").read_bytes()
    evidence = manifest.get("files", {}).get("observations.parquet", {})
    if evidence.get("sha256") != _hash(observation_bytes) or evidence.get("bytes") != len(observation_bytes):
        raise ValueError("snapshot_observation_hash_or_size_mismatch")
    # Read only the captured bytes; subsequent source changes cannot alter fitting.
    import pyarrow as pa
    table = pq.read_table(pa.BufferReader(observation_bytes))
    if manifest.get("observation_count") != table.num_rows:
        raise ValueError("snapshot_row_count_mismatch")
    cards_root = knowledge_root or Path(__file__).resolve().parents[2] / "knowledge" / "cards"
    cards = {name: (cards_root / name).read_bytes() for name in CARD_NAMES}
    source_paths = (Path(__file__), Path(__file__).with_name("player_history.py"))
    sources = {p.name: p.read_bytes() for p in source_paths}
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    (inputs / "observations.parquet").write_bytes(observation_bytes)
    (inputs / "manifest.json").write_bytes(manifest_bytes)
    for name, body in sources.items():
        (inputs / name).write_bytes(body)
    for name, body in cards.items():
        (inputs / name).write_bytes(body)
    # Retain upstream exclusions; this study cannot reconstruct rows absent upstream.
    upstream_files = {}
    for name in ("quarantine.json", "sources.json", "game-identities.json"):
        expected = manifest.get("files", {}).get(name)
        if expected:
            body = (snapshot / name).read_bytes()
            if expected.get("sha256") != _hash(body) or expected.get("bytes") != len(body):
                raise ValueError("snapshot_evidence_hash_or_size_mismatch:" + name)
            (inputs / name).write_bytes(body)
            upstream_files[name] = _hash(body)
    plan = dict(version="d06-shadow-v1", mode="retrospective_descriptive", as_of=cuts[2].isoformat(),
                selection_start=cuts[0].isoformat(), test_start=cuts[1].isoformat(),
                split_rule="start < selection_start; selection_start <= start < test_start; test_start <= start < as_of",
                metrics=[asdict(s) for s in specs], minimum_rows=MIN_ROWS, minimum_games=MIN_GAMES,
                shrinkage=SHRINKAGE, continuous_scale_floor=1.0, count_mean_floor=1e-9,
                nb_poisson_alpha_boundary=1e-8, selection_score="game_balanced_nll",
                features=["past_stat_target", "exact_player_id", "exact_team_id"], odds_used=False,
                input_sha256=_hash(observation_bytes), manifest_sha256=_hash(manifest_bytes),
                source_sha256={k: _hash(v) for k, v in sources.items()},
                knowledge_sha256={k: _hash(v) for k, v in cards.items()}, upstream_files=upstream_files,
                python=sys.version, numpy=np.__version__, scipy=scipy.__version__, pyarrow=pyarrow.__version__)
    _write(output / "plan.json", plan)
    groups, audit = _prepare(table, specs, *cuts)
    _jsonl(output / "row-audit.jsonl.gz", audit)
    # The partition membership exists on disk before any selection/challenger fit.
    studies = [_evaluate(s, groups.get(s.key, []), audit, output) for s in specs]
    report = dict(version=plan["version"], plan_sha256=_hash((output / "plan.json").read_bytes()),
                  input_rows=table.num_rows, row_dispositions=dict(Counter(a["disposition"] for a in audit)),
                  exclusion_counts=dict(Counter(a["reason"] for a in audit if a["disposition"] == "excluded")),
                  production_props_enabled=False, odds_used=False, current_participation="unknown",
                  historical_availability_authenticated=False, studies=studies,
                  source_limitations=["Input hashes establish bytes, not historical publication or completion times.",
                                      "Source and identity timestamps remain unauthenticated claims.",
                                      "Start-time splits on retrospective corrected data are not point-in-time backtests.",
                                      "Unknown historical participation is retained when a target is observed; DNP excluded.",
                                      "Rows absent or rejected upstream cannot be reconstructed; see archived manifest/quarantine.",
                                      "No current participation, exact market contract, settlement, NCAA or profit validation."])
    _write(output / "report.json", report)
    artifacts = {str(p.relative_to(output)): _hash(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file()}
    _write(output / "artifacts.json", artifacts)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    for name in ("selection-start", "test-start", "as-of"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    report = run_study(args.snapshot, args.output, selection_start=args.selection_start,
                       test_start=args.test_start, as_of=args.as_of)
    print(_json({"report": str(args.output / "report.json"), "rows": report["input_rows"],
                 "states": {s["metric"]: s["state"] for s in report["studies"]},
                 "production_props_enabled": False}))


if __name__ == "__main__":
    main()
