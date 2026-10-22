"""Unit tests for Thread-Safe Bounded In-Memory Cache."""
import time
import tempfile
from pathlib import Path
import pytest

from betting_engine.in_memory_cache import BoundedMemoryCache


def test_bounded_memory_cache_compute():
    cache = BoundedMemoryCache(max_entries=10)
    counter = 0

    def calc():
        nonlocal counter
        counter += 1
        return "result"

    # First call -> compute
    res1 = cache.get_or_compute("k1", calc, ttl_seconds=10.0)
    assert res1 == "result"
    assert counter == 1

    # Second call -> hit
    res2 = cache.get_or_compute("k1", calc, ttl_seconds=10.0)
    assert res2 == "result"
    assert counter == 1  # Not incremented

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["entries"] == 1


def test_bounded_memory_cache_file():
    cache = BoundedMemoryCache(max_entries=10)
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "data.txt"
        test_file.write_text("v1", encoding="utf-8")

        read_count = 0
        def loader(p):
            nonlocal read_count
            read_count += 1
            return p.read_text(encoding="utf-8")

        val1 = cache.get_or_load_file(test_file, loader, ttl_seconds=10.0)
        assert val1 == "v1"
        assert read_count == 1

        # Second read -> hit
        val2 = cache.get_or_load_file(test_file, loader, ttl_seconds=10.0)
        assert val2 == "v1"
        assert read_count == 1

        # Mutate file -> cache detects mtime/size change and reloads
        time.sleep(0.02)
        test_file.write_text("v2_modified", encoding="utf-8")
        val3 = cache.get_or_load_file(test_file, loader, ttl_seconds=10.0)
        assert val3 == "v2_modified"
        assert read_count == 2


def test_bounded_memory_cache_eviction():
    cache = BoundedMemoryCache(max_entries=3)
    for i in range(5):
        cache.get_or_compute(f"k_{i}", lambda: i, ttl_seconds=10.0)

    stats = cache.stats()
    assert stats["entries"] <= 3
    assert stats["evictions"] > 0
