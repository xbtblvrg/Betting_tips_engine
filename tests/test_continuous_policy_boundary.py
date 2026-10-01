import pytest

from betting_engine.development import validate_changes
from betting_engine.server import Settings


@pytest.mark.parametrize('after', ['changed', 'SYMLINK', None])
def test_automatic_developer_cannot_change_funding_policy(after):
    path='src/betting_engine/funding_policy.py'
    with pytest.raises(ValueError,match='Protected file changed'):
        validate_changes({path:'original'},{path:after} if after is not None else {})


def test_recommendation_policy_is_explicit_and_partial_settings_preserve_it():
    assert Settings().recommendation_policy=='strict'
    assert 'recommendation_policy' not in Settings(unit_stake=2).model_dump(exclude_unset=True)
    assert Settings(recommendation_policy='continuous_best_available_v1').model_dump(exclude_unset=True)=={
        'recommendation_policy':'continuous_best_available_v1'}
    with pytest.raises(ValueError):Settings(recommendation_policy='always_win')
