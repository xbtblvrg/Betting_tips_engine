from betting_engine.feed_health import market_feed_health

def test_network_outage_is_not_successful_market_coverage():
    result=market_feed_health({},[{'reason':'source_error','league':'NFL'}])
    assert result['state']=='unavailable' and result['message']

def test_partial_coverage_keeps_valid_quotes_and_reports_failure():
    result=market_feed_health({'a':object()},[{'reason':'orderbook_error'}])
    assert result['state']=='partial' and result['error_count']==1

def test_empty_schedule_and_unsupported_markets_are_not_network_outages():
    assert market_feed_health({},[])['state']=='healthy'
    assert market_feed_health({},[{'reason':'unsupported_market'}])['message'] is None

def test_recovery_clears_the_previous_failure():
    assert market_feed_health({},[{'reason':'source_error'}])['message']
    recovered=market_feed_health({'fresh':object()},[])
    assert recovered['state']=='healthy' and recovered['message'] is None

def test_stake_failures_surface_as_partial_or_unavailable():
    assert market_feed_health({},[{'reason':'stake_schedule_error'}])['state']=='unavailable'
    assert market_feed_health({'fresh':object()},[{'reason':'stake_fixture_error'}])['state']=='partial'
