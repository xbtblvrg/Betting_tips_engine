import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.outcome_feedback import (record_outcome_feedback, read_outcome_feedback,
    snapshot_outcomes, outcome_generation, settled_inputs, VERSION)
from betting_engine.io_utils import atomic_json
from betting_engine.storage import Store
from test_engineering_loop import run as intake, registry

NOW = datetime(2026, 9, 30, 21, tzinfo=timezone.utc)


def cohort(market='moneyline', game='NBA:g', status='lost', identity='decision1', league='NBA'):
    scope = 'full_game' if ':' not in market else market.split(':')[0]
    home, away = (90, 100) if status == 'lost' else (100, 90)
    payload = {'id': identity, 'game_id': game, 'league': league, 'market': market,
        'start': (NOW-timedelta(hours=3)).isoformat(), 'side': 'home', 'line': None,
        'stake': 1, 'reference_odds': 1.9, 'probability': .7, 'push_probability': 0,
        'model_version': 'frozen-v1', 'tracking_source': 'auto_assumed_placed'}
    evidence = {'game_id': game, 'home_score': home, 'away_score': away,
        'basis': 'verified_score_in_contract_scope', 'score_scope': {'scope': scope},
        'source_url': 'https://official.example/result', 'source_fetched_at': NOW.isoformat(),
        'settled_at': NOW.isoformat()}
    pnl = -1 if status == 'lost' else .9
    return {'bets': [{'id': 1, 'tip_id': identity, 'created_at': NOW.isoformat(),
        'stake': 1, 'odds': 1.9, 'platform': 'Stake', 'status': status, 'pnl': pnl, 'payload': payload}],
        'decisions': [{'family': identity, 'tip_id': identity, 'issued_at': NOW.isoformat(),
        'payload': payload, 'status': status, 'pnl': pnl, 'outcome_evidence': evidence}],
        'bet_outcomes': [{'bet_id': 1, 'league': league, 'market': market, 'model_version': 'frozen-v1',
        'result': status, 'probability': .7, 'push_probability': 0, 'evidence': evidence}]}


def emit(root, inputs=None, at=NOW):
    return record_outcome_feedback(root/'data', **(inputs or cohort()), as_of=at)


def loss(report):
    return next(p for p in report['proposals'] if p['kind']=='negative_original_pnl')


def test_loss_creates_specific_frozen_diagnostic_without_promotion_or_input_mutation(tmp_path):
    inputs=cohort(); before=copy.deepcopy(inputs)
    report=emit(tmp_path, inputs); item=loss(report)
    assert report['source_verified'] and item['assessment']=='diagnostic_only'
    assert item['evidence']['statistics']['settled_games']==1
    assert item['evidence']['statistics']['pnl']==-1
    assert item['evidence']['statistics']['calibration']['game_equal_weight_brier']==pytest.approx(.49)
    assert item['evidence']['original_decisions']==inputs['decisions']
    assert item['validated_improvement']['state']=='not_established'
    assert report['profit_established'] is False and inputs==before
    assert 'chronological validation' in item['acceptance']


def test_same_generation_reuses_immutable_review_deadline_and_no_new_tasks(tmp_path):
    first=emit(tmp_path); archive=tmp_path/first['review_path']; original=archive.read_bytes()
    first_intake=intake(tmp_path, NOW)
    p=loss(first); initial=registry(tmp_path)['gaps'][p['id']]
    for minutes in (1,60,120):
        report=emit(tmp_path,at=NOW+timedelta(minutes=minutes))
        result=intake(tmp_path,NOW+timedelta(minutes=minutes))
        assert report['review_sha256']==first['review_sha256']
        assert loss(report)['due_at']==p['due_at']
        assert result['extra_tasks']==first_intake['extra_tasks']
    assert archive.read_bytes()==original
    assert registry(tmp_path)['gaps'][p['id']]['observations']==initial['observations']==1
    assert len(list((tmp_path/'data/outcome_feedback/reviews').glob('*.json')))==1


def test_later_cohort_reuses_identity_preserves_first_deadline_and_embeds_new_records(tmp_path):
    first=emit(tmp_path); inputs=cohort()
    extra=cohort(game='NBA:second',identity='decision2')
    inputs['decisions']+=extra['decisions']
    later=emit(tmp_path,inputs,NOW+timedelta(hours=1))
    a,b=loss(first),loss(later)
    assert a['id']==b['id'] and a['due_at']==b['due_at'] and a['first_seen']==b['first_seen']
    assert a['evidence_sha256']!=b['evidence_sha256'] and b['evidence']['statistics']['settled_games']==2


def test_improving_aggregate_does_not_erase_past_loss_or_close_issue(tmp_path):
    original=emit(tmp_path); inputs=cohort()
    for n in range(3): inputs['decisions']+=cohort(game=f'NBA:{n}',identity=f'w{n}',status='won')['decisions']
    current=emit(tmp_path, inputs,NOW+timedelta(hours=1))
    item=loss(current)
    assert item['id']==loss(original)['id'] and not item['current_generation']
    assert item['state']=='open' and item['evidence']['statistics']['pnl']==-1


def test_same_game_contracts_do_not_become_independent_games(tmp_path):
    inputs=cohort(); inputs['decisions']+=cohort(identity='other')['decisions']
    item=loss(emit(tmp_path,inputs))
    assert item['evidence']['statistics']['settled']==2
    assert item['evidence']['statistics']['calibration']['independent_games']==1
    assert item['evidence']['game_ids']==['NBA:g']


def test_duplicate_ticket_changes_money_generation_not_original_diagnostic(tmp_path):
    inputs=cohort(); first=emit(tmp_path,inputs)
    dupe=copy.deepcopy(inputs['bets'][0]);dupe['id']=2;inputs['bets'].append(dupe)
    outcome=copy.deepcopy(inputs['bet_outcomes'][0]);outcome['bet_id']=2;inputs['bet_outcomes'].append(outcome)
    later=emit(tmp_path,inputs,NOW+timedelta(minutes=1))
    assert first['generation']!=later['generation']
    assert loss(first)['id']==loss(later)['id']
    assert loss(later)['evidence']['statistics']['pnl']==-1
    assert loss(first)['observed_at']==loss(later)['observed_at']
    assert loss(first)['evidence_sha256']==loss(later)['evidence_sha256']


def test_prospective_archive_proposal_does_not_fabricate_legacy_raw_verification(tmp_path):
    report=emit(tmp_path)
    item=next(p for p in report['proposals'] if p['kind']=='prospective_source_lineage')
    assert 'future settlements' in item['evidence']['next_action']
    assert 'without backfilling availability' in item['evidence']['next_action']
    assert item['evidence']['source_coverage']=={'retained_url_time':1}
    assert 'not independently reverified' in item['evidence']['limitation']


def test_no_result_no_performance_lesson(tmp_path):
    report=record_outcome_feedback(tmp_path/'data',[],[],[],as_of=NOW)
    assert report['state']=='awaiting_results' and report['proposals']==[]
    assert report['profit_established'] is False


def test_reader_never_creates_files_and_returns_not_started(tmp_path):
    assert read_outcome_feedback(tmp_path/'data',now=NOW)['state']=='not_started'
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('mutation',['archive','missing','symlink','deadline','hash','future'])
def test_reader_rejects_drift_corruption_or_rewritten_deadline(tmp_path,mutation):
    report=emit(tmp_path); path=tmp_path/report['review_path']; statepath=tmp_path/'data/outcome_feedback/latest.json'
    state=json.loads(statepath.read_text())
    if mutation=='archive': path.write_text(path.read_text()+' ')
    elif mutation=='missing': path.unlink()
    elif mutation=='symlink':
        original=path.read_bytes();path.unlink();target=tmp_path/'other';target.write_bytes(original);path.symlink_to(target)
    elif mutation=='deadline':
        next(iter(state['proposals'].values()))['due_at']=(NOW+timedelta(days=5)).isoformat();atomic_json(statepath,state)
    elif mutation=='hash':
        next(iter(state['proposals'].values()))['review']['sha256']='0'*64;atomic_json(statepath,state)
    elif mutation=='future': state['checked_at']=(NOW+timedelta(seconds=1)).isoformat();atomic_json(statepath,state)
    value=read_outcome_feedback(tmp_path/'data',now=NOW)
    assert not value['source_verified'] and value['state']=='evidence_invalid'


def test_corrupted_prior_state_not_silently_reset_by_writer(tmp_path):
    report=emit(tmp_path); (tmp_path/report['review_path']).write_text('{}')
    with pytest.raises(ValueError,match='existing_feedback_evidence_invalid'): emit(tmp_path,at=NOW+timedelta(minutes=1))


def test_parallel_identical_reviews_are_one_archive(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports=list(pool.map(lambda _:emit(tmp_path),range(4)))
    assert len({r['generation'] for r in reports})==1
    assert len(list((tmp_path/'data/outcome_feedback/reviews').glob('*.json')))==1


def test_outcome_intake_has_concrete_task_and_failed_source_is_actionable(tmp_path):
    report=emit(tmp_path); p=loss(report); intake(tmp_path,NOW)
    row=registry(tmp_path)['gaps'][p['id']]
    assert row['task_id']=='D18' and row['due_at']==p['due_at']
    proof=json.loads((tmp_path/row['evidence_path']).read_text())
    assert proof['gap']['evidence']['proposal']['evidence']['original_decisions']
    (tmp_path/report['review_path']).write_text('{}'); intake(tmp_path,NOW+timedelta(minutes=1))
    assert registry(tmp_path)['gaps']['SYSTEM:outcome_feedback_evidence']['state']=='open'
    assert registry(tmp_path)['gaps'][p['id']]['state']=='open'


def test_reader_stale_report_and_ready_task_do_not_claim_learning_success(tmp_path):
    report=emit(tmp_path); intake(tmp_path,NOW)
    state={'tasks':{'D18':{'state':'candidate_ready','run_id':'some-run','attempts':1}}}
    reread=read_outcome_feedback(tmp_path/'data',now=NOW+timedelta(hours=3),development_state=state)
    assert reread['state']=='stale' and loss(reread)['task']['state']=='candidate_ready'
    assert loss(reread)['validated_improvement']['state']=='not_established'


def test_snapshot_triggers_reconciliation_but_excludes_open_offer_churn(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    with store.db() as db:
        row=cohort()['decisions'][0]
        db.execute('INSERT INTO system_decisions VALUES (?,?,?,?,?,?,?)',(row['family'],row['tip_id'],row['issued_at'],json.dumps(row['payload']),row['status'],row['pnl'],json.dumps(row['outcome_evidence'])))
    first=outcome_generation(snapshot_outcomes(store))
    with store.db() as db:
        db.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,status,pnl,payload) VALUES (?,?,?,?,?,?,?,?)',('manual',NOW.isoformat(),1,2,'Stake','open',None,'{}'))
    assert outcome_generation(snapshot_outcomes(store))==first
    with store.db() as db: db.execute("UPDATE bets SET status='lost',pnl=-1")
    assert outcome_generation(snapshot_outcomes(store))!=first


def test_versioned_learning_job_runs_despite_old_hour_completed(tmp_path,monkeypatch):
    from test_learning import worker
    import betting_engine.learning as module
    monkeypatch.setattr(module,'utcnow',lambda:NOW)
    w=worker(tmp_path);old=w.enqueue('outcome_review',NOW.strftime('%Y-%m-%dT%H')+':old')
    with w.store.db() as db: db.execute("UPDATE learning_jobs SET status='completed' WHERE id=?",(old,))
    w.plan()
    rows=[j for j in w.snapshot()['jobs'] if j['kind']=='outcome_review']
    assert len(rows)==2 and any(j['payload'].get('feedback_version')==VERSION and j['status']=='queued' for j in rows)


def receipt(root,item,at=NOW+timedelta(minutes=1)):
    source=root/'proof.txt';source.write_text('independently reviewed diagnostic repair')
    value={'gap_id':item['id'],'state':'production_verified','verified_at':at.isoformat(),
        'checks':[{'passed':True}], 'artifacts':[{'path':'proof.txt','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
        'outcome_feedback_review_sha256':item['evidence_sha256'],'outcome_feedback_input_sha256':item['input_sha256']}
    path=root/'data/development/engineering-releases'/ (hashlib.sha256(item['id'].encode()).hexdigest()+'.json')
    atomic_json(path,value);return path


def test_exact_diagnostic_receipt_closes_only_that_issue_new_outcomes_reopen(tmp_path):
    first=emit(tmp_path);p=loss(first);intake(tmp_path,NOW);receipt(tmp_path,p)
    reread=read_outcome_feedback(tmp_path/'data',now=NOW+timedelta(minutes=2))
    assert loss(reread)['state']=='diagnostic_repair_verified'
    assert loss(reread)['validated_improvement']['state']=='not_established'
    intake(tmp_path,NOW+timedelta(minutes=2)); assert registry(tmp_path)['gaps'][p['id']]['state']=='resolved'
    inputs=cohort();inputs['decisions']+=cohort(identity='second')['decisions']
    emit(tmp_path,inputs,NOW+timedelta(minutes=3));intake(tmp_path,NOW+timedelta(minutes=3))
    gap=registry(tmp_path)['gaps'][p['id']]
    assert gap['state']=='open' and gap['due_at']==p['due_at'] and gap['regressions']==1


def test_receipt_without_current_review_binding_does_not_close(tmp_path):
    first=emit(tmp_path);p=loss(first);path=receipt(tmp_path,p)
    value=json.loads(path.read_text());value.pop('outcome_feedback_input_sha256');atomic_json(path,value)
    assert loss(read_outcome_feedback(tmp_path/'data',now=NOW+timedelta(minutes=2)))['state']=='open'


def test_drift_of_completed_diagnostic_receipt_reopens_original_gap(tmp_path):
    first=emit(tmp_path);p=loss(first);intake(tmp_path,NOW);receipt(tmp_path,p)
    intake(tmp_path,NOW+timedelta(minutes=2)); assert registry(tmp_path)['gaps'][p['id']]['state']=='resolved'
    (tmp_path/'proof.txt').write_text('changed proof')
    intake(tmp_path,NOW+timedelta(minutes=3))
    gap=registry(tmp_path)['gaps'][p['id']]
    assert gap['state']=='open' and gap['regressions']==1 and gap['due_at']==p['due_at']
    intake(tmp_path,NOW+timedelta(minutes=4))
    assert registry(tmp_path)['gaps'][p['id']]['regressions']==1


def test_accepted_legacy_reference_warning_not_new_settlement_defect(tmp_path):
    inputs=cohort(status='won')
    for evidence in (inputs['decisions'][0]['outcome_evidence'],inputs['bet_outcomes'][0]['evidence']):
        evidence.pop('score_scope',None);evidence['basis']='final_game_score'
    result=emit(tmp_path,inputs)
    assert not any(p['kind'] in ('outcome_integrity','ticket_resolution_audit') for p in result['proposals'])
    assert any(p['kind']=='prospective_source_lineage' for p in result['proposals'])


def test_actual_score_disagreement_still_creates_integrity_work(tmp_path):
    inputs=cohort();inputs['decisions'][0]['outcome_evidence']['home_score']=110
    result=emit(tmp_path,inputs)
    assert any(p['kind']=='outcome_integrity' for p in result['proposals'])
    assert any(p['kind']=='ticket_resolution_audit' for p in result['proposals'])


def test_orphan_and_contradictory_open_ticket_outcomes_are_actionable_not_money(tmp_path):
    inputs=cohort(status='won')
    orphan=copy.deepcopy(inputs['bet_outcomes'][0]);orphan['bet_id']=999
    inputs['bet_outcomes'].append(orphan)
    contradictory=copy.deepcopy(inputs['bets'][0]);contradictory.update(id=2,status='open',pnl=None)
    inputs['bets'].append(contradictory)
    outcome=copy.deepcopy(inputs['bet_outcomes'][0]);outcome['bet_id']=2;inputs['bet_outcomes'].append(outcome)
    report=emit(tmp_path,inputs)
    proposal=next(p for p in report['proposals'] if p['kind']=='ticket_resolution_audit')
    assert proposal['evidence']['audit']['counts']['orphan_outcome']==1
    assert proposal['evidence']['audit']['counts']['bet_outcome_status_mismatch']==1
    archived=json.loads((tmp_path/report['review_path']).read_text())
    assert len(archived['inputs']['bet_outcomes'])==3
    assert archived['statistics']['ticket_ledger']['summary']['settled']==1
    assert archived['statistics']['ticket_ledger']['summary']['pnl']==.9


def test_worker_receives_exact_historical_ticket_audit_inputs_not_only_digest(tmp_path):
    from betting_engine.development import copy_task_evidence
    inputs=cohort();inputs['bets'][0]['pnl']=-.5
    report=emit(tmp_path,inputs); proposal=next(p for p in report['proposals'] if p['kind']=='ticket_resolution_audit')
    result=intake(tmp_path,NOW)
    gap=registry(tmp_path)['gaps'][proposal['id']]
    workspace=tmp_path/'isolated-worker'
    copied=copy_task_evidence(tmp_path/'data/development',workspace,
        {'id':gap['task_id'],**result['task_focus'][gap['task_id']]})
    proof=next(json.loads((workspace/r['path']).read_text()) for r in copied
               if json.loads((workspace/r['path']).read_text())['gap']['id']==proposal['id'])
    frozen=proof['gap']['evidence']['proposal']['evidence']['inputs']
    assert frozen==settled_inputs(**inputs)
    inputs['bets'][0]['pnl']=-1  # Later reconciliation must not change worker proof.
    assert frozen['bets'][0]['pnl']==-.5
    assert copied and not json.loads((workspace/'data/engineering-evidence-manifest.json').read_text())['rejected']


@pytest.mark.parametrize('mutation',['missing','corrupt','future'])
def test_invalid_diagnostic_receipt_reopens_without_losing_valid_cohort(tmp_path,mutation):
    first=emit(tmp_path);p=loss(first);intake(tmp_path,NOW);path=receipt(tmp_path,p)
    intake(tmp_path,NOW+timedelta(minutes=2));assert registry(tmp_path)['gaps'][p['id']]['state']=='resolved'
    if mutation=='missing':path.unlink()
    elif mutation=='corrupt':path.write_text('[')
    else:
        data=json.loads(path.read_text());data['verified_at']=(NOW+timedelta(days=2)).isoformat();atomic_json(path,data)
    value=read_outcome_feedback(tmp_path/'data',now=NOW+timedelta(minutes=3))
    assert value['source_verified'] and loss(value)['state']=='open'
    intake(tmp_path,NOW+timedelta(minutes=3))
    gap=registry(tmp_path)['gaps'][p['id']]
    assert gap['state']=='open' and gap['due_at']==p['due_at']
