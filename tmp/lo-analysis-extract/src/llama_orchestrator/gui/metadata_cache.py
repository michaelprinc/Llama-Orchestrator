"""Metadata cache for engine and instance state caching.

Extracted from app.py (Phase 5: Module extraction).
Provides caching for engine metadata, instance states, and GPU info.

NOTE: This module does NOT import from app.py to avoid circular imports.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntry:
    """A single cache entry with expiry."""
    value: Any
    created_at: float = field(default_factory=time.time)
    ttl_seconds: float = 300.0  # 5 minutes default


@dataclass
class MetadataCache:
    """Thread-safe cache for engine and instance metadata."""
    _entries: dict[str, CacheEntry] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    max_size: int = 1000

    def get(self, key: str) -> Any | None:
        """Get a value from cache.

        Args:
            key: Cache key.

        Returns:
            Cached value or None if expired/missing.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None

            # Check expiry
            if time.time() - entry.created_at > entry.ttl_seconds:
                del self._entries[key]
                return None

            return entry.value

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: float = 300.0,
    ) -> None:
        """Set a value in cache.

        Args:
            key: Cache key.
            value: Value to cache.
            ttl_seconds: Time-to-live in seconds.
        """
        with self._lock:
            # Evict old entries if at capacity
            if len(self._entries) >= self.max_size:
                self._evict_oldest()

            self._entries[key] = CacheEntry(value=value, ttl_seconds=ttl_seconds)

    def delete(self, key: str) -> bool:
        """Delete a cache entry.

        Args:
            key: Cache key to delete.

        Returns:
            True if entry existed, False otherwise.
        """
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        """Return number of entries in cache."""
        with self._lock:
            return len(self._entries)

    def _evict_oldest(self) -> None:
        """Evict the oldest entry from cache.

        Called only from within set(), which already holds the lock.
        RLock allows re-entry, so this is safe.
        """
        if not self._entries:
            return

        oldest_key = min(
            self._entries,
            key=lambda k: self._entries[k].created_at,
        )
        del self._entries[oldest_key]


def create_instance_cache() -> MetadataCache:
    """Create a metadata cache instance with sensible defaults."""
    return MetadataCache(max_size=500)


def invalidate_instance_cache(
    cache: MetadataCache,
    instance_name: str,
) -> None:
    """Invalidate cache entries for a specific instance.

    Args:
        cache: The MetadataCache instance.
        instance_name: Name of the instance to invalidate.
    """
    with cache._lock:
        keys_to_delete = [
            key for key in cache._entries
            if key.startswith(f"instance:{instance_name}:")
        ]
    for key in keys_to_delete:
        cache.delete(key)


def invalidate_all_caches(*caches: MetadataCache) -> None:
    """Invalidate all caches.

    Args:
        caches: MetadataCache instances to clear.
    """
    for cache in caches:
        cache.clear()
