"""Live publication and settings must retain the reviewed exposure policy."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from betting_engine import server
from betting_engine.decisions import publication_limits
from betting_engine.domain import utcnow
from betting_engine.governance import verify_publication
from test_autotracking import store
from test_continuous_delivery import reviewed

LIMIT_KEYS=('publication_limits_version','max_new_funded_per_publication','max_per_event',
            'max_period_per_event','max_stake_per_event','max_daily_family_share')
V2={'publication_limits_version':'v2','max_new_funded_per_publication':15,
    'max_per_event':4,'max_period_per_event':2,'max_stake_per_event':10.,'max_daily_family_share':.35}


def test_live_publication_requires_exact_reviewed_limits_but_historical_standalone_replays():
    tip,settings,guard=reviewed('strict',probability=.72,odds=2.)
    assert guard['passed'] and tip['stake']>0
    active={**settings,**V2}
    with pytest.raises(ValueError,match='exposure limits changed'):
        verify_publication(tip,utcnow(),active)
    # This is precisely the raw limit receipt emitted by EvidenceGuard.check.
    tip['decision_guard']['publication_limits']={k:active.get(k) for k in LIMIT_KEYS}
    verify_publication(tip,utcnow(),active)
    verify_publication(tip,utcnow())
    for key,value in [('publication_limits_version','v1'),('publication_limits_version',None),
                      ('max_per_event',3),('max_period_per_event',1),
                      ('max_new_funded_per_publication',10),('max_stake_per_event',5.),
                      ('max_daily_family_share',.25)]:
        with pytest.raises(ValueError,match='exposure limits changed'):
            verify_publication(tip,utcnow(),{**active,key:value})
    forged=deepcopy(tip);forged['decision_guard']['publication_limits']['max_per_event']=3
    with pytest.raises(ValueError,match='exposure limits changed'):
        verify_publication(forged,utcnow(),active)


@pytest.mark.parametrize('unknown',['v4','bad','',False,1])
def test_unknown_versions_fail_closed_and_absent_legacy_remains(unknown):
    with pytest.raises(ValueError,match='Unknown publication limits'):
        publication_limits({'publication_limits_version':unknown})
    assert publication_limits({}) is None
    assert publication_limits({'publication_limits_version':None}) is None


@pytest.mark.parametrize('active,bad',[('v1',None),('v2',None),('v2','v1')])
def test_settings_cannot_clear_or_downgrade_active_exposure_policy(tmp_path,monkeypatch,active,bad):
    saved=store(tmp_path,**{**V2,'publication_limits_version':active})
    monkeypatch.setattr(server.engine,'store',saved)
    monkeypatch.setattr(server.engine,'refresh',lambda:True)
    client=TestClient(server.app)
    assert client.put('/api/settings',json={'publication_limits_version':bad}).status_code==422
    assert saved.settings()['publication_limits_version']==active
    assert client.put('/api/settings',json={'unit_stake':1}).status_code==200
    assert saved.settings()['publication_limits_version']==active
    assert client.put('/api/settings',json={'publication_limits_version':'v2'}).status_code==200
