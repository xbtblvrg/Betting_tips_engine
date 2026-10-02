"""Guarded engine profiles use the trusted release path without passive bypasses."""

import pytest

from betting_engine import release_coordinator as release


FILES = [
    'src/betting_engine/contracts.py',
    'src/betting_engine/market_anchor.py',
    'tests/test_composite_market_coverage.py',
]


def test_guarded_profile_allows_exact_runtime_contracts():
    policy = {'profiles': {'ENGINE': {'kind': 'guarded_engine', 'files': FILES}}}
    profile = release.profile_for(policy, 'ENGINE', FILES)
    assert profile['kind'] == 'guarded_engine'


@pytest.mark.parametrize('name', [
    'data/engine.sqlite3', 'config/release_policy.json',
    'src/betting_engine/storage.py', 'src/betting_engine/release_coordinator.py',
    'tests/conftest.py',
])
def test_guarded_profile_cannot_cross_ledger_or_controller_boundary(name):
    policy = {'profiles': {'ENGINE': {'kind': 'guarded_engine', 'files': FILES + [name]}}}
    with pytest.raises(release.ReleaseBlocked, match='Guarded engine release'):
        release.profile_for(policy, 'ENGINE', FILES + [name])


def test_passive_profile_still_rejects_protected_decision_core():
    policy = {'profiles': {'ENGINE': {'kind': 'passive_utility', 'files': ['src/betting_engine/decisions.py']}}}
    with pytest.raises(release.ReleaseBlocked, match='ordinary utility'):
        release.profile_for(policy, 'ENGINE', ['src/betting_engine/decisions.py'])
