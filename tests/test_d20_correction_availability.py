"""A corrected snapshot cannot borrow an earlier body's observation time.

Mutations below are negative/contract fixtures, never additional observations.
"""
import copy
import json
from datetime import datetime, timedelta

from betting_engine.native_event_sequence import load_archive
from test_d20_recovered_native import ROOT, bundle, goal, manifest, source


def test_later_correction_keeps_original_body_and_observation_cutoff(tmp_path, manifest):
    entry = source(manifest, '2022020543')
    original = load_archive(ROOT, entry['source_sha256'])
    gid = 'NHL:2022020543'
    observed = entry['available_at']
    before = original.targets(gid, as_of=observed, subject='player')
    assert before['status'] == 'ready'

    corrected_entry = copy.deepcopy(entry)
    later = (datetime.fromisoformat(observed) + timedelta(hours=1)).isoformat()
    corrected_entry.update(available_at=later, fetched_at=later)
    body = json.loads((ROOT / entry['blob_path']).read_bytes())
    credit = goal(body)['details']
    credit['scoringPlayerId'], credit['assist1PlayerId'] = (
        credit['assist1PlayerId'], credit['scoringPlayerId'])
    digest = bundle(tmp_path, corrected_entry, json.dumps(body).encode(), len(body['plays']))
    corrected = load_archive(tmp_path, digest)

    unavailable = corrected.targets(gid, as_of=observed, subject='player')
    assert unavailable['status'] == 'rejected'
    assert unavailable['reason'] == 'evidence_not_available_at_cutoff'
    assert unavailable['sequence'] is unavailable['event_count'] is None
    assert unavailable['first'] is unavailable['last'] is None

    after = corrected.targets(gid, as_of=later, subject='player')
    assert after['status'] == 'ready'
    assert after['sequence']['events'][0]['event_id'] == before['sequence']['events'][0]['event_id']
    assert after['sequence']['events'][0]['player_id'] != before['sequence']['events'][0]['player_id']
    assert after['sequence']['final_score'] == before['sequence']['final_score']
    assert after['provenance']['correction_identity'] != before['provenance']['correction_identity']
    assert after['provenance']['available_at'] == later
    assert after['provenance']['source_published_at'] is None
    assert after['sequence']['completed_at'] is None
    assert after['sequence']['correction_scope'] == 'this_snapshot_only'
    assert not after['historical_point_in_time_backtest_ready']
    assert original.targets(gid, as_of=observed, subject='player') == before
