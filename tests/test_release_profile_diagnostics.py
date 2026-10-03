"""Recovery reporting must agree with the actual root authorization gate."""
import pytest
from betting_engine import release_coordinator as release
from test_release_coordinator import project


@pytest.mark.parametrize('profile', [None, {}, {'files': []}, {'kind': 'unknown'}, 'invalid'])
def test_missing_or_invalid_profile_is_reported_and_still_blocked(tmp_path, profile):
    root, policy, run_id = project(tmp_path)
    if profile is None:
        policy['profiles'].pop('D01')
    else:
        policy['profiles']['D01'] = profile
    context = release.recovery_context(root, run_id, policy=policy)
    assert context['profile_missing'] is True
    assert context['files_outside_profile']
    with pytest.raises(release.ReleaseBlocked) as exc:
        release.profile_for(policy, 'D01', [row['path'] for row in context['changed_files']])
    assert exc.value.code == 'profile_required'


def test_valid_profile_is_present_without_autoapproving_changes(tmp_path):
    root, policy, run_id = project(tmp_path)
    assert release.recovery_context(root, run_id, policy=policy)['profile_missing'] is False
    policy['profiles']['D01']['files'] = []
    context = release.recovery_context(root, run_id, policy=policy)
    assert context['profile_missing'] is False
    assert context['files_outside_profile']
    with pytest.raises(release.ReleaseBlocked) as exc:
        release.profile_for(policy, 'D01', [row['path'] for row in context['changed_files']])
    assert exc.value.code == 'file_not_allowed'
