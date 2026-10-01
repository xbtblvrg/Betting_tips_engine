from betting_engine.development import PROTECTED

def test_dashboard_instruction_runtime_requires_root_integration():
    for name in ('dashboard_payload.py', 'dashboard_process.py', 'dashboard_worker.py'):
        assert 'src/betting_engine/' + name in PROTECTED
