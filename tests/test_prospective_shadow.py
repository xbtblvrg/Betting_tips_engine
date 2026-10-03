from copy import deepcopy
from datetime import timedelta
import gzip
import hashlib
import json
import sqlite3

import pytest

from betting_engine.domain import utcnow
from betting_engine.decisions import candidate
from betting_engine.governance import EvidenceGuard, KnowledgeAgent, evidence_digest
from betting_engine.prospective_shadow import ShadowEvidenceStore, _capture_reference, _cluster_summary
from betting_engine.shadow_runtime import record_publication, legacy_funded_summary
from test_governance import guard_case


def archive(root, tip, *, odds=None, at=None, suspended=False, counterpart=True):
    ids=tip['reference_market_id'].split(':'); title=tip['stake_evidence']['market_name'];line=tip['line']
    outcomes=[{'id':ids[3], 'name':tip['stake_evidence']['outcome_name'], 'active':True, 'odds':odds or tip['reference_odds']}]
    if counterpart: outcomes.append({'id':'other', 'name':f'Under {line:g}', 'active':True, 'odds':2.})
    body=json.dumps({'fixture':{'id':ids[1], 'slug':'synthetic-event', 'startTime':__import__('datetime').datetime.fromisoformat(tip['start']).timestamp()*1000,
        'status':'active','enabled':True,'preMatchEnabled':True,'blacklisted':False},
        'groups':[{'id':'main','name':'main','markets':[[{'id':ids[2],'name':title,'status':'suspended' if suspended else 'active',
        'specifiers':f'total={line:g}','outcomes':outcomes}]]}]}).encode()
    sha=hashlib.sha256(body).hexdigest();relative=f'stake_api_archive/{sha}.json.gz';(root/'stake_api_archive').mkdir(exist_ok=True)
    (root/relative).write_bytes(gzip.compress(body))
    return {'url':'https://odds-data.stake.com/odds/synthetic-event','fetched_at':at or tip['quote_at'],'sha256':sha,'archive':relative}


def tip(root, *, game_id='g', line=210.5):
    _,g,m,q,f,r,s=guard_case();g.id=game_id;m.game_id=game_id;f.game_id=game_id
    m.id='stake:fixture:market:outcome';m.kind='totals';m.side='over';m.line=line;m.description='Total (Incl. Overtime)';m.selection=f'Over {line:g}'
    t=candidate(g,m,q,f,s);t['stake_evidence']={'fixture_slug':'synthetic-event','market_name':m.description,'outcome_name':m.selection,
        'decimal_odds':t['reference_odds'],'fetched_at':t['quote_at']}
    t['stake_evidence']['api_source']=archive(root,t)
    m.metadata['api_source']=t['stake_evidence']['api_source']
    assert EvidenceGuard(KnowledgeAgent()).check(t,g,m,q,f,r,s)['passed']
    return t


def setup(root):
    t=tip(root);at=(utcnow()+timedelta(seconds=1)).isoformat();j=ShadowEvidenceStore.create(root/'shadow.sqlite3')
    j.register_trial('t',t['model_version'],{'hypothesis':'predeclared test'},as_of=at)
    assert j.capture('t',[t],archive_root=root,as_of=at)['accepted']==1
    return j,t,at


def close(root,j,t,*,odds=1.8,suspended=False,counterpart=True):
    from datetime import datetime
    at=(datetime.fromisoformat(t['start'])-timedelta(seconds=60)).isoformat()
    src=archive(root,t,odds=odds,at=at,suspended=suspended,counterpart=counterpart)
    cap=_capture_reference(src,'fixture','NBA');j.record_captures([cap],archive_root=root,as_of=at)
    return src,at


def result(t, *, scope='full_game', observed=None):
    from datetime import datetime
    return {'game_id':t['game_id'],'league':t['league'],'scope':scope,'status':'final','complete':True,
        'home_score':110,'away_score':105,'source_url':'https://sports.test/final',
        'observed_at':observed or (datetime.fromisoformat(t['start'])+timedelta(hours=3)).isoformat()}


def test_complete_unfunded_vertical_slice_and_comparable_market_accuracy(tmp_path):
    j,t,at=setup(tmp_path);close(tmp_path,j,t)
    outcome=result(t);study=j.evaluate('t',settlements=[outcome],as_of=outcome['observed_at']);f=study['families'][0]
    assert t['stake']==0 and len(j.export_decisions('t',as_of=at))==1
    assert study['rows'][0]['close']['price_clv']==pytest.approx(2/1.8-1)
    assert study['rows'][0]['closing_market_probability']==pytest.approx((1/1.8)/(1/1.8+1/2))
    assert f['model_brier']['game_equal_weight_mean']==pytest.approx(.16)
    assert f['price_clv']['independent_games']==1 and f['price_clv']['ci95'] is None
    assert not f['funding_veto'] and not study['profit_established']
    assert j.report('t',as_of=outcome['observed_at'])==study


def test_repricing_never_rewrites_first_snapshot_or_inflates_count(tmp_path):
    j,t,at=setup(tmp_path);before=j.export_decisions('t',as_of=at)
    changed=deepcopy(t);changed['reference_odds']=10.;changed['probability']=.99
    assert j.capture('t',[changed,t],archive_root=tmp_path,as_of=at)['duplicates']==2
    assert j.export_decisions('t',as_of=at)==before
    with sqlite3.connect(j.path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM observations")


def test_invalid_guard_or_original_archive_price_is_never_captured(tmp_path):
    t=tip(tmp_path);j=ShadowEvidenceStore.create(tmp_path/'shadow.sqlite3');at=(utcnow()+timedelta(seconds=1)).isoformat()
    j.register_trial('t',t['model_version'],{},as_of=at)
    t['stake_evidence']['api_source']=archive(tmp_path,t,odds=9.)
    t['decision_guard']['candidate_sha256']=evidence_digest(t)
    r=j.capture('t',[t],archive_root=tmp_path,as_of=at)
    assert r['accepted']==0 and r['rejected'][0]['reason']=='original_archive_odds_mismatch'
    t['decision_guard']['passed']=False
    assert j.capture('t',[t],archive_root=tmp_path,as_of=at)['accepted']==0


def test_later_suspension_blocks_old_good_close(tmp_path):
    j,t,_=setup(tmp_path);close(tmp_path,j,t)
    from datetime import datetime
    at=(datetime.fromisoformat(t['start'])-timedelta(seconds=20)).isoformat()
    src=archive(tmp_path,t,at=at,suspended=True)
    j.record_captures([_capture_reference(src,'fixture','NBA')],archive_root=tmp_path,as_of=at)
    s=j.evaluate('t',settlements=[],as_of=t['start'])
    assert s['rows'][0]['close']['status']=='missing_closing_price'
    assert s['families'][0]['price_clv']['state']=='insufficient_evidence'
    assert s['funding_veto'] is False


def test_incomplete_market_can_measure_price_but_cannot_invent_fair_probability(tmp_path):
    j,t,_=setup(tmp_path);close(tmp_path,j,t,counterpart=False);o=result(t)
    s=j.evaluate('t',settlements=[o],as_of=o['observed_at'])
    assert s['rows'][0]['close']['status']=='comparable'
    assert s['rows'][0]['closing_market_probability'] is None
    assert s['families'][0]['model_minus_market_brier']['contracts']==0


def test_scope_and_future_result_do_not_leak_into_fixed_cutoff(tmp_path):
    j,t,_=setup(tmp_path);close(tmp_path,j,t);future=result(t)
    s=j.evaluate('t',settlements=[future,result(t,scope='half_1')],as_of=t['start'])
    assert s['rows'][0]['outcome']['state']=='insufficient_evidence'
    s2=j.evaluate('t',settlements=[result(t,scope='half_1')],as_of=future['observed_at'])
    assert s2['rows'][0]['outcome']['reason']=='exact_scope_result_missing'


def test_game_clusters_not_lines_drive_sample_size_and_intervals():
    s=_cluster_summary([('g',.1)]*100+[('other',-.2)])
    assert s['contracts']==101 and s['independent_games']==2
    assert s['game_equal_weight_mean']==pytest.approx(-.05) and s['ci95'] is None
    negative=_cluster_summary([(f'g{i}',-.05) for i in range(20)])
    assert negative['negative_mean_interval'] and negative['game_equal_weight_mean']<0


def test_capture_targets_and_runtime_include_unfunded_fixture(tmp_path):
    t=tip(tmp_path);at=(utcnow()+timedelta(seconds=1)).isoformat();source=t['stake_evidence']['api_source']
    payload={'generated_at':at,'evaluated_candidates':[t],'api_audit':{'fixtures':[{'fixture_id':'fixture','league':'NBA','responses':{'odds':source}}]}}
    result=record_publication(tmp_path,payload,as_of=at)
    assert result['accepted']==1 and result['source_captures']['added']==1
    assert not result['counts_as_daily_funded_bets']
    assert record_publication(tmp_path,payload,as_of=at)['duplicates']==1


def test_trial_and_existing_nonshadow_database_cannot_be_overwritten(tmp_path):
    j,t,at=setup(tmp_path)
    with pytest.raises(ValueError):j.register_trial('t','another',{},as_of=at)
    other=tmp_path/'ledger.sqlite3'
    with sqlite3.connect(other) as db:db.execute('CREATE TABLE bets(id INTEGER)')
    before=other.read_bytes()
    with pytest.raises(ValueError):ShadowEvidenceStore(other)
    with pytest.raises(FileExistsError):ShadowEvidenceStore.create(other)
    assert other.read_bytes()==before


def test_legacy_funded_cohort_separate_and_missing_clv_is_not_veto(tmp_path):
    _,t,at=setup(tmp_path);t['stake']=1.
    d={'family':'contract:'+t['id'],'issued_at':at,'payload':t,'status':'lost','pnl':-1.,'outcome_evidence':{'settled_at':result(t)['observed_at']}}
    r=legacy_funded_summary([d],{},as_of=result(t)['observed_at'])
    f=r['families'][0]
    assert f['recorded_pnl_after_stakes']==-1 and f['original_funded_decisions']==1
    assert f['price_clv']['state']=='insufficient_evidence' and not f['funding_veto']


def test_unchanged_pending_and_closed_evidence_does_not_grow_studies(tmp_path):
    from datetime import datetime
    j,t,at=setup(tmp_path)
    pending=j.evaluate('t',settlements=[],as_of=at)
    later=(datetime.fromisoformat(at)+timedelta(seconds=10)).isoformat()
    again=j.evaluate('t',settlements=[],as_of=later)
    assert pending==again and pending['rows']==[] and pending['pending_contracts']==1
    assert len(pending['pending_families'])==1
    close(tmp_path,j,t)
    closed=j.evaluate('t',settlements=[],as_of=t['start'])
    assert len(closed['rows'])==1 and closed['pending_contracts']==0
    outcome=result(t)
    before_result=j.evaluate('t',settlements=[],as_of=outcome['observed_at'])
    assert before_result==closed  # Do not relabel the frozen report with a new cutoff.
    measured=j.evaluate('t',settlements=[outcome],as_of=outcome['observed_at'])
    assert measured['study_id']!=closed['study_id']
    assert measured['families'][0]['has_sustained_evidence'] is False
    assert measured['families'][0]['first_start']==t['start']
    with sqlite3.connect(j.path) as db:
        assert db.execute('SELECT count(*) FROM evaluations').fetchone()[0]==3


def test_invalid_close_archive_can_recover_with_later_verified_bytes(tmp_path):
    from datetime import datetime
    j,t,_=setup(tmp_path)
    at=(datetime.fromisoformat(t['start'])-timedelta(seconds=60)).isoformat()
    src=archive(tmp_path,t,at=at,odds=1.8);path=tmp_path/src['archive'];body=path.read_bytes();path.unlink()
    capture=_capture_reference(src,'fixture','NBA')
    assert j.record_captures([capture],archive_root=tmp_path,as_of=at)['errors']
    path.write_bytes(body)
    recorded=(datetime.fromisoformat(at)+timedelta(seconds=5)).isoformat()
    assert not j.record_captures([capture],archive_root=tmp_path,as_of=recorded)['errors']
    study=j.evaluate('t',settlements=[],as_of=t['start'])
    assert study['rows'][0]['close']['status']=='comparable'
    with sqlite3.connect(j.path) as db:assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==2


def test_concurrent_initialization_and_duplicate_capture_are_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    t=tip(tmp_path);at=(utcnow()+timedelta(seconds=1)).isoformat()
    payload={'evaluated_candidates':[t]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:record_publication(tmp_path,payload,as_of=at),range(2)))
    assert sum(r['accepted'] for r in results)==1
    assert sum(r['duplicates'] for r in results)==1


def test_runtime_result_proof_never_claims_hash_reference_verified(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from betting_engine import shadow_runtime
    t=tip(tmp_path);g=SimpleNamespace(id=t['game_id'],league=t['league'],state='final')
    stamp=result(t)['observed_at']
    monkeypatch.setattr(shadow_runtime,'settlement_scores',lambda *_:(110,105,{'source':'https://sports.test/result','fetched_at':stamp,'source_sha256':'a'*64,'archive':'raw/example'}))
    rows=shadow_runtime._results([{'payload':t}],[g],stamp)
    assert rows[0]['source_sha256']=='a'*64 and rows[0]['source_archive']=='raw/example'
    assert rows[0]['raw_source_proof']=='hash_referenced_not_reverified'
    assert rows[0]['source_verified_by_shadow'] is False


def test_schema_initialization_failure_never_leaves_advertised_empty_store(tmp_path,monkeypatch):
    from betting_engine import prospective_shadow
    path=tmp_path/'new.sqlite3';original=prospective_shadow.sqlite3.connect
    def fail(*a,**k):raise sqlite3.OperationalError('injected initialization failure')
    monkeypatch.setattr(prospective_shadow.sqlite3,'connect',fail)
    with pytest.raises(sqlite3.OperationalError):ShadowEvidenceStore.create(path)
    assert not path.exists() and not list(tmp_path.glob('.shadow-create-*'))
    monkeypatch.setattr(prospective_shadow.sqlite3,'connect',original)
    assert ShadowEvidenceStore.create(path).path==path


def test_wal_reader_snapshot_does_not_block_new_capture(tmp_path):
    j,t,at=setup(tmp_path)
    with sqlite3.connect(j.path) as reader:
        assert reader.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
        reader.execute('BEGIN')
        reader.execute('SELECT count(*) FROM captures').fetchone()
        close(tmp_path,j,t)
        assert reader.execute('SELECT count(*) FROM captures').fetchone()[0]==0
    with sqlite3.connect(j.path) as check:assert check.execute('SELECT count(*) FROM captures').fetchone()[0]==1


def test_evaluator_code_change_invalidates_unchanged_evidence_cache(tmp_path,monkeypatch):
    from betting_engine import prospective_shadow
    j,t,at=setup(tmp_path)
    before=j.evaluate('t',settlements=[],as_of=at)
    monkeypatch.setattr(prospective_shadow,'EVALUATOR_ID',{**prospective_shadow.EVALUATOR_ID,'closing_version':'new-exact-parser-version'})
    after=j.evaluate('t',settlements=[],as_of=at)
    assert after['input_sha256']!=before['input_sha256'] and after['study_id']!=before['study_id']
    with sqlite3.connect(j.path) as db:assert db.execute('SELECT count(*) FROM evaluations').fetchone()[0]==2


def test_one_stale_source_does_not_discard_fresh_capture_in_same_batch(tmp_path):
    from datetime import datetime
    j,t,_=setup(tmp_path)
    at=(datetime.fromisoformat(t['start'])-timedelta(seconds=60)).isoformat()
    source=archive(tmp_path,t,at=at)
    stale=deepcopy(source);stale['fetched_at']=(datetime.fromisoformat(at)-timedelta(seconds=301)).isoformat()
    r=j.record_captures([_capture_reference(stale,'fixture','NBA'),_capture_reference(source,'fixture','NBA')],archive_root=tmp_path,as_of=at)
    assert r['added']==1 and r['errors'][0]['reason']=='stale_or_future_capture'


@pytest.mark.parametrize('scope,key', [('full_game','official_result'),('period_1','final_summary_source')])
def test_runtime_propagates_exact_nested_official_proof_without_reverification(tmp_path,monkeypatch,scope,key):
    from types import SimpleNamespace
    from betting_engine import shadow_runtime
    t=tip(tmp_path);t['model_scope']=scope;g=SimpleNamespace(id=t['game_id'],league='NHL',state='final')
    stamp=result(t)['observed_at'];source={'url':'https://api-web.nhle.com/v1/gamecenter/1/landing','fetched_at':stamp,'game_id':g.id,'sha256':'b'*64,'archive':'raw/nhl_final.json'}
    evidence={'source':source['url'],'fetched_at':stamp,key:source}
    if scope!='full_game':evidence['source_sha256']=source['sha256']
    monkeypatch.setattr(shadow_runtime,'settlement_scores',lambda *_:(3,2,evidence))
    row=shadow_runtime._results([{'payload':t}],[g],stamp)[0]
    assert row['source_sha256']==source['sha256'] and row['source_archive']==source['archive']
    assert row['raw_source_proof']=='hash_referenced_not_reverified' and row['source_verified_by_shadow'] is False
    assert row['source_evidence']==evidence
    row['source_evidence'][key]['archive']='changed'
    assert source['archive']=='raw/nhl_final.json'
    source['game_id']='another-game'
    conflict=shadow_runtime._results([{'payload':t}],[g],stamp)[0]
    assert conflict['source_sha256'] is None and conflict['source_archive'] is None
    assert conflict['raw_source_proof']=='conflicting_raw_proof'


def test_source_capture_verification_does_not_hold_shared_writer_lock(tmp_path,monkeypatch):
    from betting_engine import prospective_shadow
    journal=ShadowEvidenceStore.create(tmp_path/'shadow.sqlite3')
    at=utcnow().isoformat();body=b'verified';sha=hashlib.sha256(body).hexdigest()
    capture={'observed_at':at,'sha256':sha,'fixture_id':'fixture'}
    calls=[]
    def verify(source,root):
        # A concurrent learner can write while the slow archive is inspected.
        with sqlite3.connect(journal.path,timeout=0) as db:
            db.execute('BEGIN IMMEDIATE')
            calls.append(True)
        return body,[]
    monkeypatch.setattr(prospective_shadow,'_source',verify)
    assert journal.record_captures([capture],archive_root=tmp_path,as_of=at)['added']==1
    assert calls==[True]
    with sqlite3.connect(journal.path) as db:
        assert db.execute('SELECT body FROM source_blobs WHERE sha256=?',(sha,)).fetchone()[0]==body
        assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==1
    assert journal.record_captures([capture],archive_root=tmp_path,as_of=at)['added']==0


def test_source_capture_pair_rolls_back_together_on_write_failure(tmp_path,monkeypatch):
    from betting_engine import prospective_shadow
    journal=ShadowEvidenceStore.create(tmp_path/'shadow.sqlite3')
    at=utcnow().isoformat();body=b'verified';sha=hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(prospective_shadow,'_source',lambda *_:(body,[]))
    with sqlite3.connect(journal.path) as db:
        db.execute("CREATE TRIGGER reject_capture BEFORE INSERT ON captures BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        journal.record_captures([{'observed_at':at,'sha256':sha,'fixture_id':'f'}],archive_root=tmp_path,as_of=at)
    with sqlite3.connect(journal.path) as db:
        assert db.execute('SELECT count(*) FROM source_blobs').fetchone()[0]==0
