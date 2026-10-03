from betting_engine import development


def test_diagnosed_regression_sources_reuses_exact_immutable_inputs(monkeypatch):
    development._DIAG_SOURCE_CACHE.clear()
    calls = []
    original = development._diagnosed_regression_sources_uncached

    def count(names, text):
        calls.append((tuple(names), text))
        return original(names, text)

    monkeypatch.setattr(development, '_diagnosed_regression_sources_uncached', count)
    manifest = {
        'tests/test_alpha.py': 'sha-alpha',
        'tests/test_beta.py': 'sha-beta',
        'docs/diagnostic.json': 'sha-doc',
    }
    text = 'Repair tests/test_alpha.py and tests/test_beta.py'
    assert development._diagnosed_regression_sources(manifest, text) == [
        'tests/test_alpha.py', 'tests/test_beta.py']
    assert development._diagnosed_regression_sources(manifest, text) == [
        'tests/test_alpha.py', 'tests/test_beta.py']
    assert len(calls) == 1

    development._diagnosed_regression_sources(manifest, text + ' changed')
    assert len(calls) == 2
    changed_manifest = {**manifest, 'tests/test_gamma.py': 'sha-gamma'}
    development._diagnosed_regression_sources(changed_manifest, text)
    assert len(calls) == 3


def test_diagnosed_regression_source_cache_is_bounded(monkeypatch):
    development._DIAG_SOURCE_CACHE.clear()
    monkeypatch.setattr(development, '_diagnosed_regression_sources_uncached', lambda names, text: [])
    for index in range(development._DIAG_SOURCE_CACHE_MAX + 10):
        development._diagnosed_regression_sources(
            {f'tests/test_{index}.py': 'sha'}, f'tests/test_{index}.py')
    assert len(development._DIAG_SOURCE_CACHE) == development._DIAG_SOURCE_CACHE_MAX
