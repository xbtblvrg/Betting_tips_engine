"""Thread-safe, Bounded In-Memory Cache with TTL and File Modification Invalidation.

Prevents redundant disk I/O, file reading, and repeated JSON deserialization:
1. Fast RAM lookups for public sports schedules, weather forecasts, and odds snapshots.
2. Dual invalidation: TTL expiration AND file modification (st_mtime, st_size) detection.
3. Bounded capacity (max 500 items) with automatic LRU pruning to prevent memory growth.
4. Thread-safe operations with mutex locking.
5. Telemetry & hit-rate tracking reported to AI supervision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from .system_resource_governor import log_acceleration_event

logger = logging.getLogger(__name__)

MAX_CACHE_ENTRIES = 500


@dataclass
class CacheEntry:
    value: Any
    created_at: float
    ttl_seconds: float
    file_signature: Optional[Tuple[int, int]] = None  # (st_size, st_mtime_ns)
    last_accessed: float = 0.0


class BoundedMemoryCache:
    """Thread-safe bounded memory cache with dual TTL and mtime invalidation."""

    def __init__(self, max_entries: int = MAX_CACHE_ENTRIES):
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._cache: Dict[str, CacheEntry] = {}
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0

    def get_or_load_file(
        self,
        file_path: Path | str,
        loader_fn: Callable[[Path], Any],
        ttl_seconds: float = 300.0,
    ) -> Any:
        """Retrieves cached file contents if valid by (size, mtime) and TTL, or executes loader."""
        path = Path(file_path)
        if not path.exists():
            return loader_fn(path)

        try:
            stat = path.stat()
            file_sig = (stat.st_size, stat.st_mtime_ns)
        except Exception:
            return loader_fn(path)

        cache_key = f"file:{path.resolve()}"
        now = time.time()

        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is not None:
                # Check both TTL and file modification signature
                if (now - entry.created_at < entry.ttl_seconds) and (entry.file_signature == file_sig):
                    self.hits += 1
                    entry.last_accessed = now
                    return entry.value
            self.misses += 1

        # Compute outside lock to avoid blocking readers
        val = loader_fn(path)

        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._prune_lru()
            self._cache[cache_key] = CacheEntry(
                value=val,
                created_at=time.time(),
                ttl_seconds=ttl_seconds,
                file_signature=file_sig,
                last_accessed=time.time(),
            )
        return val

    def get_or_compute(
        self,
        key: str,
        compute_fn: Callable[[], Any],
        ttl_seconds: float = 300.0,
    ) -> Any:
        """Retrieves cached value if within TTL, otherwise computes and caches."""
        cache_key = f"key:{key}"
        now = time.time()

        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is not None and (now - entry.created_at < entry.ttl_seconds):
                self.hits += 1
                entry.last_accessed = now
                return entry.value
            self.misses += 1

        val = compute_fn()

        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._prune_lru()
            self._cache[cache_key] = CacheEntry(
                value=val,
                created_at=time.time(),
                ttl_seconds=ttl_seconds,
                last_accessed=time.time(),
            )
        return val

    def _prune_lru(self) -> None:
        """Prunes the oldest 20% of entries based on last_accessed."""
        if not self._cache:
            return
        sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k].last_accessed)
        num_to_remove = max(1, len(sorted_keys) // 5)
        for k in sorted_keys[:num_to_remove]:
            self._cache.pop(k, None)
            self.evictions += 1

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total_reqs = self.hits + self.misses
            hit_rate = round(self.hits / total_reqs, 4) if total_reqs > 0 else 1.0
            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "total_requests": total_reqs,
                "hit_rate": hit_rate,
                "evictions": self.evictions,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# Global Singleton for shared engine operations
GLOBAL_IN_MEMORY_CACHE = BoundedMemoryCache()
