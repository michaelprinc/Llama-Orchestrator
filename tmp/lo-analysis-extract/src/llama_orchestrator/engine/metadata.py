"""Cached runtime metadata for the GUI refresh cycle.

This module provides a single-shot cache that pre-computes expensive
instance-level queries (command-line building, GPU label resolution,
model-size stat) once per background refresh, so the main thread can
render rows from plain data instead of invoking subprocesses, parsing
flags, or calling ``stat()``.

Usage inside a background refresh::

    cache = MetadataCache()
    cache.load_all(configs)  # called once
    for name, row in build_rows(configs, cache):
        meta = cache.get(name)  # instant dict lookup

If any config changed (detected via model_dump hash), the caller
invalidates ``load_all()`` on the next refresh cycle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from llama_orchestrator.config import InstanceConfig
from llama_orchestrator.engine.detection import EffectiveRuntimeSelection


@dataclass(frozen=True)
class InstanceMetadata:
    """Immutable snapshot of pre-computed instance data for one refresh cycle.

    Attributes:
        effective_runtime: Resolved CPU/GPU backend selection (GPU labels,
            layers, thread count).
        model_size_gb: Model file size in GB (base-1024).  ``None`` when
            the file is missing or unreadable.
        config_hash: Deterministic hash of the config's JSON serialisation
            (sorted keys).  Used by ``MetadataCache.is_stale()`` to detect
            config mutations.
    """

    effective_runtime: EffectiveRuntimeSelection
    model_size_gb: float | None
    config_hash: str


class MetadataCache:
    """Thread-safe cache for per-instance runtime metadata.

    The cache is **single-use per refresh cycle**: call ``load_all()``
    once, then read via ``get()``.  The ``is_stale()`` method tells the
    caller whether a new ``load_all()`` is warranted.

    Thread-safety note
    ------------------
    The cache is not protected by locks.  It is designed for the
    *producer-consumer* pattern where one background thread calls
    ``load_all()`` and posts a reference to the cache, while the main
    thread only reads from the same immutable cache instance.
    """

    __slots__ = ("_cache",)

    _cache: dict[str, InstanceMetadata]

    def __init__(self) -> None:
        self._cache = {}

    # -- loading --------------------------------------------------------

    def load_all(self, configs: dict[str, InstanceConfig]) -> None:
        """Pre-compute metadata for every config and store it.

        This is the expensive O(N) operation that should run on a
        background thread.  It calls ``describe_effective_runtime()``
        and ``resolve_model_size_gb()`` **exactly once per instance**.

        Args:
            configs: Instance name → config mapping, typically produced
                by ``load_all_instances()``.
        """
        from llama_orchestrator.engine.detection import (
            describe_effective_runtime,
            resolve_model_size_gb,
        )

        self._cache = {}
        for name, config in configs.items():
            effective = describe_effective_runtime(config)
            model_size = resolve_model_size_gb(config)
            raw = json.dumps(config.model_dump(), sort_keys=True).encode()
            h = hashlib.md5(raw).hexdigest()
            self._cache[name] = InstanceMetadata(
                effective_runtime=effective,
                model_size_gb=model_size,
                config_hash=h,
            )

    # -- read -----------------------------------------------------------

    def get(self, name: str) -> InstanceMetadata | None:
        """Return cached metadata for *name*, or ``None``."""
        return self._cache.get(name)

    def get_runtime_selection(self, name: str) -> EffectiveRuntimeSelection | None:
        """Shortcut for the effective runtime selection only."""
        entry = self._cache.get(name)
        return entry.effective_runtime if entry else None

    # -- staleness detection --------------------------------------------

    def is_stale(self, configs: dict[str, InstanceConfig]) -> bool:
        """Return ``True`` when *configs* differ from what was loaded.

        Iterates only over the configs currently passed in (no external
        file reads).  If a new instance appeared or an existing config
        changed, the caller should invalidate and reload.

        Args:
            configs: Same mapping passed to ``load_all()``.

        Returns:
            ``True`` if the cache needs a full reload.
        """
        for name, config in configs.items():
            entry = self._cache.get(name)
            if entry is None:
                return True
            raw = json.dumps(config.model_dump(), sort_keys=True).encode()
            h = hashlib.md5(raw).hexdigest()
            if h != entry.config_hash:
                return True
        return False

    # -- partial invalidation -------------------------------------------

    def invalidate(self, name: str) -> None:
        """Remove a single entry so it gets re-computed on the next ``load_all()``.

        Call this when the user edits a specific instance's config and
        you want to avoid a full ``load_all()`` during the next refresh
        cycle.
        """
        self._cache.pop(name, None)

    # -- capacity -------------------------------------------------------

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, name: str) -> bool:
        return name in self._cache
