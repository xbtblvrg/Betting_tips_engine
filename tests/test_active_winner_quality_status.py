from copy import deepcopy

from betting_engine.professional_quality import assess_active_winner_quality, assess_professional_quality
from betting_engine.winner_reference import choose_reference
from test_winner_reference import inputs, forecast


def test_selected_reference_quality_does_not_relabel_champion_diagnostics():
    evidence,champion=inputs()
    before=deepcopy(champion)
    original=forecast(champion)
    selected=choose_reference(original,champion,evidence,'moneyline')
    active=assess_active_winner_quality({original.game_id:original},{'NFL':champion},{original.game_id:selected})
    row=active[original.game_id]
    assert row['selection']=='reference'
    assert row['model_version']==selected[0].model_version
    assert row['quality']['model_version']==row['model_version']
    assert row['quality']['blocks_publication'] is False
    assert row['quality']['winner_reference']['original_input_archive_recovered'] is False
    assert row['quality']['authorizes_publication'] is False
    assert champion==before
    assert original.model_version==champion['version']
    assert assess_professional_quality(champion,'moneyline')['blocks_publication'] is True


def test_missing_or_failed_reference_reports_actual_champion_choice():
    _,champion=inputs();original=forecast(champion)
    row=assess_active_winner_quality({original.game_id:original},{'NFL':champion},{})[original.game_id]
    assert row['selection']=='champion'
    assert row['model_version']==champion['version']
    assert row['quality']['blocks_publication'] is True
    assert 'winner_brier_worse_than_elo' in row['quality']['errors']


def test_active_status_has_no_fictional_fixture_when_forecast_absent():
    _,champion=inputs()
    assert assess_active_winner_quality({}, {'NFL':champion}, {})=={}
