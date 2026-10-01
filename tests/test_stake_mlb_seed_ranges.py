"""Replay complete observed responses; a seed label never supplies team facts."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from betting_engine.domain import Game, parse_time
from betting_engine.markets import StakeSportsbook
from betting_engine.stake_api import BASE, fixture_bundle, league_fixtures, market_records, seed_title


FIXTURES=Path(__file__).parent/'fixtures/stake_mlb_seed_ranges'
SCHEDULE=BASE+'/schedule/sport/baseball/usa/tournament/mlb'
LIST=BASE+'/sports/baseball/usa/mlb/fixtures'
IDS={'5b9884c3-b579-4b92-b3de-8b21c6837ad5','d6048277-2ab8-48cd-ae3a-af8b0eefb385'}


class RecordedFeed:
    def __init__(self):
        self.rows={};self.metadata={}
        manifest=json.loads((FIXTURES/'manifest.json').read_text())
        for source in manifest['sources']:
            raw=gzip.decompress((FIXTURES/source['file']).read_bytes())
            assert hashlib.sha256(raw).hexdigest()==source['sha256']
            assert len(raw)==source['bytes'] and source['http_status']==200
            self.rows[source['url']]=json.loads(raw)
            self.metadata[source['url']]=source

    def json(self,url,ttl):
        return deepcopy(self.rows[url]),deepcopy(self.metadata[url])


def targets(feed):
    rows,sources,issues,ok=league_fixtures(feed,'MLB')
    return [r for r in rows if r['id'] in IDS],sources,issues,ok


def schedule_row(feed,fixture_id):
    return next(r for day in feed.rows[SCHEDULE]['schedule'] for r in day['fixtures'] if r['id']==fixture_id)


@pytest.mark.parametrize('title',[
    'AL Seed 3 - AL Seed 6','Cleveland Guardians - AL 3/6',
    'Milwaukee Brewers - Nl 4/5','NL 4/5 – Milwaukee Brewers',
])
def test_recognizes_observed_slots_without_assigning_teams(title):
    assert seed_title(title)


@pytest.mark.parametrize('title',[
    'Cleveland Guardians - Chicago White Sox','Cleveland Guardians - AL 7/8',
    'Cleveland Guardians - AL 3/3','AL 3/6',' - AL 3/6',
    'Cleveland Guardians - AL 3/6 - San Diego Padres',
])
def test_malformed_and_fully_named_titles_are_not_seed_slots(title):
    assert not seed_title(title)


def test_two_observed_fixture_pairs_resolve_only_against_exact_active_winner_outcomes():
    feed=RecordedFeed();before=deepcopy(feed.rows)
    fixtures,sources,issues,ok=targets(feed)
    assert ok and len(fixtures)==2 and len(sources)==2 and not issues
    for fixture in fixtures:
        assert not fixture['identity_conflict']
        original=schedule_row(feed,fixture['id'])
        assert fixture['seed_name_resolution_required']['schedule_name']==original['name']
        payload,meta,audit,responses=fixture_bundle(feed,fixture)
        assert set(responses)=={'fixtures','odds'} and not audit['errors']
        assert audit['identity_verified'] is True
        assert audit['seed_name_resolution']['verified_from_active_winner'] is True
        assert meta['url']==BASE+'/odds/'+fixture['slug']
        pair=sorted(fixture['name'].split(' - '))
        winners=[r for _,r in market_records(payload) if r['name']=='Winner (Incl. Extra Innings)']
        assert winners and all(sorted(o['name'] for o in r['outcomes'])==pair for r in winners)
    assert feed.rows==before


@pytest.mark.parametrize('conflict',['named_team','named_pair','slug','start'])
def test_seed_range_does_not_excuse_real_identity_or_schedule_conflicts(conflict):
    feed=RecordedFeed();fid=next(iter(IDS));row=schedule_row(feed,fid)
    if conflict=='named_team':row['name']='New York Yankees - AL 3/6'
    elif conflict=='named_pair':row['name']='New York Yankees - Toronto Blue Jays'
    elif conflict=='slug':row['slug']='different-fixture'
    else:row['date']+=3600001
    fixtures,_,issues,_=targets(feed)
    assert next(r for r in fixtures if r['id']==fid)['identity_conflict'] is True
    assert any(i['reason']=='stake_fixture_identity_conflict' for i in issues)


@pytest.mark.parametrize('mutation',['wrong_team','inactive','suspended','missing','conflicting_pair'])
def test_source_placeholder_remains_unverified_without_unique_active_exact_winner_pair(mutation):
    feed=RecordedFeed();fixture=targets(feed)[0][0]
    for endpoint in ('fixtures','odds'):
        payload=feed.rows[BASE+'/'+endpoint+'/'+fixture['slug']]
        for group,raw in list(market_records(payload)):
            if raw['name']!='Winner (Incl. Extra Innings)':continue
            if mutation=='wrong_team':raw['outcomes'][0]['name']='New York Yankees'
            elif mutation=='inactive':raw['outcomes'][0]['active']=False
            elif mutation=='suspended':raw['status']='suspended'
            elif mutation=='missing':raw['name']='1st Inning Winner'
            else:
                wrong=deepcopy(raw);wrong['outcomes'][0]['name']='New York Yankees'
                group['markets'].append([wrong])
    _,_,audit,_=fixture_bundle(feed,fixture)
    assert audit['identity_verified'] is False
    assert any(e['reason']=='stake_seed_identity_unresolved' for e in audit['errors'])


def test_direct_bundle_cannot_override_a_real_named_team_in_resolution_metadata():
    feed=RecordedFeed();fixture=targets(feed)[0][0]
    fixture['seed_name_resolution_required']={'schedule_name':'New York Yankees - AL 3/6','fixture_name':fixture['name']}
    assert fixture_bundle(feed,fixture)[2]['identity_verified'] is False


def test_observed_recovery_preserves_independent_start_gate_and_current_horizon(monkeypatch):
    import betting_engine.markets as markets
    import betting_engine.stake_api as stake_api
    feed=RecordedFeed();games=[Game(**r) for r in json.loads((FIXTURES/'independent-games.json').read_text())['games']]
    registry={'MLB':[{'slug':'mlb','sport':'baseball','league_path':'baseball/usa/mlb'}]}
    monkeypatch.setattr(stake_api,'tournaments',lambda fetcher:(registry,{'MLB'},[],[]))
    # Both observed fixtures are outside the actual capture's 48-hour window.
    monkeypatch.setattr(markets,'utcnow',lambda:parse_time('2026-10-01T07:27:00Z'))
    sportsbook=StakeSportsbook(feed)
    available,events,issues=sportsbook.discover(games)
    assert not available and not events and not issues
    assert sportsbook.fixture_audit and not IDS.intersection(r['fixture_id'] for r in sportsbook.fixture_audit)
    assert sportsbook.inventory  # Unmatched current feed inventory is retained.
    # At a later replay clock, independently retained starts permit Guardians
    # but still block Brewers: its sports start is 7.5 hours after Stake's.
    monkeypatch.setattr(markets,'utcnow',lambda:parse_time('2026-10-02T07:27:00Z'))
    available,events,issues=sportsbook.discover(games)
    assert available and {m.game_id for m in available}=={'MLB:849829'}
    cleveland=next(r for r in sportsbook.fixture_audit if r['fixture_id']=='5b9884c3-b579-4b92-b3de-8b21c6837ad5')
    milwaukee=next(r for r in sportsbook.fixture_audit if r['fixture_id']=='d6048277-2ab8-48cd-ae3a-af8b0eefb385')
    assert cleveland['active'] and cleveland['matching_basis']=='active_winner_outcome_team_names'
    assert milwaukee['matched'] is False and milwaukee['game_id'] is None
    assert all(r['supported_outcomes']==0 for r in sportsbook.inventory if r['fixture_id']==milwaukee['fixture_id'])
    assert not [m for m in available if m.game_id=='MLB:849830']
    assert not any(i['reason']=='stake_fixture_identity_conflict' for i in issues)
    assert all(m.fetched_at.startswith('2026-10-01') for m in available)
    # This replay parses historical contracts; quote freshness/guard approval
    # is deliberately not claimed by moving the discovery clock.
    # A controlled 30-minute perturbation is close enough for the preliminary
    # join, but must still fail the unchanged exact-start publication gate.
    changed=deepcopy(games)
    changed[0].start='2026-10-03T17:30:00Z'
    blocked,_,issues=sportsbook.discover(changed)
    assert not blocked and any(i['reason']=='stake_start_mismatch' for i in issues)
