"""An unrelated current feed is not the missing frozen legacy regression input."""
from copy import deepcopy
import pytest
from betting_engine.runtime_errors import meaningful_source_changes


@pytest.mark.parametrize('requirement',[
    'Resolve two legacy tests requiring frozen NBA fixtures.',
    'Restore the frozen MLB fixtures required by retained tests.',
    'Supply frozen NHL fixtures for the regression tests.',
    'Root-bound frozen NBA fixtures for two unchanged failing tests.',
])
def test_explicit_unresolved_frozen_test_input_does_not_wake_on_new_odds(tmp_path,requirement):
    result={'remaining':[requirement],'input_generation':{'available':{'stake_sources':True}}}
    changed={'stake_sources':'unrelated-fresh-odds','validation_sources':'new-frozen-fixture'}
    before=deepcopy((result,changed))
    kept,notes=meaningful_source_changes(tmp_path,'failed-run',result,{},changed)
    assert kept=={'validation_sources':'new-frozen-fixture'}
    assert notes[0]['state']=='awaiting_root_pinned_regression_fixture'
    assert (result,changed)==before


@pytest.mark.parametrize('requirement',[
    'Legacy tests using frozen NBA fixtures passed; discover current offers.',
    'Resolve current offers and use the frozen NBA model for new tests.',
    'No frozen regression fixtures are required; discover the live market.',
    'Supply fresh current fixtures for NBA regression analysis.',
    'Root-bound frozen NBA fixtures are complete; discover current offers.',
])
def test_completed_fixture_or_genuine_current_source_work_retains_acceleration(tmp_path,requirement):
    result={'remaining':[requirement],'input_generation':{'available':{'stake_sources':True}}}
    assert meaningful_source_changes(tmp_path,'run',result,{},{'stake_sources':'new'})==({'stake_sources':'new'},[])
