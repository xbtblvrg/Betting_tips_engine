"""The disposable control must accept its prompt before it exits."""
import pytest
from betting_engine import native_boundary as boundary

@pytest.mark.parametrize('payload_bytes',[32768,1048576])
def test_positive_probe_drains_entire_prompt_without_false_input_failure(monkeypatch,payload_bytes):
    # Exercise real pipe delivery; policy-denial semantics have separate tests.
    # A nested sandbox cannot independently launch another sandbox-exec.
    monkeypatch.setattr(boundary.sys,'platform','darwin')
    class ProtectedControlReached(Exception): pass
    invoke=boundary.ai_delegation.invoke
    processes=[]
    def large_prompt(provider,config,prompt,**kwargs):
        if config.get('_protected_root'):
            raise ProtectedControlReached()
        result=invoke(provider,config,'x'*payload_bytes,**kwargs)
        processes.append(result)
        return result
    monkeypatch.setattr(boundary.ai_delegation,'invoke',large_prompt)
    with pytest.raises(ProtectedControlReached):
        boundary.exercise_native_boundary()
    assert len(processes)==1
    assert processes[0]['returncode']==0 and processes[0]['state'] is None
