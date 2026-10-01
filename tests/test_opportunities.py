from datetime import datetime, timedelta, timezone
from betting_engine.opportunities import active_standard_outcomes, inventory_snapshot, rolling_opportunities

NOW = datetime(2026, 9, 28, 22, 45, tzinfo=timezone.utc)


def test_active_offers_require_active_finite_decimal_prices():
    rows = [{'active': True, 'odds': p} for p in [2., 1., 0., float('nan'), float('inf'), '2', True]]
    rows += [{'active': False, 'odds': 2}, {'active': True, 'status': 'suspended', 'odds': 2}]
    assert active_standard_outcomes({'status': 'active', 'outcomes': rows}) == 1
    assert active_standard_outcomes({'status': 'suspended', 'outcomes': rows}) == 0


def fixture(id, hours, league='NFL'):
    return dict(fixture_id=id, league=league, start=(NOW+timedelta(hours=hours)).isoformat(),
                active=True, matched=True, odds_loaded=True)


def market(id, **kwargs):
    return dict(fixture_id=id, league='NFL', outcome_count=10, active_outcomes=4,
                supported_outcomes=2, reason='partially_supported', fetched_at=NOW.isoformat(), **kwargs)


def tip(id, hours, **kwargs):
    return dict(id=id, league='NFL', start=(NOW+timedelta(hours=hours)).isoformat(),
                valid_until=(NOW+timedelta(minutes=5)).isoformat(), stress_roi=.1, **kwargs)


def test_rolling_boundaries_and_ticket_duplicates_are_not_daily_issuance():
    fs=[fixture('started',0),fixture('near',24),fixture('later',24.01),fixture('far',48),fixture('beyond',49)]
    snapshot=inventory_snapshot(fs+[fs[1]], [market(f['fixture_id']) for f in fs], {'NFL':5,'NBA':0}, [])
    t=tip('t',24)
    payload={'generated_at':NOW.isoformat(),'opportunity_inventory':snapshot,'evaluated_candidates':[t,t],
             'recommendations':[t], 'alternatives':[]}
    bets=[{'tip_id':'t','payload':t}]*2
    result=rolling_opportunities(payload,bets,{},NOW,.03)
    a=result['windows']['24']['totals'];b=result['windows']['48']['totals']
    assert a['games']==1 and b['games']==3
    assert a['observed_outcomes']==10 and a['active_standard_outcomes']==4
    assert a['modeled_outcomes']==a['qualified_families']==a['recorded_selections']==a['unmarked_selections']==1
    assert rolling_opportunities(payload,bets,{'t':'marked'},NOW,.03)['windows']['24']['totals']['unmarked_selections']==0
    assert 'daily_issued' not in a
    sources={r['league']:r['source_state'] for r in result['windows']['24']['leagues']}
    assert sources['NBA']=='observed' and sources['MLB']=='unavailable'


def test_stale_prices_and_swish_are_not_current_standard_availability():
    old=market('near');old['fetched_at']=(NOW-timedelta(minutes=6)).isoformat()
    swish=market('near',provider_section='swishMarkets.playerProps',invalid_prices=3);swish['active_outcomes']=0;swish['supported_outcomes']=0
    snapshot=inventory_snapshot([fixture('near',2)],[old,swish],{'NFL':1},[{'league':'NFL','reason':'stake_fixture_error'}])
    t=tip('t',2);t['valid_until']=NOW.isoformat()
    p={'generated_at':NOW.isoformat(),'opportunity_inventory':snapshot,'evaluated_candidates':[t],'recommendations':[t]}
    w=rolling_opportunities(p,[{'tip_id':'t','payload':t}],{},NOW,.03)['windows']['24']
    assert w['totals']['observed_outcomes']==20
    assert w['totals']['active_standard_outcomes']==w['totals']['modeled_outcomes']==0
    assert w['totals']['swish_priced_unverified']==7
    assert w['totals']['recorded_selections']==1
    assert next(r for r in w['leagues'] if r['league']=='NFL')['source_state']=='partial'


def test_old_publications_do_not_invent_zero_coverage():
    assert rolling_opportunities({},[],{},NOW,.03)=={'state':'awaiting_publication','windows':{}}
