"""Separate a completed polling cycle from healthy market-data coverage."""

def market_feed_health(quotes,issues):
    failures=[i for i in issues if i.get('reason') in ('source_error','orderbook_error','pagination_limit','stake_schedule_error','stake_fixture_error','stake_invalid_odds',
        'stake_sports_registry_error','stake_tournament_error','stake_fixture_schema','stake_fixture_identity_conflict',
        'stake_fixture_endpoint_error','stake_seed_identity_unresolved','stake_endpoint_market_difference')]
    if not failures:return {'state':'healthy','error_count':0,'message':None}
    state='partial' if quotes else 'unavailable'
    message='Az árforrás részben elérhető; a piaci lefedettség hiányos.' if quotes else 'Az árforrás nem elérhető; nincs friss, ellenőrzött piaci ajánlás.'
    return {'state':state,'error_count':len(failures),'message':message}
