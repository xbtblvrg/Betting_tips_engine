from betting_engine.learning import catalog_operating_gaps


def test_catalog_gap_projection_keeps_unfinished_contracts_actionable():
    gaps = catalog_operating_gaps({'types': [
        {'league': 'MLB', 'section': 'standard', 'market': 'First Run',
         'scope': 'full_game', 'occurs_in_next_48h': True,
         'scope_model_status': 'not_established',
         'settlement_status': 'recognized_contract_only',
         'decision_chain_status': 'not_established',
         'required_data_source': 'Stake Sports Data API',
         'stake_event_paired': True},
        {'league': 'MLB', 'section': 'standard', 'market': 'Winner',
         'scope': 'full_game', 'scope_model_status': 'evaluated',
         'settlement_status': 'evaluated', 'decision_chain_status': 'evaluated'},
    ]})
    assert len(gaps) == 1
    row = gaps[0]
    assert row['id'] == 'CATALOG:MLB:standard:First Run'
    assert row['code'] == 'market_catalog_not_established'
    assert row['action'] == 'market_research'
    assert row['occurs_in_next_48h'] is True
    assert row['next_step']

