"""Process-restart reproducibility using identical archived Parquet bytes."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from betting_engine.player_prop_contracts import canonical_hash
from test_f0027_player_rush_reconciliation import archives, sample


@pytest.mark.parametrize("case", ["reconciled", "missing_rushers", "mixed_disagreement", "rejected"])
def test_assessment_is_identical_across_process_hash_seeds(tmp_path, case):
    plays, weekly = sample()
    if case in ("missing_rushers", "mixed_disagreement"):
        weekly = [weekly[-1]]
    if case == "mixed_disagreement":
        weekly[0]["carries"] = 1
        weekly[0]["rushing_yards"] = 2
    if case == "rejected":
        plays[-1]["total_home_score"] = 999

    # Encode once, so every subprocess receives the very same archive bytes.
    for name, archive in zip(("plays", "weekly"), archives(plays, weekly)):
        metadata = asdict(archive)
        (tmp_path / (name + ".parquet")).write_bytes(metadata.pop("body"))
        (tmp_path / (name + ".json")).write_text(json.dumps(metadata))

    script = '''
import json
from pathlib import Path
import sys
from betting_engine.player_archive import PlayerArchive
from betting_engine.player_rush_reconciliation import reconcile_nfl_rushing

root = Path(sys.argv[1])
def archive(name):
    metadata = json.loads((root / (name + ".json")).read_text())
    return PlayerArchive(body=(root / (name + ".parquet")).read_bytes(), **metadata)
result = reconcile_nfl_rushing(archive("plays"), game_id="2026_01_ARI_LAC",
    as_of="2026-09-30T06:00:00+00:00", weekly=archive("weekly"))
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
'''
    outputs = []
    for seed in ("1", "2", "3", "99"):
        env = dict(os.environ, PYTHONHASHSEED=seed, OMP_NUM_THREADS="1",
                   PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        process = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                                 env=env, capture_output=True, text=True,
                                 check=True, timeout=30)
        outputs.append(process.stdout)
    assert len(set(outputs)) == 1, "Same archive bytes must yield the same reasons and assessment hash"
    result = json.loads(outputs[0])
    digest = result.pop("assessment_sha256")
    assert digest == canonical_hash(result)
    if case in ("missing_rushers", "mixed_disagreement"):
        missing = ["rusher_missing_from_weekly:00-000000" + str(i) for i in (1, 2, 3)]
        prefix = ["weekly_credit_missing_from_plays:00-0000004"] if case == "mixed_disagreement" else []
        assert result["reasons"] == prefix + missing
        assert result["state"] == "source_disagreement"
    elif case == "rejected":
        assert result["state"] == "rejected"
        assert result["reasons"] == ["terminal_score_mismatch"]
    else:
        assert result["state"] == "historical_totals_reconciled"
        assert result["reasons"] == []
    if case != "reconciled":
        assert all(p["reconciled_value"] is None for p in result["players"])
    for field in ("publishable", "execution_eligible", "settlement_ready",
                  "production_props_enabled", "current_participation_verified"):
        assert result[field] is False
