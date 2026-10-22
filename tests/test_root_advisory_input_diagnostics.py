"""Local subprocess regressions; no model requests or provider credentials."""
import json
import sys
import time

import pytest

from betting_engine.ai_delegation import MAX_BYTES, decode, invoke


def child(tmp_path, body):
    path = tmp_path / 'local-advisory-fixture'
    path.write_text('#!' + sys.executable + '\nimport os, sys, time\nos.close(0)\n' + body)
    path.chmod(0o700)
    return {'binary': str(path)}


def test_closed_input_preserves_delayed_startup_diagnostic(tmp_path):
    config = child(tmp_path, 'time.sleep(.1)\nsys.stderr.write("specific startup failure\\n")\nsys.stderr.flush()\nsys.exit(2)\n')
    result = invoke('claude', config, 'x' * 1000000, timeout=10)
    assert result['state'] == 'input_error'
    assert result['stderr'] == 'specific startup failure\n'
    assert result['returncode'] == 2
    assert decode('claude', result) == ('input_error', None)


def test_closed_input_never_accepts_plausible_advice(tmp_path):
    payload = json.dumps({'result': 'Plausible but incomplete-input advice', 'modelUsage': {'claude-opus-5-5': {}}})
    config = child(tmp_path, 'time.sleep(.1)\nprint(' + repr(payload) + ', flush=True)\n')
    result = invoke('claude', config, 'x' * 1000000, timeout=10)
    assert result['stdout'].strip() == payload
    assert decode('claude', result, 'claude-opus-5-5') == ('input_error', None)


@pytest.mark.parametrize('inherited', [False, True])
def test_closed_input_still_bounds_child_and_inherited_pipe_lifetime(tmp_path, inherited):
    body = ('if os.fork() != 0: os._exit(2)\n' if inherited else '') + 'time.sleep(30)\n'
    config = child(tmp_path, body)
    started = time.monotonic()
    result = invoke('claude', config, 'x' * 1000000, timeout=.3)
    assert time.monotonic() - started < 3
    assert result['state'] == 'timeout'
    assert result['returncode'] is not None
    assert decode('claude', result) == ('timeout', None)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_closed_input_keeps_per_stream_output_limit(tmp_path, stream):
    config = child(tmp_path, 'time.sleep(.1)\nsys.' + stream + '.write("x" * 300000)\nsys.' + stream + '.flush()\n')
    result = invoke('claude', config, 'x' * 1000000, timeout=10)
    assert result['state'] == 'output_limit'
    assert len(result[stream]) == MAX_BYTES
    assert decode('claude', result) == ('output_limit', None)
