"""Controller pipe logs must describe a live child, not wait for child exit."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

import pytest

from betting_engine.development import DevelopmentWorker
from betting_engine.domain import utcnow


def worker_case(tmp_path, monkeypatch):
    from betting_engine import native_boundary
    # This test isolates the controller pipe behavior. Native sandbox controls
    # keep their existing separate real-process regressions unchanged.
    monkeypatch.setattr(native_boundary, 'controller_evidence', lambda *args: {})
    worker = DevelopmentWorker(tmp_path)
    worker.workspace = tmp_path / 'workspace'
    worker.workspace.mkdir()
    worker.run_started_at = utcnow().isoformat()
    run = worker.folder / 'runs' / 'stream-test'
    run.mkdir(parents=True)
    return worker, run


def test_small_stdout_and_stderr_are_visible_before_exit_and_all_bytes_survive(tmp_path, monkeypatch):
    worker, run = worker_case(tmp_path, monkeypatch)
    prefix = run / 'python-tests'
    release = worker.workspace / 'continue'
    command = [sys.executable, '-u', '-c',
        "import sys,time\nfrom pathlib import Path\n"
        "sys.stdout.buffer.write(b'progress\\n');sys.stdout.flush()\n"
        "sys.stderr.buffer.write(b'diagnostic\\n');sys.stderr.flush()\n"
        "while not Path('continue').exists():time.sleep(.01)\n"
        "sys.stdout.buffer.write(b'X'*20000+b'complete\\n');sys.stdout.flush()\n"
        "sys.stderr.buffer.write(b'Y'*17000+b'finished\\n');sys.stderr.flush()\n"]
    result = queue.Queue()
    def execute():
        try:
            worker.run_process(command, prefix, timeout=15)
            result.put(None)
        except BaseException as exc:
            result.put(exc)
    thread = threading.Thread(target=execute)
    thread.start()
    stdout = Path(str(prefix) + '.stdout.log')
    stderr = Path(str(prefix) + '.stderr.log')
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (stdout.exists() and stderr.exists()
                    and stdout.read_bytes() == b'progress\n'
                    and stderr.read_bytes() == b'diagnostic\n'):
                break
            time.sleep(.01)
        assert stdout.read_bytes() == b'progress\n'
        assert stderr.read_bytes() == b'diagnostic\n'
        assert thread.is_alive() and result.empty()
        pid = json.loads((run / 'child-process.json').read_text())['pid']
        os.kill(pid, 0)  # The observed bytes preceded the actual process exit.
    finally:
        release.write_text('continue')
        thread.join(timeout=12)
        if thread.is_alive():
            worker._stop.set()
            thread.join(timeout=12)
    assert not thread.is_alive()
    assert result.get_nowait() is None
    assert stdout.read_bytes() == b'progress\n' + b'X'*20000 + b'complete\n'
    assert stderr.read_bytes() == b'diagnostic\n' + b'Y'*17000 + b'finished\n'


def test_timeout_retains_flushed_logs_and_reaps_child(tmp_path, monkeypatch):
    worker, run = worker_case(tmp_path, monkeypatch)
    prefix = run / 'timeout'
    command = [sys.executable, '-u', '-c',
        "import sys,time;print('started',flush=True);"
        "print('waiting',file=sys.stderr,flush=True);time.sleep(30)"]
    with pytest.raises(TimeoutError, match='execution time limit'):
        worker.run_process(command, prefix, timeout=.15)
    assert Path(str(prefix) + '.stdout.log').read_bytes() == b'started\n'
    assert Path(str(prefix) + '.stderr.log').read_bytes() == b'waiting\n'
    pid = json.loads((run / 'child-process.json').read_text())['pid']
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_nonzero_exit_keeps_exact_diagnostic_bytes(tmp_path, monkeypatch):
    worker, run = worker_case(tmp_path, monkeypatch)
    prefix = run / 'failed'
    command = [sys.executable, '-u', '-c',
        "import sys;sys.stdout.buffer.write(b'partial\\x00output');"
        "sys.stderr.buffer.write(b'failure\\n');sys.exit(7)"]
    with pytest.raises(RuntimeError, match='failed exited 7'):
        worker.run_process(command, prefix, timeout=10)
    assert Path(str(prefix) + '.stdout.log').read_bytes() == b'partial\x00output'
    assert Path(str(prefix) + '.stderr.log').read_bytes() == b'failure\n'
