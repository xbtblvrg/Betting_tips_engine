import gzip
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json
from betting_engine.player_runtime import audit_prop_catalog,prepare_player_sources
from test_learning import worker


def catalogue(data):
    fixtures=Path(__file__).parent/'fixtures/player_props'
    sources=json.loads((fixtures/'manifest.json').read_text())['fixtures']
    source=next(r for r in sources if r['name']=='wnba-standard')
    body=(fixtures/source['body_file']).read_bytes();target=data/source['archive']
    target.parent.mkdir(parents=True);target.write_bytes(body)
    fixture={'league':source['league'],'fixture_id':source['fixture_id'],'identity_verified':True,
             'responses':{'odds':source,'fixtures':{**source,'url':source['url'].replace('/odds/','/fixtures/')}},'errors':[]}
    endpoint='market_catalog/versions/probe/endpoints.json'
    atomic_json(data/endpoint,{'fixtures':[fixture]})
    atomic_json(data/'market_catalog/latest.json',{'checked_at':source['fetched_at'],'endpoint_archive':endpoint,'issues':[]})
    return fixture,endpoint


def test_both_full_bodies_are_interpreted_without_multiplying_distinct_contracts(tmp_path):
    catalogue(tmp_path);r=audit_prop_catalog(tmp_path)
    assert r['state']=='audited' and not r['production_props_enabled']
    counts=r['leagues']['WNBA'];assert counts['source_responses']==2
    result=json.loads((tmp_path/r['contracts_archive']).read_text())
    assert len(result['responses'])==2
    assert counts['unique_observed_contracts']==len(result['responses'][0]['contracts'])


@pytest.mark.parametrize('failure',['missing','corrupt','endpoint_issue','parser_issue'])
def test_partial_feed_never_looks_fully_audited(tmp_path,failure):
    fixture,endpoint=catalogue(tmp_path)
    if failure=='missing':fixture['responses']={}
    elif failure=='endpoint_issue':fixture['errors']=[{'reason':'timeout'}]
    elif failure=='corrupt':
        (tmp_path/fixture['responses']['odds']['archive']).write_bytes(gzip.compress(b'{}'))
    else:
        meta=fixture['responses']['odds'];body=json.loads(gzip.decompress((tmp_path/meta['archive']).read_bytes()))
        # Malformed player market shape must be retained as a parser issue.
        body['swishMarkets']={'playerProps':[{'id':'broken','marketName':'points','outcomes':'invalid'}]}
        raw=json.dumps(body).encode();sha=hashlib.sha256(raw).hexdigest()
        path='stake_api_archive/'+sha+'.json.gz';(tmp_path/path).write_bytes(gzip.compress(raw))
        for v in fixture['responses'].values():v.update(archive=path,sha256=sha)
    atomic_json(tmp_path/endpoint,{'fixtures':[fixture]})
    r=audit_prop_catalog(tmp_path)
    assert r['state']=='partial' and r['errors']


def test_obsolete_player_jobs_are_coalesced_without_bypassing_failure_backoff(tmp_path):
    w=worker(tmp_path);catalogue(w.data);w.plan_player_evidence();w.plan_player_evidence()
    jobs=lambda:[j for j in w.snapshot()['jobs'] if j['kind']=='player_prop_audit']
    assert len(jobs())==1
    old=jobs()[0];later=(utcnow()+timedelta(hours=2)).isoformat()
    with w.store.db() as c:c.execute("UPDATE learning_jobs SET status='retry',next_attempt=?,last_error='source failure' WHERE id=?",(later,old['id']))
    path=w.data/'market_catalog/latest.json';value=json.loads(path.read_text());value['checked_at']=utcnow().isoformat();atomic_json(path,value)
    w.plan_player_evidence();rows=jobs()
    assert next(j for j in rows if j['id']==old['id'])['status']=='superseded'
    new=next(j for j in rows if j['id']!=old['id']);assert new['next_attempt']==later
    with pytest.raises(RuntimeError,match='generation changed'):w.execute(old)
    r=w.execute(new);assert r['catalogue_sha256']==new['payload']['source_generation_sha256']


def test_missing_history_is_visible_and_does_not_block_unrelated_development(tmp_path):
    result=prepare_player_sources(tmp_path/'project',tmp_path/'workspace')
    assert result['state']=='not_supplied' and result['production_props_enabled'] is False


def test_restored_exact_source_reactivates_its_superseded_job(tmp_path):
    w=worker(tmp_path);catalogue(w.data);path=w.data/'market_catalog/latest.json';first=path.read_bytes()
    w.plan_player_evidence();original=next(j for j in w.snapshot()['jobs'] if j['kind']=='player_prop_audit')
    value=json.loads(first);value['checked_at']=utcnow().isoformat();atomic_json(path,value)
    w.plan_player_evidence();path.write_bytes(first);w.plan_player_evidence()
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='player_prop_audit']
    assert [(j['id'],j['status']) for j in jobs if j['status']!='superseded']==[(original['id'],'queued')]
    assert all(j['attempts']==0 for j in jobs)
