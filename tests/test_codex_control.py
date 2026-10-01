"""Protocol/process faults only. No real inference or reset is made in tests."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

import pytest
from betting_engine import codex_control as c

SERVER = r'''
import json,os,sys,time
from pathlib import Path
MODE = __MODE__
TRACE = __TRACE__
def send(x):
 print(json.dumps(x),flush=True)
for line in sys.stdin:
 r=json.loads(line)
 with open(TRACE,'a') as f:f.write(json.dumps(r)+'\n')
 method=r['method']; ident=r.get('id')
 if method=='initialized':continue
 if MODE=='timeout':time.sleep(20)
 if MODE=='stderr_flood':sys.stderr.write('x'*3000000);sys.stderr.flush();time.sleep(20)
 if MODE=='stdout_flood':sys.stdout.write('x'*1100000);sys.stdout.flush();time.sleep(20)
 if MODE=='broken_json':print('{bad}',flush=True);continue
 if MODE=='duplicate_json':print('{"id":1,"id":1,"result":{}}',flush=True);continue
 if MODE=='nonfinite':print('{"id":1,"result":{"x":NaN}}',flush=True);continue
 if MODE=='wrong_id':send({'id':999,'result':{}});continue
 if MODE=='server_request':send({'id':'approval','method':'item/commandExecution/requestApproval','params':{}});continue
 if MODE=='early_exit':sys.exit(3)
 if MODE=='truncated':sys.stdout.write('{"id":');sys.stdout.flush();sys.exit(3)
 if MODE=='child_pipe':
  pid=os.fork()
  if pid==0:time.sleep(20);os._exit(0)
  Path(TRACE+'.pid').write_text(str(pid));sys.exit(0)
 if MODE=='rpc_error':send({'id':ident,'error':{'code':-32601,'message':'SECRET_VALUE must not be recorded'}});continue
 if method=='initialize':result={'userAgent':'quant_provider_control/0.157.1 (mock)','codexHome':'SECRET_HOME'}
 elif method=='account/rateLimits/read':
  result={'accountId':'account-A','ordinaryUsageAllowed':False,'rateLimits':{'rateLimitReachedType':'rate_limit_reached','primary':{'usedPercent':100,'windowDurationMins':10080,'resetsAt':1791487468}},'rateLimitResetCredits':{'availableCount':1,'credits':[{'id':'credit-A','status':'available','resetType':'codexRateLimits','grantedAt':1,'expiresAt':None}]}}
  if MODE=='invalid_limits':result['ordinaryUsageAllowed']=1
  if MODE=='no_account':result.pop('accountId')
 elif method=='model/list':
  row={'id':'gpt-6-astra','model':'gpt-6-astra','displayName':'Astra','description':'Frontier','isDefault':True,'hidden':False,'defaultReasoningEffort':'medium','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}
  result={'data':[row],'nextCursor':None}
  if MODE=='duplicate_model':result['data']=[row,row]
  if MODE=='pages':
   cursor=r['params'].get('cursor');result={'data':[dict(row,id='gpt-6-sol',model='gpt-6-sol')] if cursor else [row],'nextCursor':None if cursor else 'two'}
  if MODE=='infinite_pages':result={'data':[],'nextCursor':'again'}
 elif method=='account/rateLimitResetCredit/consume':result={'outcome':MODE.split('outcome_',1)[1] if MODE.startswith('outcome_') else 'reset'}
 elif method=='thread/start':
  assert r['params']['ephemeral'] is True
  assert r['params']['allowProviderModelFallback'] is False
  assert r['params']['permissions']=='quant_probe'
  assert r['params']['config']['permissions']['quant_probe']['network']['enabled'] is False
  assert r['params']['environments']==[] and r['params']['dynamicTools']==[]
  assert r['params']['config']['features']['shell_tool'] is False
  result={'thread':{'id':'thread-A'},'model':'gpt-6-sol' if MODE=='wrong_model' else r['params']['model'],'instructionSources':['/secret/AGENTS.md'] if MODE=='context' else []}
 elif method=='turn/start':
  assert r['params']['outputSchema']['properties']['ok']['type']=='boolean'
  result={'turn':{'id':'turn-A'}}
  event={'method':'turn/completed','params':{'threadId':'thread-A','turn':{'id':'turn-A','status':'completed','items':[{'type':'agentMessage','text':'{"ok":true}'}]}}}
  if MODE=='probe_failure':event['params']['turn'].update(status='failed',error={'codexErrorInfo':'usageLimitExceeded','message':'SECRET_VALUE'})
  if MODE in ('numeric_true','float_true'):event['params']['turn']['items'][0]['text']='{"ok":1}' if MODE=='numeric_true' else '{"ok":1.0}'
  if MODE in ('foreign_turn','missing_turn'):
   params={'threadId':'thread-A','item':{'type':'agentMessage','text':'{"ok":true}'}}
   if MODE=='foreign_turn':params['turnId']='turn-other'
   send({'method':'item/completed','params':params});event['params']['turn']['items']=[]
  if MODE=='matching_item':
   send({'method':'item/completed','params':{'threadId':'thread-A','turnId':'turn-A','item':{'type':'agentMessage','text':'{"ok":true}'}}});event['params']['turn']['items']=[]
  if MODE=='bad_answer':event['params']['turn']['items'][0]['text']='{"ok":false}'
  if MODE=='terminal_tool':event['params']['turn']['items'].append({'type':'commandExecution'})
  if MODE=='tool':send({'method':'item/started','params':{'threadId':'thread-A','turnId':'turn-A','item':{'type':'commandExecution'}}})
  # Both directions are legal: notifications can precede the start response.
  if MODE=='early_notification':send(event)
  send({'id':ident,'result':result})
  if MODE!='early_notification':send(event)
  continue
 else:raise AssertionError(method)
 send({'id':ident,'result':result})
'''


def client(tmp_path, mode='normal'):
    binary=tmp_path/'fake-codex';trace=tmp_path/'trace.jsonl'
    binary.write_text('#!'+sys.executable+'\n'+SERVER.replace('__MODE__',repr(mode)).replace('__TRACE__',repr(str(trace))))
    binary.chmod(0o700)
    return c.CodexControl(binary, tmp_path/'evidence'),trace


def requests(trace):return [json.loads(x) for x in trace.read_text().splitlines()]


def test_capabilities_are_readonly_account_scoped_and_source_pinned(tmp_path):
    cli,trace=client(tmp_path);result=cli.capabilities(timeout_seconds=2,ttl_seconds=300)
    assert result['account_scope_sha256']==c._hash('account-A')
    assert result['reset_supported'] is True and result['limits']['ordinaryUsageAllowed'] is False
    assert [r['method'] for r in requests(trace)]==['initialize','initialized','account/rateLimits/read','model/list']
    assert result['models'][0]['id']=='gpt-6-astra'
    body=next((tmp_path/'evidence').glob('*.json')).read_bytes()
    assert hashlib.sha256(body).hexdigest()==result['evidence_sha256']
    assert b'account-A' not in body and b'credit-A' not in body and b'SECRET_HOME' not in body


@pytest.mark.parametrize('mode,kind', [('timeout','timeout'),('stderr_flood','output_limit'),('stdout_flood','line_limit'),('broken_json','invalid_json'),('duplicate_json','invalid_json'),('nonfinite','invalid_json'),('wrong_id','unexpected_response_id'),('server_request','unexpected_server_request'),('early_exit','process_exited'),('truncated','truncated_json'),('child_pipe','timeout'),('rpc_error','rpc_error')])
def test_protocol_faults_are_bounded_retained_and_do_not_reset(tmp_path,mode,kind):
    cli,trace=client(tmp_path,mode);start=time.monotonic()
    with pytest.raises(c.ControlError) as found:cli.read_limits(timeout_seconds=1 if mode in ('timeout','child_pipe') else 2)
    assert found.value.kind==kind
    assert time.monotonic()-start<3
    record=json.loads(next((tmp_path/'evidence').glob('*.json')).read_bytes())
    assert record['failure']['kind']==kind and record['transport']['streams']
    assert 'SECRET_VALUE' not in json.dumps(record)
    assert not any(r['method'].endswith('/consume') for r in requests(trace))


@pytest.mark.parametrize('value',[0,-1,121,True,float('nan'),float('inf'),'30'])
def test_invalid_deadline_never_starts_process(tmp_path,value):
    cli,trace=client(tmp_path)
    with pytest.raises(ValueError):cli.read_limits(timeout_seconds=value)
    assert not trace.exists()


@pytest.mark.parametrize('mode,kind',[('invalid_limits','invalid_rate_limits'),('duplicate_model','duplicate_model'),('infinite_pages','model_page_limit')])
def test_result_validation_and_catalog_bound(tmp_path,mode,kind):
    cli,_=client(tmp_path,mode)
    with pytest.raises(c.ControlError) as found:cli.capabilities(timeout_seconds=2)
    assert found.value.kind==kind


def test_catalog_pagination_preserves_both_models(tmp_path):
    cli,_=client(tmp_path,'pages')
    assert [v['id'] for v in cli.list_models(timeout_seconds=2)['data']]==['gpt-6-astra','gpt-6-sol']


@pytest.mark.parametrize('outcome',['reset','alreadyRedeemed','nothingToReset','noCredit'])
def test_explicit_reset_uses_exact_uuid_and_credit_once(tmp_path,outcome):
    cli,trace=client(tmp_path,'outcome_'+outcome);key=str(uuid.uuid4())
    result=cli.consume_reset(key,'credit-A',expected_account_scope_sha256=c._hash('account-A'),timeout_seconds=2)
    assert result['outcome']==outcome
    reqs=requests(trace);consumes=[r for r in reqs if r['method'].endswith('/consume')]
    assert len(consumes)==1 and consumes[0]['params']=={'idempotencyKey':key,'creditId':'credit-A'}
    assert [r['method'] for r in reqs][-2:]==['account/rateLimits/read','account/rateLimitResetCredit/consume']


def test_account_switch_prevents_reset(tmp_path):
    cli,trace=client(tmp_path)
    with pytest.raises(c.ControlError,match='account_scope_changed'):
        cli.consume_reset(str(uuid.uuid4()),expected_account_scope_sha256=c._hash('other-account'),timeout_seconds=2)
    assert not any(r['method'].endswith('/consume') for r in requests(trace))


@pytest.mark.parametrize('key,scope',[('not-a-uuid','a'*64),(str(uuid.uuid4()),None)])
def test_unowned_reset_is_rejected_before_process(tmp_path,key,scope):
    cli,trace=client(tmp_path)
    with pytest.raises(ValueError):cli.consume_reset(key,expected_account_scope_sha256=scope)
    assert not trace.exists()


@pytest.mark.parametrize('mode,status',[('normal','available'),('early_notification','available'),('wrong_model','unavailable'),('context','unavailable'),('probe_failure','quota'),('bad_answer','transport_error'),('tool','transport_error'),('terminal_tool','transport_error'),('numeric_true','transport_error'),('float_true','transport_error'),('foreign_turn','transport_error'),('missing_turn','transport_error'),('matching_item','available')])
def test_probe_exact_model_and_terminal_evidence(tmp_path,mode,status):
    cli,trace=client(tmp_path,mode)
    result=cli.probe_model('gpt-6-astra',operation_id=str(uuid.uuid4()),timeout_seconds=2)
    assert result['status']==status and len(result['evidence_sha256'])==64
    if status=='available':assert result['reported_model']=='gpt-6-astra'
    if mode=='probe_failure':
        assert result['codex_error_info']==result['code']=='usageLimitExceeded'
        assert result['source']=='codex_structured'
    if mode in ('wrong_model','context'):assert not any(r['method']=='turn/start' for r in requests(trace))
    assert not any(r['method'].endswith('/consume') for r in requests(trace))


def test_unknown_account_is_not_fabricated(tmp_path):
    cli,_=client(tmp_path,'no_account')
    assert cli.read_limits(timeout_seconds=2)['account_scope_sha256'] is None


def test_symlink_evidence_directory_never_creates_external_children(tmp_path):
    cli,_=client(tmp_path);external=tmp_path/'external';external.mkdir()
    link=tmp_path/'link';link.symlink_to(external,target_is_directory=True)
    cli.evidence_dir=link/'new-directory'
    with pytest.raises(c.ControlError,match='unsafe_evidence_directory'):cli.read_limits(timeout_seconds=2)
    assert list(external.iterdir())==[]


def test_real_router_consumes_control_quota_evidence_before_mock_reset(tmp_path):
    from betting_engine.provider_recovery import ProviderRouter, POLICY_VERSION
    cli, trace = client(tmp_path, 'probe_failure')
    pin = 'a' * 64
    policy = {'version': POLICY_VERSION, 'account_scope_sha256': c._hash('account-A'),
              'capability_evidence_sha256': pin, 'verified_at': 0, 'expires_at': 10000,
              'reset_supported': True, 'allow_temporary_claude': False,
              'routes': [{'id': 'primary', 'provider': 'codex', 'family': 'gpt',
                          'model': 'gpt-6-astra', 'reasoning_effort': 'high', 'capability_sha256': pin}]}
    router = ProviderRouter(tmp_path / 'router', policy, cli, clock=lambda: 1000, jitter=lambda: 0.5)
    result = router.periodic_tick(max_actions=3)
    state = json.loads(router.path.read_bytes())
    assert result['selection'] is None
    assert len(state['resets']) == 1 and state['resets'][0]['status'] == 'reset'
    methods = [row['method'] for row in requests(trace)]
    assert methods.count('turn/start') == 1
    assert methods.count('account/rateLimits/read') == 2  # router diagnosis, then consume account CAS
    assert methods.count('account/rateLimitResetCredit/consume') == 1
    assert state['quota_episode']['resolved_at'] is None
