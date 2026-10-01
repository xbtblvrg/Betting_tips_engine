from copy import deepcopy
from datetime import timedelta
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from betting_engine import instruction_refill as refill
from betting_engine.domain import utcnow, parse_time
from betting_engine.storage import Store
from test_execution_instructions import reviewed_tip


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path/'refill.sqlite3')
    result.save_settings({'auto_track_recommendations': True, 'max_bet': 1.})
    with result.db() as c: refill.initialize(c)
    return result


def publish(store, **kwargs):
    tip = reviewed_tip(**{'cap': 1., **kwargs})
    store.publish({'generated_at': utcnow().isoformat(), 'recommendations': [tip],
        'alternatives': [], 'evaluated_candidates': [tip]}, require_guard=True)
    return tip


def mark(store, identity, placed=True, now=None):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        return refill.mark_placed(c, identity, placed, now)


def claim(store, now=None):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE'); return refill.claim(c, now)


def complete(store, receipt, **kwargs):
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE'); return refill.complete(c, receipt, **kwargs)


def rows(store, table):
    with store.db() as c: return [dict(r) for r in c.execute('SELECT * FROM '+table)]


def snapshot(store, now=None):
    with store.db() as c:
        c.execute('BEGIN'); return refill.ready_snapshot(refill.TransactionStore(c), now)


def test_last_fresh_instruction_queues_without_ledger_or_issuance_change(store):
    tip = publish(store)
    assert snapshot(store)['ids'] == [tip['id']]
    tables = ('bets', 'system_decisions', 'recommendation_issuance', 'bet_outcomes')
    before = {name: rows(store, name) for name in tables}
    response = mark(store, tip['id'])
    assert response['placed'] is True
    assert snapshot(store)['ids'] == []
    assert {name: rows(store, name) for name in tables} == before
    queue = rows(store, 'instruction_refill_requests')
    assert len(queue) == 1 and queue[0]['state'] == 'queued'
    assert json.loads(queue[0]['payload'])['horizon_hours'] == 48
    assert [r['event'] for r in rows(store, 'instruction_refill_events')] == ['requested', 'depletion_observed']


@pytest.mark.parametrize('case', ['expired', 'started', 'manual', 'duplicate', 'zero', 'invalid_seal', 'withdrawn', 'historical'])
def test_nonexecutable_or_historical_marks_never_request_refill(store, case):
    tip = publish(store); now = utcnow()
    if case == 'expired': now += timedelta(minutes=6)
    elif case == 'started': now += timedelta(hours=3)
    else:
        with store.db() as c:
            if case == 'historical':
                c.execute('DELETE FROM publications')
            elif case == 'duplicate':
                r = c.execute('SELECT * FROM bets').fetchone()
                c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,status,payload) VALUES (?,?,?,?,?,?,?)',
                    (r['tip_id'],r['created_at'],r['stake'],r['odds'],r['platform'],r['status'],r['payload']))
            else:
                r = c.execute('SELECT id,payload FROM bets').fetchone(); value = json.loads(r['payload'])
                if case == 'manual': value['tracking_source'] = 'user_reported_placed'
                elif case == 'zero': c.execute('UPDATE bets SET stake=0')
                elif case == 'invalid_seal': value['decision_guard']['passed'] = False
                else: value['start'] = (now-timedelta(seconds=1)).isoformat()
                c.execute('UPDATE bets SET payload=? WHERE id=?', (json.dumps(value),r['id']))
    mark(store, tip['id'], now=now)
    assert rows(store, 'instruction_refill_requests') == []


def test_duplicate_requests_and_unmark_remark_are_permanently_idempotent(store):
    tip = publish(store); first = mark(store, tip['id'])
    before = rows(store, 'instruction_refill_events')
    assert mark(store, tip['id']) == first
    mark(store, tip['id'], False); assert snapshot(store)['ids'] == [tip['id']]
    mark(store, tip['id'])
    assert rows(store, 'instruction_refill_events') == before


def test_concurrent_repeated_marks_have_one_depletion_receipt(store):
    tip = publish(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: mark(store, tip['id']), range(12)))
    assert responses == [responses[0]]*12
    assert len(rows(store, 'instruction_refill_requests')) == 1
    assert len(rows(store, 'instruction_refill_triggers')) == 1


def test_two_instructions_only_last_mark_triggers(store):
    a = reviewed_tip(cap=1.); b = reviewed_tip(cap=1., market_kind='totals', side='over', line=210.5)
    store.publish({'generated_at':utcnow().isoformat(), 'recommendations':[a,b], 'evaluated_candidates':[a,b]}, require_guard=True)
    assert set(snapshot(store)['ids']) == {a['id'], b['id']}
    mark(store, a['id']); assert rows(store,'instruction_refill_requests') == []
    mark(store, b['id']); assert len(rows(store,'instruction_refill_requests')) == 1


def test_distinct_depletions_coalesce_while_queued(store):
    a = publish(store); mark(store,a['id'])
    b = publish(store,market_kind='totals',side='over',line=210.5); mark(store,b['id'])
    assert len(rows(store,'instruction_refill_requests')) == 1
    assert len(rows(store,'instruction_refill_triggers')) == 2


def test_during_scan_one_followup_survives_and_no_second_owner_claims(store):
    a = publish(store); mark(store,a['id']); active = claim(store)
    assert active and claim(store) is None
    b = publish(store,market_kind='totals',side='over',line=210.5); mark(store,b['id'])
    assert sorted(r['state'] for r in rows(store,'instruction_refill_requests')) == ['queued','running']
    assert claim(store) is None
    result = complete(store,active)
    assert result['state'] == 'completed'
    assert result['novel_qualified_instruction_ids'] == [b['id']]
    assert result['observed']['ids'] == []
    assert b['id'] in result['observed']['marked_qualified_ids']
    next_scan = claim(store)
    assert next_scan['request_id'] != active['request_id']
    with store.db() as c:
        assert refill.status(c)['request_id']==next_scan['request_id']
        exact=refill.status(c,active['request_id'])
    assert exact['request_id']==active['request_id'] and exact['state']=='completed'
    assert exact['attempt_number']==active['attempt_number'] and exact['started_at']==active['started_at']


def test_new_guarded_funded_contract_is_observed_not_invented_by_queue(store):
    old = publish(store); mark(store,old['id']); active = claim(store)
    assert len(store.system_decisions()) == 1
    new = publish(store,market_kind='totals',side='over',line=210.5)
    result = complete(store,active)
    assert result['state'] == 'completed'
    assert result['novel_qualified_instruction_ids'] == [new['id']]
    assert len(store.system_decisions()) == 2
    assert refill.shortage_observation(result) is None
    before = rows(store,'instruction_refill_events')
    assert complete(store,active) is None
    assert rows(store,'instruction_refill_events') == before


def test_repricing_and_unmarking_an_old_contract_cannot_count_as_refill(store):
    old = publish(store); mark(store,old['id']); active = claim(store)
    publish(store,start=old['start'],odds=2.1)
    mark(store,old['id'],False)
    result = complete(store,active)
    assert result['state'] == 'shortage' and result['novel_qualified_instruction_ids'] == []
    observation = refill.shortage_observation(result)
    assert observation['id'] == 'SYSTEM:instruction_refill_shortage'
    assert observation['bucket'] == 'delivery'
    assert observation['evidence']['request_id'] == active['request_id']


@pytest.mark.parametrize('case', ['no_publication', 'failed_refresh', 'expired_new'])
def test_failed_or_nonactionable_attempt_retains_explicit_outcome(store, case):
    old=publish(store);mark(store,old['id']);active=claim(store)
    kwargs={}
    if case=='failed_refresh': kwargs['error']='Provider unavailable'
    elif case=='expired_new':
        publish(store,market_kind='totals',side='over',line=210.5)
        kwargs['now']=utcnow()+timedelta(minutes=6)
    result=complete(store,active,**kwargs)
    assert result['state']==('shortage' if case=='expired_new' else 'failed')
    assert result['novel_qualified_instruction_ids']==[]
    assert claim(store) is None
    assert [r['event'] for r in rows(store,'instruction_refill_events')][-2:]==['attempt_started','attempt_finished']


def test_restart_preserves_interrupted_attempt_without_duplicate_retry_loop(store):
    tip=publish(store);mark(store,tip['id']);active=claim(store)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE'); recovered=refill.recover_interrupted(c)
    assert recovered[0]['request_id']==active['request_id']
    assert rows(store,'instruction_refill_requests')[0]['state']=='interrupted'
    assert claim(store) is None
    assert complete(store,active) is None


def test_mutation_requires_transaction_and_unknown_mark_rolls_back(store):
    with store.db() as c:
        with pytest.raises(ValueError,match='transaction'):refill.claim(c)
        c.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError,match='Pick not found'):refill.mark_placed(c,'unknown',True)
    assert rows(store,'instruction_refill_requests')==[]


def test_bad_current_publication_preserves_checklist_but_cannot_create_request(store):
    tip=publish(store)
    with store.db() as c:
        c.execute('UPDATE publications SET payload=?', ('{"evaluated_candidates":[{"untrusted":"malformed"}]}',))
    assert mark(store,tip['id'])['placed'] is True
    assert rows(store,'instruction_refill_requests')==[]


def test_failed_observation_retains_failed_attempt_instead_of_claiming_success(store):
    tip=publish(store);mark(store,tip['id']);active=claim(store)
    with store.db() as c:
        c.execute('UPDATE publications SET payload=?', ('{"evaluated_candidates":1}',))
    result=complete(store,active)
    assert result['state']=='failed' and result['novel_qualified_instruction_ids']==[]
    assert 'unavailable' in result['error']


def test_wrong_claim_identity_does_not_complete_another_attempt(store):
    tip=publish(store);mark(store,tip['id']);active=claim(store)
    wrong=deepcopy(active);wrong['started_at']=(utcnow()+timedelta(seconds=1)).isoformat()
    assert complete(store,wrong) is None
    assert rows(store,'instruction_refill_requests')[0]['state']=='running'


def test_new_payoff_aliases_are_not_reported_as_two_independent_refills(store, monkeypatch):
    import test_execution_instructions as fixtures
    original_case=fixtures.guard_case
    def with_margin():
        case=original_case();case[4].margin=10.;return case
    monkeypatch.setattr(fixtures,'guard_case',with_margin)
    initial=publish(store,market_kind='totals',side='over',line=210.5)
    mark(store,initial['id']);active=claim(store)
    winner=reviewed_tip(cap=1.)
    alias=reviewed_tip(cap=1.,market_kind='spreads',side='home',line=0.)
    store.publish({'generated_at':utcnow().isoformat(),'recommendations':[winner,alias],
        'evaluated_candidates':[winner,alias]},require_guard=True)
    result=complete(store,active)
    assert result['state']=='completed'
    assert len(result['novel_qualified_instruction_ids'])==1
    assert set(result['novel_qualified_instruction_ids']) <= {winner['id'],alias['id']}


@pytest.mark.parametrize('failure',['failed','interrupted'])
def test_same_request_retries_after_backoff_with_original_baseline_and_real_recovery(store,failure):
    tip=publish(store);mark(store,tip['id']);first=claim(store)
    if failure=='failed': report=complete(store,first,error='source unavailable')
    else:
        with store.db() as c:
            c.execute('BEGIN IMMEDIATE');report=refill.recover_interrupted(c)[0]
    assert refill.shortage_observation(report)['evidence']['attempt_state']==failure
    retry_at=parse_time(report['retry_at'])
    assert claim(store,retry_at-timedelta(microseconds=1)) is None
    second=claim(store,retry_at)
    assert second['request_id']==first['request_id'] and second['attempt_number']==2
    assert first['force_data'] is True and second['force_data'] is False
    for key in ('requested_at','baseline_publication_version','baseline_contract_ids','baseline_issuance_ids'):
        assert second[key]==first[key]
    new=publish(store,market_kind='totals',side='over',line=210.5)
    result=complete(store,second,now=retry_at+timedelta(seconds=1))
    assert result['state']=='completed' and result['novel_qualified_instruction_ids']==[new['id']]
    assert claim(store,retry_at+timedelta(hours=1)) is None
    events=rows(store,'instruction_refill_events')
    assert sum(row['event']=='attempt_started' for row in events)==2
    assert sum(row['event'] in ('attempt_finished','attempt_interrupted') for row in events)==2


def test_failure_backoff_grows_without_erasing_first_request_or_spawning_new_request(store):
    tip=publish(store);mark(store,tip['id']);first=claim(store)
    report=complete(store,first,error='outage');now=parse_time(report['retry_at'])
    second=claim(store,now);later=complete(store,second,error='same outage',now=now)
    assert parse_time(later['retry_at'])-now==timedelta(seconds=120)
    mark(store,tip['id'],False);mark(store,tip['id'])
    assert len(rows(store,'instruction_refill_requests'))==1
    assert claim(store,now+timedelta(seconds=119)) is None
