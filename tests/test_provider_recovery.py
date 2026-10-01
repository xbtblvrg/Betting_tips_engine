"""Synthetic providers and temporary state only; no real resets or inference."""
import copy
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import time
import uuid

import pytest

_MODULE = Path(__file__).resolve().parents[1] / 'src/betting_engine/provider_recovery.py'
spec = importlib.util.spec_from_file_location('provider_recovery_candidate', _MODULE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
Router = module.ProviderRouter
PIN = 'a' * 64
ACCOUNT = 'b' * 64


def policy():
    return {'version': module.POLICY_VERSION, 'account_scope_sha256': ACCOUNT, 'capability_evidence_sha256': PIN,
            'verified_at': 0, 'expires_at': 100000, 'reset_supported': True, 'allow_temporary_claude': True,
            'routes': [{'id': 'preferred', 'provider': 'codex', 'family': 'gpt', 'model': 'synthetic-gpt-strong', 'capability_sha256': PIN, 'reasoning_effort': 'high'},
                       {'id': 'alternate', 'provider': 'codex', 'family': 'gpt', 'model': 'synthetic-gpt-other', 'capability_sha256': PIN},
                       {'id': 'temporary', 'provider': 'claude', 'family': 'opus-5.5', 'model': 'claude-opus-5-5', 'capability_sha256': PIN}]}


def failure(kind='weekly_quota', **extra):
    return {'kind': kind, 'evidence_sha256': PIN, 'source': 'codex_structured', 'code': 'usageLimitExceeded', 'window_duration_mins': 10080, **extra}


def limits(weekly=True, count=1, allowed=False):
    return {'account_scope_sha256': ACCOUNT, 'evidence_sha256': PIN, 'ordinaryUsageAllowed': allowed,
            'rateLimits': {'limitId': 'codex', 'rateLimitReachedType': 'rate_limit_reached' if weekly else None,
                           'secondary': {'windowDurationMins': 10080, 'usedPercent': 100 if weekly else 0, 'resetsAt': 2000}},
            'rateLimitResetCredits': {'availableCount': count, 'credits': None}}


class Transport:
    def __init__(self):
        self.calls = []; self.reads = [limits()]; self.resets = [{'outcome': 'reset', 'evidence_sha256': PIN}]
        self.probes = {}; self.default_probe = 'available'
    @staticmethod
    def response(items):
        value = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(value, BaseException): raise value
        return copy.deepcopy(value)
    def read_limits(self, **kwargs):
        self.calls.append(('read', kwargs)); return self.response(self.reads)
    def consume_reset(self, key, **kwargs):
        self.calls.append(('reset', key, kwargs)); return self.response(self.resets)
    def probe_model(self, model, **kwargs):
        self.calls.append(('probe', model, kwargs))
        values = self.probes.get(model)
        if values: return self.response(values)
        if self.default_probe == 'available':
            return {'status': 'available', 'reported_model': model, 'evidence_sha256': PIN}
        return {'status': self.default_probe, 'evidence_sha256': PIN, 'source': 'codex_structured', 'code': 'usageLimitExceeded'}


def router(tmp_path, transport=None, config=None):
    return Router(tmp_path / 'routing', config or policy(), transport or Transport(), clock=lambda: 1000, jitter=lambda: 0.5)


def load(router):
    return json.loads(router.path.read_text())


def resets(transport):
    return [c for c in transport.calls if c[0] == 'reset']


def test_catalog_alone_does_not_prove_availability_and_primary_probe_does(tmp_path):
    t = Transport(); r = router(tmp_path, t)
    assert r.get_selection() is None
    result = r.periodic_tick()
    assert result['selection']['id'] == 'preferred' and result['actions'] == 1
    assert r.get_selection()['model'] == 'synthetic-gpt-strong'
    assert [c[0] for c in t.calls] == ['probe']


def test_full_reset_order_read_claim_consume_refresh_exact_probe(tmp_path):
    t = Transport(); t.reads = [limits(), limits(False, 0, True)]
    r = router(tmp_path, t); r.report_failure('preferred', failure())
    result = r.periodic_tick(max_actions=4)
    assert [c[0] for c in t.calls] == ['read', 'reset', 'read', 'probe']
    claim = load(r)['resets'][0]
    assert str(uuid.UUID(claim['idempotency_key'])) == resets(t)[0][1]
    assert claim['status'] == 'reset' and not claim['needs_refresh']
    assert resets(t)[0][2]['expected_account_scope_sha256'] == ACCOUNT
    assert result['selection']['id'] == 'preferred'
    assert load(r)['quota_episode']['resolved_at'] == 1000


@pytest.mark.parametrize('outcome', ['reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'])
def test_every_documented_reset_result_refreshes_limits_without_inventing_success(tmp_path, outcome):
    t = Transport(); t.resets = [{'outcome': outcome, 'evidence_sha256': PIN}]; t.default_probe = 'unavailable'
    r = router(tmp_path, t); r.report_failure('preferred', failure())
    result = r.periodic_tick(max_actions=3)
    assert [c[0] for c in t.calls] == ['read', 'reset', 'read']
    assert result['selection'] is None
    assert load(r)['resets'][0]['status'] == outcome
    assert load(r)['quota_episode']['resolved_at'] is None


@pytest.mark.parametrize('change', ['short-window', 'no-reached-type', 'workspace-credits', 'ordinary-allowed', 'unknown-count', 'zero-count', 'bool-count', 'wrong-account', 'unsupported-reset', 'bare-percent'])
def test_reset_requires_fresh_weekly_quota_earned_count_and_account(tmp_path, change):
    t = Transport(); config = policy(); reply = limits()
    if change == 'short-window': reply['rateLimits']['secondary']['windowDurationMins'] = 300
    elif change == 'no-reached-type': reply['rateLimits']['rateLimitReachedType'] = None
    elif change == 'workspace-credits': reply['rateLimits']['rateLimitReachedType'] = 'workspace_owner_credits_depleted'
    elif change == 'ordinary-allowed': reply['ordinaryUsageAllowed'] = True
    elif change == 'unknown-count': reply['rateLimitResetCredits'] = None
    elif change == 'zero-count': reply['rateLimitResetCredits']['availableCount'] = 0
    elif change == 'bool-count': reply['rateLimitResetCredits']['availableCount'] = True
    elif change == 'wrong-account': reply['account_scope_sha256'] = 'c' * 64
    elif change == 'unsupported-reset': config['reset_supported'] = False
    elif change == 'bare-percent': reply['rateLimits']['secondary'].pop('windowDurationMins')
    t.reads = [reply]; r = router(tmp_path, t, config);r.report_failure('preferred', failure())
    r.periodic_tick()
    assert not resets(t)


@pytest.mark.parametrize('kind', ['timeout', 'auth_error', 'transport_error', 'unavailable', 'unknown'])
def test_nonquota_failure_never_resets_or_reads_quota_control(tmp_path, kind):
    t = Transport(); r = router(tmp_path, t)
    r.report_failure('preferred', failure(kind)); r.periodic_tick()
    assert not resets(t) and not any(c[0] == 'read' for c in t.calls)


def test_free_text_http429_and_timeout_are_not_weekly_quota():
    assert module.classify_failure(failure('weekly_quota', source='transport', text='weekly usage limit HTTP429')) == 'unknown'
    assert module.classify_failure(failure('weekly_quota', code='serverOverloaded')) == 'unknown'
    assert module.classify_failure(failure('weekly_quota', window_duration_mins=300)) == 'quota'
    with pytest.raises(ValueError): module.classify_failure({'message': 'weekly limit reached'})


def test_timeout_unknown_reset_reuses_uuid_after_restart_and_backoff(tmp_path):
    t = Transport(); t.resets = [TimeoutError('secret-auth'), {'outcome': 'alreadyRedeemed', 'evidence_sha256': PIN}]
    r = router(tmp_path, t);r.report_failure('preferred', failure());r.periodic_tick(max_actions=2)
    first = resets(t)[0][1]; assert load(r)['resets'][0]['status'] == 'uncertain'
    second = router(tmp_path, t);second.periodic_tick(now=1010)
    assert len(resets(t)) == 1
    second.periodic_tick(now=1040)
    assert len(resets(t)) == 2 and resets(t)[1][1] == first
    assert len(load(r)['resets']) == 1


def test_unknown_reset_has_finite_attempt_budget_and_can_use_fallback(tmp_path):
    t = Transport(); t.resets = [RuntimeError('private failure')]; config = policy(); config['max_reset_attempts'] = 2
    r = router(tmp_path, t, config);r.report_failure('preferred', failure());r.periodic_tick(max_actions=2)
    r.periodic_tick(now=1040);r.periodic_tick(now=2000)
    assert len(resets(t)) == 2 and len({x[1] for x in resets(t)}) == 1
    assert load(r)['resets'][0]['status'] == 'unknown_exhausted'
    assert r.get_selection(now=2000) is not None


def test_no_second_credit_claim_for_same_unresolved_episode(tmp_path):
    t = Transport();t.default_probe = 'quota';r = router(tmp_path,t);r.report_failure('preferred',failure())
    r.periodic_tick(max_actions=8)
    for now in (1400, 2200, 3200):
        r.report_failure('preferred', failure(evidence_sha256=hashlib.sha256(str(now).encode()).hexdigest()), now=now)
        r.periodic_tick(now=now,max_actions=10)
    assert len(resets(t)) == 1 and len(load(r)['resets']) == 1


def test_new_real_quota_after_preferred_success_can_claim_new_reset(tmp_path):
    t = Transport();r = router(tmp_path,t);r.report_failure('preferred',failure());r.periodic_tick()
    first = resets(t)[0][1]
    r.report_failure('preferred',failure(),now=1500);r.periodic_tick(now=1500)
    assert len(resets(t)) == 2 and resets(t)[1][1] != first


@pytest.mark.parametrize('credit_details', [None, [], [{'status': 'unknown', 'description': 'do not log secret'}]])
def test_available_count_is_authoritative_not_credit_detail_count(tmp_path, credit_details):
    t = Transport();t.reads[0]['rateLimitResetCredits']['credits'] = credit_details
    r = router(tmp_path,t);r.report_failure('preferred',failure());r.periodic_tick(max_actions=2)
    assert len(resets(t)) == 1 and resets(t)[0][2]['credit_id'] is None


def test_preferred_then_other_verified_gpt_then_temporary_opus(tmp_path):
    t = Transport();t.reads=[limits(True,0,False)]
    t.probes['synthetic-gpt-other'] = [{'status': 'quota', 'evidence_sha256': PIN, 'source': 'codex_structured', 'code': 'usageLimitExceeded'}]
    r = router(tmp_path,t);r.report_failure('preferred',failure());result=r.periodic_tick()
    assert result['selection']['id'] == 'temporary'
    assert [c[1] for c in t.calls if c[0]=='probe'] == ['synthetic-gpt-other','claude-opus-5-5']
    assert not resets(t)


def test_alt_success_is_available_even_when_account_ordinary_false(tmp_path):
    t = Transport();t.reads=[limits(True,0,False)]
    r = router(tmp_path,t);r.report_failure('preferred',failure());r.periodic_tick()
    assert r.get_selection()['id'] == 'alternate'
    assert load(r)['quota_episode']['resolved_at'] is None


def test_preferred_recovers_only_after_exact_probe_not_null_percentage(tmp_path):
    t=Transport();t.reads=[limits(False,0,None)]
    t.probes['synthetic-gpt-other']=[{'status':'unavailable','evidence_sha256':PIN}]
    r=router(tmp_path,t);r.report_failure('preferred',failure());r.periodic_tick()
    assert r.get_selection()['id']=='temporary'
    r.periodic_tick(now=1300)
    assert r.get_selection(now=1300)['id']=='preferred'
    assert load(r)['quota_episode']['resolved_at']==1300


def test_periodic_strongest_probe_and_work_success_avoid_minute_probe_spam(tmp_path):
    t=Transport();r=router(tmp_path,t);r.periodic_tick()
    for now in (1060,1120,1180):
        r.report_success('preferred',PIN,'synthetic-gpt-strong',now=now)
        r.periodic_tick(now=now)
    assert len(t.calls)==1
    r.report_failure('preferred',failure(),now=1200);t.reads=[limits(True,0,False)];r.periodic_tick(now=1200)
    assert r.get_selection(now=1200)['id']=='alternate'
    count=len(t.calls);r.periodic_tick(now=1490);assert len(t.calls)<=count+1 # quota read only
    r.periodic_tick(now=1500);assert r.get_selection(now=1500)['id']=='preferred'


@pytest.mark.parametrize('response', [{'status':'available','reported_model':'wrong','evidence_sha256':PIN}, {'status':'available','reported_model':'synthetic-gpt-strong'}, {'status':'available','reported_model':'synthetic-gpt-strong','evidence_sha256':'invalid'}, {'status':'anything','evidence_sha256':PIN}])
def test_probe_requires_exact_model_and_proof(tmp_path,response):
    t=Transport();t.probes['synthetic-gpt-strong']=[response];r=router(tmp_path,t);r.periodic_tick(max_actions=1)
    assert r.get_selection() is None


def test_report_success_exact_model_and_evidence_no_silent_alias(tmp_path):
    r=router(tmp_path)
    with pytest.raises(ValueError):r.report_success('preferred',PIN,'aliased-model')
    with pytest.raises(ValueError):r.report_success('preferred','bad','synthetic-gpt-strong')
    assert r.get_selection() is None


def test_expired_policy_auto_refresh_preserves_proof_and_attempt_history(tmp_path):
    t=Transport();config=policy();config['expires_at']=1200
    config['allow_temporary_claude']=False;config['routes']=config['routes'][:2]
    r=router(tmp_path,t,config);r.periodic_tick()
    assert r.get_selection(now=1200) is None
    assert r.periodic_tick(now=1200)['state']=='policy_refresh_required'
    new=copy.deepcopy(config);new.update(verified_at=1190,expires_at=3000,capability_evidence_sha256='c'*64)
    assert r.set_policy(new,now=1201)
    assert r.get_selection(now=1201)['id']=='preferred'
    assert len(t.calls)==1 and len(list((r.folder/'attempts').glob('*.json')))>=3


def test_model_changed_removed_or_reordered_never_lexically_guessed(tmp_path):
    t=Transport();r=router(tmp_path,t);r.report_success('preferred',PIN,'synthetic-gpt-strong')
    new=policy();new['routes'][0]['model']='future-unknown-verified-by-root';r.set_policy(new)
    assert r.get_selection() is None
    r.periodic_tick();assert r.get_selection()['model']=='future-unknown-verified-by-root'
    assert t.calls[0][1]=='future-unknown-verified-by-root'


def test_ttl_and_backward_clock_never_make_stale_proof_current(tmp_path):
    r=router(tmp_path);r.report_success('preferred',PIN,'synthetic-gpt-strong')
    assert r.get_selection(now=999) is None
    assert r.get_selection(now=4600) is None


@pytest.mark.parametrize('change', ['empty','duplicate','same-model','claude-first','other-provider','other-family','unapproved-claude','bad-pin','bad-model','negative-time','nan-time','reversed-time','unknown-field','oversized-backoff','low-ttl','too-many','bool-timeout'])
def test_invalid_policy_fail_closed(tmp_path,change):
    p=policy()
    if change=='empty':p['routes']=[]
    elif change=='duplicate':p['routes'][1]['id']='preferred'
    elif change=='same-model':p['routes'][1]['model']=p['routes'][0]['model']
    elif change=='claude-first':p['routes'].reverse()
    elif change=='other-provider':p['routes'][1]['provider']='unknown'
    elif change=='other-family':p['routes'][1]['family']='other'
    elif change=='unapproved-claude':p['allow_temporary_claude']=False
    elif change=='bad-pin':p['routes'][0]['capability_sha256']='wrong'
    elif change=='bad-model':p['routes'][0]['model']='unsafe\ncredential'
    elif change=='negative-time':p['verified_at']=-1
    elif change=='nan-time':p['verified_at']=float('nan')
    elif change=='reversed-time':p['verified_at']=200000
    elif change=='unknown-field':p['purchase_credits']=True
    elif change=='oversized-backoff':p['max_backoff_seconds']=999999
    elif change=='low-ttl':p['verification_ttl_seconds']=1
    elif change=='too-many':p['routes']*=10
    elif change=='bool-timeout':p['transport_timeout_seconds']=True
    with pytest.raises(ValueError):router(tmp_path,config=p)


@pytest.mark.parametrize('body', [b'{',b'{"revision":1,"revision":2}',b'{}',b'null',b'{"version":NaN}',b'X'*(2*1024*1024+1)])
def test_corrupt_state_preserved_without_control_calls(tmp_path,body):
    t=Transport();r=router(tmp_path,t);r.path.write_bytes(body)
    with pytest.raises((ValueError,TypeError)):r.periodic_tick()
    assert r.path.read_bytes()==body and not t.calls


def test_account_change_cannot_reuse_other_accounts_reset_state(tmp_path):
    r=router(tmp_path);r.report_failure('preferred',failure())
    config=policy();config['account_scope_sha256']='c'*64
    with pytest.raises(ValueError):r.set_policy(config)
    other=router(tmp_path,config=config)
    with pytest.raises(ValueError):other.periodic_tick()


@pytest.mark.parametrize('where',['folder','state','lock'])
def test_symlink_state_boundaries(tmp_path,where):
    target=tmp_path/'foreign';target.mkdir()
    if where=='folder':
        (tmp_path/'routing').symlink_to(target,target_is_directory=True)
        with pytest.raises(ValueError):router(tmp_path)
    else:
        r=router(tmp_path);(r.path if where=='state' else r.folder/'routing.lock').symlink_to(target/'data')
        with pytest.raises((ValueError,OSError)):r.periodic_tick()
    assert list(target.iterdir())==[]


def test_secret_provider_text_and_job_deadlines_never_enter_state(tmp_path):
    secret='SECRET-credential-and-full-prompt';t=Transport();t.resets=[RuntimeError(secret)]
    r=router(tmp_path,t);r.report_failure('preferred',failure(message=secret,task={'due_at':'original','attempts':9}));r.periodic_tick(max_actions=2)
    assert secret not in ''.join(p.read_text() for p in r.folder.rglob('*.json'))
    state=load(r);assert 'jobs' not in state and 'due_at' not in json.dumps(state)
    assert all(c[0] in ('probe','read','reset') for c in t.calls)


def test_transport_timeout_forwarded_and_tick_budget_bounded(tmp_path):
    t=Transport();t.default_probe='unavailable';r=router(tmp_path,t)
    result=r.periodic_tick(max_actions=2);assert result['actions']==2 and len(t.calls)==2
    assert all(c[-1]['timeout_seconds']==30 for c in t.calls)
    with pytest.raises(ValueError):r.periodic_tick(max_actions=21)


def _hold_lock(folder, entered, release):
    import fcntl
    with open(Path(folder)/'routing.lock','a') as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX);entered.set();release.wait(5)


def test_get_selection_nonblocking_while_other_process_holds_lock(tmp_path):
    r=router(tmp_path);r.report_success('preferred',PIN,'synthetic-gpt-strong')
    ctx=multiprocessing.get_context('fork');entered=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=_hold_lock,args=(str(r.folder),entered,release));child.start();assert entered.wait(2)
    try:
        started=time.monotonic();assert r.get_selection()['id']=='preferred';assert time.monotonic()-started<0.5
        assert r.periodic_tick()['state']=='busy'
        assert r.report_failure('preferred',failure()) is True
        assert r.get_selection() is None
        assert r.report_success('preferred',PIN,'synthetic-gpt-strong',now=1001) is True
        assert len(list((r.folder/'inbox').glob('*.json')))==2
    finally:release.set();child.join(3)
    assert child.exitcode==0


def _consume_child(folder, entered, leave, marker):
    class SlowTransport(Transport):
        def consume_reset(self,key,**kwargs):
            with open(marker,'a') as output:output.write(key+'\n')
            entered.set();leave.wait(5)
            return {'outcome':'reset','evidence_sha256':PIN}
    r=Router(folder,policy(),SlowTransport(),clock=lambda:1000,jitter=lambda:0.5)
    r.periodic_tick(max_actions=2)


def test_two_processes_cannot_spend_two_reset_claims(tmp_path):
    r=router(tmp_path);r.report_failure('preferred',failure())
    ctx=multiprocessing.get_context('fork');entered=ctx.Event();leave=ctx.Event();marker=tmp_path/'keys'
    child=ctx.Process(target=_consume_child,args=(str(r.folder),entered,leave,str(marker)));child.start();assert entered.wait(3)
    try:
        assert r.periodic_tick()['state']=='busy'
        assert len(marker.read_text().splitlines())==1
        state=load(r);assert state['resets'][0]['status']=='pending' # Durable before transport invocation.
    finally:leave.set();child.join(3)
    assert child.exitcode==0
    assert len(load(r)['resets'])==1


def _crash_child(folder,marker):
    class CrashTransport(Transport):
        def consume_reset(self,key,**kwargs):
            Path(marker).write_text(key);os._exit(37)
    Router(folder,policy(),CrashTransport(),clock=lambda:1000,jitter=lambda:0.5).periodic_tick(max_actions=2)


def test_process_crash_after_reset_dispatch_retries_same_durable_uuid(tmp_path):
    r=router(tmp_path);r.report_failure('preferred',failure());marker=tmp_path/'key'
    child=multiprocessing.get_context('fork').Process(target=_crash_child,args=(str(r.folder),str(marker)));child.start();child.join(3)
    assert child.exitcode==37
    assert load(r)['resets'][0]['status']=='pending'
    t=Transport();t.resets=[{'outcome':'alreadyRedeemed','evidence_sha256':PIN}]
    restarted=router(tmp_path,t);restarted.periodic_tick(now=1061,max_actions=2)
    assert resets(t)[0][1]==marker.read_text()
    assert len(load(r)['resets'])==1


@pytest.mark.parametrize('jitter',[0,0.5,1])
def test_bounded_exponential_jitter(tmp_path,jitter):
    r=Router(tmp_path/'routing',policy(),Transport(),clock=lambda:1000,jitter=lambda:jitter)
    delays=[r._backoff(n) for n in (1,2,5,10,1000)]
    assert delays==sorted(delays) and all(1<=d<=900 for d in delays)


def test_invalid_jitter_rejects_without_reset(tmp_path):
    t=Transport();r=Router(tmp_path/'routing',policy(),t,clock=lambda:1000,jitter=lambda:float('nan'))
    with pytest.raises(ValueError):r.report_failure('preferred',failure())
    assert not resets(t)


def test_concurrent_control_does_not_drop_quota_notification(tmp_path):
    r=router(tmp_path);r.report_success('preferred',PIN,'synthetic-gpt-strong')
    ctx=multiprocessing.get_context('fork');entered=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=_hold_lock,args=(str(r.folder),entered,release));child.start();assert entered.wait(2)
    try:
        assert r.report_failure('preferred',failure(),now=1010)
        assert r.get_selection(now=1010) is None
        assert len(list((r.folder/'inbox').glob('*.json')))==1
    finally:release.set();child.join(3)
    restarted=router(tmp_path);assert restarted.get_selection(now=1010) is None
    restarted.periodic_tick(now=1010,max_actions=1)
    assert load(restarted)['quota_episode']['opened_at']==1010
    assert not list((r.folder/'inbox').glob('*.json'))


def test_notification_ack_crash_is_idempotent(tmp_path,monkeypatch):
    r=router(tmp_path);route=r.policy['routes'][0]
    r._enqueue(route,'quota',PIN,1000)
    real=Path.unlink
    def fail(path,*args,**kwargs):
        if path.parent.name=='inbox':raise OSError('synthetic acknowledgment interruption')
        return real(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',fail)
        with pytest.raises(OSError):r.periodic_tick(max_actions=1)
    assert load(r)['routes']['preferred']['failures']==1
    with r._locked() as acquired:
        assert acquired;r._drain(r._load(),1000)
    assert load(r)['routes']['preferred']['failures']==1
    assert not list((r.folder/'inbox').glob('*.json'))


def test_pending_old_route_notification_does_not_poison_verified_new_model(tmp_path):
    r=router(tmp_path);r._enqueue(r.policy['routes'][0],'quota',PIN,1000)
    updated=policy();updated['routes'][0]['model']='new-root-verified-model';r.set_policy(updated)
    r.report_success('preferred',PIN,'new-root-verified-model',now=1001)
    assert r.get_selection(now=1001)['model']=='new-root-verified-model'
    assert load(r)['quota_episode'] is None


@pytest.mark.parametrize('response',[{}, {'outcome':'unknown','evidence_sha256':PIN}, {'outcome':'reset'}, {'outcome':'reset','evidence_sha256':'invalid'}])
def test_malformed_reset_never_becomes_success_or_new_uuid(tmp_path,response):
    t=Transport();t.resets=[response];r=router(tmp_path,t);r.report_failure('preferred',failure());r.periodic_tick(max_actions=2)
    assert load(r)['resets'][0]['status']=='uncertain'
    assert r.get_selection() is None
    first=resets(t)[0][1];r.periodic_tick(now=1040,max_actions=1)
    assert all(row[1]==first for row in resets(t))


def test_success_timestamp_and_backoff_use_completion_clock(tmp_path):
    clock=[1000]
    class Slow(Transport):
        def probe_model(self,model,**kwargs):
            clock[0]+=20
            return super().probe_model(model,**kwargs)
    r=Router(tmp_path/'routing',policy(),Slow(),clock=lambda:clock[0],jitter=lambda:0.5)
    r.periodic_tick(now=1000)
    assert load(r)['routes']['preferred']['verified_at']==1020
    assert r.get_selection(now=1020)['id']=='preferred'


def test_pending_failure_queued_after_probe_start_is_not_lost(tmp_path):
    clock=[1000]
    class Concurrent(Transport):
        def probe_model(self,model,**kwargs):
            router_two=Router(tmp_path/'routing',policy(),Transport(),clock=lambda:clock[0],jitter=lambda:0.5)
            clock[0]=1010;router_two.report_failure('preferred',failure())
            clock[0]=1005  # Probe's earlier response is observed late; conservative event ordering.
            return super().probe_model(model,**kwargs)
    r=Router(tmp_path/'routing',policy(),Concurrent(),clock=lambda:clock[0],jitter=lambda:0.5)
    r.periodic_tick(max_actions=1)
    assert r.get_selection(now=1010) is None
    assert load(r)['routes']['preferred']['status']=='weekly_quota'
    assert load(r)['quota_episode']['opened_at']==1010


def test_inbox_backlog_never_hides_a_failure_after_bounded_read(tmp_path):
    r=router(tmp_path);r.report_success('preferred',PIN,'synthetic-gpt-strong')
    for index in range(129):r._enqueue(r.policy['routes'][1],'timeout',PIN,1000+index)
    r._enqueue(r.policy['routes'][0],'quota',PIN,1200)
    assert r.get_selection(now=1200) is None
    assert r.status(now=1200)['pending_notifications']==130


def test_crashed_last_reset_attempt_becomes_unknown_exhausted_without_new_call(tmp_path):
    p=policy();p['max_reset_attempts']=1;r=router(tmp_path,config=p);r.report_failure('preferred',failure())
    state=load(r);state['resets']=[{'idempotency_key':str(uuid.uuid4()),'episode_id':state['quota_episode']['id'],
      'status':'pending','attempts':1,'next_retry_at':1000,'created_at':900,'evidence_sha256':None,'needs_refresh':False}]
    r.path.write_text(json.dumps(state))
    r.periodic_tick()
    assert load(r)['resets'][0]['status']=='unknown_exhausted' and not resets(r.transport)


def test_policy_expiry_during_control_read_cannot_authorize_reset(tmp_path):
    clock=[1000];config=policy();config['expires_at']=1001
    config['allow_temporary_claude']=False;config['routes']=config['routes'][:-1]
    class SlowRead(Transport):
        def read_limits(self,**kwargs):
            clock[0]=1002;return super().read_limits(**kwargs)
    transport=SlowRead();r=Router(tmp_path/'routing',config,transport,clock=lambda:clock[0],jitter=lambda:0.5)
    r.report_failure('preferred',failure());result=r.periodic_tick(max_actions=2)
    assert result=={'state':'policy_refresh_required','actions':1,'selection':None}
    assert [c[0] for c in transport.calls]==['read'] and load(r)['resets']==[]


def test_policy_expiry_during_probe_cannot_return_live_selection(tmp_path):
    clock=[1000];config=policy();config['expires_at']=1001
    class SlowProbe(Transport):
        def probe_model(self,model,**kwargs):
            clock[0]=1002;return super().probe_model(model,**kwargs)
    transport=SlowProbe();r=Router(tmp_path/'routing',config,transport,clock=lambda:clock[0],jitter=lambda:0.5)
    result=r.periodic_tick(max_actions=1)
    assert result['state']=='policy_refresh_required' and result['selection'] is None


def test_opus_family_label_cannot_authorize_a_different_claude_model(tmp_path):
    config=policy();config['routes'][-1]['model']='claude-sonnet'
    with pytest.raises(ValueError):router(tmp_path,config=config)


def test_stale_codex_catalog_keeps_only_exact_recent_claude_proof(tmp_path):
    config=policy();config['expires_at']=1100;t=Transport();r=router(tmp_path,t,config)
    r.report_success('preferred',PIN,'synthetic-gpt-strong')
    r.report_success('temporary',PIN,'claude-opus-5-5')
    assert r.get_selection(now=1101)['id']=='temporary'
    result=r.periodic_tick(now=1101)
    assert result['state']=='policy_refresh_required' and result['selection']['id']=='temporary'
    assert not t.calls and r.policy['verified_at']==0 and r.policy['expires_at']==1100


def test_stale_catalog_can_probe_only_existing_authorized_claude(tmp_path):
    config=policy();config['expires_at']=1100;t=Transport();r=router(tmp_path,t,config)
    r.report_failure('preferred',failure())
    result=r.periodic_tick(now=1101,max_actions=4)
    assert result['selection']['id']=='temporary' and result['state']=='policy_refresh_required'
    assert [c[0] for c in t.calls]==['probe'] and t.calls[0][1]=='claude-opus-5-5'
    assert not resets(t) and load(r)['resets']==[]


def test_stale_catalog_revocation_stops_claude_without_forging_freshness(tmp_path):
    config=policy();config['expires_at']=1100;r=router(tmp_path,config=config)
    r.report_success('temporary',PIN,'claude-opus-5-5')
    revoked=copy.deepcopy(config);revoked['allow_temporary_claude']=False;revoked['routes']=revoked['routes'][:-1]
    r.set_policy(revoked,now=1101)
    assert r.get_selection(now=1101) is None
    assert r.periodic_tick(now=1101)['actions']==0


def test_stale_catalog_claude_still_requires_fresh_exact_inference(tmp_path):
    config=policy();config['expires_at']=1100;t=Transport();t.default_probe='auth_error';r=router(tmp_path,t,config)
    r.report_success('temporary',PIN,'claude-opus-5-5')
    assert r.get_selection(now=4601) is None
    result=r.periodic_tick(now=4601)
    assert result['selection'] is None and all(c[1]=='claude-opus-5-5' for c in t.calls)


def test_future_policy_never_authorizes_stale_claude(tmp_path):
    config=policy();config['verified_at']=2000;r=router(tmp_path,config=config)
    assert r.periodic_tick(now=1000)['actions']==0 and not r.transport.calls


def test_refresh_after_stale_claude_can_return_to_preferred_gpt(tmp_path):
    config=policy();config['expires_at']=1100;t=Transport();r=router(tmp_path,t,config)
    assert r.periodic_tick(now=1101)['selection']['id']=='temporary'
    fresh=copy.deepcopy(config);fresh.update(verified_at=1102,expires_at=10000)
    r.set_policy(fresh,now=1102);result=r.periodic_tick(now=1102)
    assert result['selection']['id']=='preferred'
