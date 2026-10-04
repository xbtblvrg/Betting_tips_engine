"""Successful tests must not leave large generated fixture trees behind."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('fail', [False, True])
def test_config_removes_successful_tmp_inputs_but_retains_failure_evidence(tmp_path, fail):
    project = Path(__file__).resolve().parents[1]
    config = tmp_path/'pytest.ini'
    # Read actual deployed policy, not a second hardcoded configuration.
    import tomllib
    policy = tomllib.loads((project/'pyproject.toml').read_text())['tool']['pytest']['ini_options']
    assert policy['tmp_path_retention_policy'] == 'failed'
    config.write_text('[pytest]\ntmp_path_retention_policy = '+policy['tmp_path_retention_policy']+'\ntmp_path_retention_count = '+str(policy['tmp_path_retention_count'])+'\n')
    marker = tmp_path/'marker.json'
    test = tmp_path/'test_sample.py'
    test.write_text('from pathlib import Path\nimport json\ndef test_sample(tmp_path):\n'
                    '    (tmp_path/"generated").write_bytes(b"x"*1024)\n'
                    f'    Path({str(marker)!r}).write_text(json.dumps(str(tmp_path)))\n'
                    f'    assert {not fail!r}\n')
    temp = tmp_path/'temp'; temp.mkdir()
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1', 'PYTEST_DEBUG_TEMPROOT':str(temp)}
    done = subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider','-c',str(config),str(test)],
                          cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert done.returncode == (1 if fail else 0), done.stdout+done.stderr
    generated = Path(json.loads(marker.read_text()))/'generated'
    assert generated.exists() == fail
