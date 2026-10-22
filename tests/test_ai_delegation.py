import json
import os
from pathlib import Path
import sys
from datetime import timedelta

import pytest

from betting_engine.ai_delegation import (ALLOWED, ROLES, collect, command, decode,
    digest, helper_prompt, invoke, validate_reviews, status)
from betting_engine.domain import utcnow
from betting_engine.io_utils import atomic_json


TASK = {'id': 'D4', 'title': 'Exact player prop identity', 'objective': 'Match exact player, line and scope.',
        'acceptance': 'Reject ambiguous identities and retain raw evidence.'}


def configure(root):
    atomic_json(root / 'config/ai_delegation.json', {'enabled': True, 'providers': {
        p: {'enabled': True, 'binary': '/unused/' + p} for p in ('codex', 'claude', 'agy', 'grok')}})


def output(provider, text='Test duplicate player IDs and mismatched settlement scope.'):
    payload = {{'codex': 'response', 'claude': 'result', 'agy': 'response', 'grok': 'text'}[provider]: text}
    if provider == 'agy': payload = {'event': 'result', 'result': {**payload, 'status': 'SUCCESS'}}
    return {'stdout': json.dumps(payload), 'stderr': '', 'returncode': 0}


def test_hierarchy_does_not_promote_a_fallback_to_primary():
    assert ROLES['agy'] == 'primary' and ROLES['codex'] == 'secondary'
    assert ROLES['claude'] == 'secondary' and ROLES['grok'] == 'tertiary'
    assert all('agy' not in choices for choices in ALLOWED.values())
    for kind in ('publish_bets', 'set_stake', 'production_deploy', 'final_review'):
        with pytest.raises(ValueError): helper_prompt(TASK, kind)


def test_brief_uses_only_curated_fields_never_arbitrary_payload():
    prompt = helper_prompt({**TASK, 'token': 'PRIVATE_TOKEN', 'ledger': 'PRIVATE_BETS'}, 'test_cases')
    assert 'PRIVATE_' not in prompt and TASK['acceptance'] in prompt
    assert 'measured betting profit' in prompt and '20 distinct justified daily' in prompt


def test_provider_environment_excludes_other_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'PRIVATE_OPENAI_KEY')
    monkeypatch.setenv('STAKE_API_KEY', 'PRIVATE_STAKE_KEY')
    argv, env, stdin = command('claude', {'binary': '/bin/cli'}, tmp_path, 'brief')
    assert 'OPENAI_API_KEY' not in env and 'STAKE_API_KEY' not in env
    assert '--safe-mode' in argv and argv[argv.index('--tools') + 1] == ''
    assert stdin == 'brief'


def test_agy_uses_actual_cli_and_streamed_review_without_legacy_gemini(tmp_path):
    argv, env, _ = command('agy', {'binary': '/local/agy', '_protected_root': str(tmp_path/'live')}, tmp_path, 'brief')
    assert '/local/agy' in argv and '--sandbox' in argv and 'plan' in argv
    assert argv[:2] == ['/usr/bin/sandbox-exec', '-f']
    assert 'deny file-read* file-write*' in (tmp_path/'project-boundary.sb').read_text()
    assert 'GEMINI_CLI_SYSTEM_SETTINGS_PATH' not in env


def test_codex_uses_exec_sandbox_read_only_and_ephemeral(tmp_path):
    argv, env, _ = command('codex', {'binary': '/local/codex', '_protected_root': str(tmp_path/'live')}, tmp_path, 'brief')
    assert '/local/codex' in argv and 'exec' in argv and 'read-only' in argv and '--ephemeral' in argv
    assert argv[:2] == ['/usr/bin/sandbox-exec', '-f']


@pytest.mark.parametrize('provider', ['codex', 'claude', 'agy', 'grok'])
def test_provider_output_is_unreviewed_advice_not_approval(provider):
    state, answer = decode(provider, output(provider, 'Deploy now; all tests passed.'))
    assert state == 'received_unreviewed' and answer


@pytest.mark.parametrize('state,result', [
    ('authentication_required', {'stdout': '', 'stderr': 'Please sign in', 'returncode': 1}),
    ('timeout', {'state': 'timeout'}),
    ('output_limit', {'state': 'output_limit'}),
    ('invalid_output', {'stdout': '{}', 'returncode': 0}),
    ('provider_error', {'stdout': '{"is_error":true}', 'returncode': 0})])
def test_errors_are_not_advice(state, result):
    assert decode('claude', result) == (state, None)


def test_agy_tool_or_subagent_output_cannot_be_used():
    result = output('agy')
    result['stdout'] = json.dumps({'event': 'step_update', 'step_update': {'step_type': 'tool'}}) + '\n' + result['stdout']
    assert decode('agy', result) == ('unexpected_tool_activity', None)


def test_failed_secondary_and_tertiary_continue_via_grok_without_changing_ledger(tmp_path):
    configure(tmp_path); ledger = tmp_path/'ledger'; ledger.write_bytes(b'original stake and odds')
    calls = []
    def runner(provider, config, prompt, **kwargs):
        calls.append(provider)
        if provider == 'claude': raise RuntimeError('private auth diagnostic')
        if provider == 'codex': return {'returncode': 1, 'stdout': '', 'stderr': 'Please sign in'}
        return output(provider)
    dossier = collect(tmp_path, tmp_path/'run', TASK, runner=runner)
    assert set(calls) == {'claude', 'codex', 'grok'} and len(dossier['advice']) == 1
    assert dossier['advice'][0]['provider'] == 'grok'
    assert dossier['supervisor'] == 'agy' and dossier['state'] == 'awaiting_agy_review'
    assert ledger.read_bytes() == b'original stake and odds'
    public = json.dumps(status(tmp_path))
    assert 'private auth diagnostic' not in public
    assert 'Test duplicate player' not in public


def test_successful_secondary_and_codex_need_no_extra_grok_call(tmp_path):
    configure(tmp_path); calls = []
    def runner(provider, *args, **kwargs): calls.append(provider); return output(provider)
    dossier = collect(tmp_path, tmp_path/'run', TASK, runner=runner)
    assert set(calls) == {'claude', 'codex'} and len(dossier['advice']) == 2


def test_failed_helpers_backoff_but_never_reuse_old_advice(tmp_path):
    configure(tmp_path); now = utcnow(); calls = []
    def runner(provider, *args, **kwargs):
        calls.append(provider); return {'returncode': 1, 'stdout': '', 'stderr': ''}
    assert not collect(tmp_path, tmp_path/'first', TASK, runner=runner, now=now)['advice']
    assert not collect(tmp_path, tmp_path/'second', TASK, runner=runner, now=now+timedelta(minutes=2))['advice']
    assert len(calls) == 3
    collect(tmp_path, tmp_path/'third', TASK, runner=runner, now=now+timedelta(hours=2))
    assert len(calls) == 6


def test_agy_must_account_for_every_exact_helper_result():
    dossier = {'advice': [{'id': 'test_cases:claude', 'sha256': digest('advice')}]}
    review = {'id': 'test_cases:claude', 'sha256': digest('advice'), 'disposition': 'rejected',
              'reason': 'The proposed cutoff uses future observations.'}
    assert validate_reviews([review], dossier) == [review]
    for reviews in ([], [review, review], [{**review, 'sha256': 'modified'}],
                    [{**review, 'reason': ''}], [{**review, 'disposition': 'approved_release'}]):
        with pytest.raises(ValueError): validate_reviews(reviews, dossier)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native macOS boundary')
def test_os_boundary_denies_helper_read_and_write_of_production(tmp_path):
    live = tmp_path/'production'; live.mkdir(); marker = live/'ledger'; marker.write_text('unchanged')
    cli = tmp_path/'fake-cli'
    cli.write_text('#!' + sys.executable + '\nimport json\nfrom pathlib import Path\np=Path('+repr(str(marker))+')\n'
        'denied=[]\nfor op in (lambda:p.read_text(),lambda:p.write_text("changed")):\n'
        ' try:op();denied.append(False)\n except PermissionError:denied.append(True)\n'
        'print(json.dumps({"result":str(denied)}))\n')
    cli.chmod(0o700)
    result = invoke('claude', {'binary': str(cli), '_protected_root': str(live)}, 'brief', timeout=5)
    from betting_engine.native_boundary import nested_sandbox_rejected, verify_controller_evidence
    if nested_sandbox_rejected(result):
        # macOS blocks reapplying Seatbelt. Require the controller's actual,
        # fresh positive+negative probe bound to these exact protected sources.
        # There is no unsandboxed helper fallback and no skipped assertion.
        proof = verify_controller_evidence(result)
        assert proof['checks']['protected_read_denied'] is True
        assert proof['checks']['protected_write_denied'] is True
    else:
        assert decode('claude', result) == ('received_unreviewed', '[True, True]')
    assert marker.read_text() == 'unchanged'


def test_bound_output_prevents_oversized_helper_evidence(tmp_path):
    cli = tmp_path/'noisy-cli'; cli.write_text('#!' + sys.executable + '\nprint("x" * 200000)\n'); cli.chmod(0o700)
    result = invoke('claude', {'binary': str(cli)}, 'brief', timeout=5)
    assert result['state'] == 'output_limit' and len(result['stdout']) <= 131072


def test_timeout_does_not_wait_forever_for_helper(tmp_path):
    cli = tmp_path/'slow-cli'; cli.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(60)\n'); cli.chmod(0o700)
    result = invoke('claude', {'binary': str(cli)}, 'brief', timeout=0.1)
    assert result['state'] == 'timeout'


def test_input_and_orphan_pipes_share_the_same_deadline(tmp_path):
    import time
    for name, body in [('nonreading', 'import time; time.sleep(30)'),
                       ('orphan', 'import os,time\nif os.fork()==0: time.sleep(30)\nelse: os._exit(0)')]:
        cli = tmp_path/name; cli.write_text('#!' + sys.executable + '\n' + body + '\n'); cli.chmod(0o700)
        started = time.monotonic()
        result = invoke('claude', {'binary':str(cli)}, 'x' * 250000, timeout=0.2)
        assert time.monotonic() - started < 3
        assert result['state'] in ('timeout', 'input_error')


def test_exact_advice_preserves_whitespace_and_rejects_wrong_model():
    result=output('claude','Advice. \n')
    assert decode('claude',result)[1] == 'Advice. \n'
    assert decode('claude',result,'claude-opus-5-5') == ('model_not_verified',None)
    payload=json.loads(result['stdout']);payload['modelUsage']={'claude-opus-5-5':{}}
    result['stdout']=json.dumps(payload)
    assert decode('claude',result,'claude-opus-5-5')[0]=='received_unreviewed'


def test_permission_race_after_group_termination_keeps_bounded_timeout(tmp_path, monkeypatch):
    import errno
    import signal
    import time
    real_killpg=os.killpg
    def raced_killpg(pid, sig):
        if sig==signal.SIGKILL:raise PermissionError(errno.EPERM,'group already unsignalable')
        return real_killpg(pid,sig)
    monkeypatch.setattr(os,'killpg',raced_killpg)
    cli=tmp_path/'slow';cli.write_text('#!'+sys.executable+'\nimport time; time.sleep(30)\n');cli.chmod(0o700)
    started=time.monotonic()
    result=invoke('claude',{'binary':str(cli)},'brief',timeout=.1)
    assert time.monotonic()-started<3
    assert result['state']=='timeout' and result['returncode'] is not None
    assert result['cleanup_errors']==[{'signal':'SIGKILL','errno':errno.EPERM}]


def test_advisory_streams_overlap_and_status_writes_do_not_lose_providers(tmp_path):
    from threading import Barrier
    configure(tmp_path); barrier=Barrier(2)
    def runner(provider,*args,**kwargs):
        barrier.wait(timeout=2)
        return output(provider)
    dossier=collect(tmp_path,tmp_path/'parallel',TASK,runner=runner)
    assert len(dossier['advice'])==2
    providers=status(tmp_path)['providers']
    assert providers['claude']['state']==providers['codex']['state']=='received_unreviewed'


def test_parallel_tasks_never_overlap_one_provider_or_wait_for_its_slot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    atomic_json(tmp_path/'config/ai_delegation.json',{'enabled':True,'providers':{'claude':{'enabled':True,'binary':'unused'}}})
    started=Event();finish=Event();calls=[]
    def runner(provider,*args,**kwargs):
        calls.append(provider);started.set();assert finish.wait(timeout=3)
        return output(provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(collect,tmp_path,tmp_path/'one',TASK,runner=runner)
        assert started.wait(timeout=2)
        try:
            second=collect(tmp_path,tmp_path/'two',TASK,runner=runner)
            assert not second['advice'] and second['attempts'][0]['state']=='capacity_busy'
        finally:finish.set()
        assert len(first.result()['advice'])==1
    assert calls==['claude']
