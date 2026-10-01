from datetime import timedelta
import gzip,hashlib,json
from betting_engine.closing_capture import capture_issued_prices
from betting_engine.domain import utcnow
from betting_engine.storage import Store

def setup(tmp_path,*,suspended=False):
    store=Store(tmp_path/'engine.sqlite3');now=utcnow()
    tip={'id':'t','game_id':'g','league':'NHL','start':(now+timedelta(minutes=10)).isoformat(),
         'stake':1.,'reference_odds':2.,'stake_evidence':{'fixture_slug':'game'}}
    with store.db() as c:
        c.execute('INSERT INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',('t','t',now.isoformat(),json.dumps(tip)))
    body=json.dumps({'fixture':{'id':'f','slug':'game','status':'suspended' if suspended else 'active'},'groups':[]}).encode()
    sha=hashlib.sha256(body).hexdigest();archive=f'stake_api_archive/{sha}.json.gz'
    path=tmp_path/archive;path.parent.mkdir();path.write_bytes(gzip.compress(body))
    ref={'url':'https://odds-data.stake.com/odds/game','sha256':sha,'archive':archive,'fetched_at':now.isoformat()}
    payload={'api_audit':{'fixtures':[{'fixture_id':'f','league':'NHL','responses':{'odds':ref}}]}}
    return store,now,payload,path

def test_real_bytes_are_hash_checked_idempotent_and_suspensions_are_retained(tmp_path):
    store,now,payload,path=setup(tmp_path,suspended=True);before=store.system_decisions()
    first=capture_issued_prices(store,payload,now);second=capture_issued_prices(store,payload,now)
    assert first['new_captures']==1 and second['new_captures']==0 and second['stored_captures']==1
    assert store.system_decisions()==before
    assert not first['clv_evaluation_enabled']

def test_bad_archive_is_reported_not_accepted(tmp_path):
    store,now,payload,path=setup(tmp_path);path.write_bytes(gzip.compress(b'{}'))
    r=capture_issued_prices(store,payload,now)
    assert r['stored_captures']==0 and r['state']=='degraded'
    assert r['errors'][0]['reason']=='Archive hash mismatch'

def test_late_or_stale_observation_cannot_be_backdated(tmp_path):
    store,now,payload,path=setup(tmp_path)
    payload['api_audit']['fixtures'][0]['responses']['odds']['fetched_at']=(now+timedelta(minutes=1)).isoformat()
    assert capture_issued_prices(store,payload,now)['stored_captures']==0
