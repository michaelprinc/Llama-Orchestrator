"""Tests for engine.metadata.MetadataCache staleness detection (R15/D4).

The cache is single-use per refresh cycle: ``load_all(configs)`` once, then
``is_stale(next_configs)`` decides whether a reload is warranted.  R15/D4
requires that *removed* instance names also invalidate the cache — previously
only added/changed configs did.
"""

import copy
from pathlib import Path

import pytest

from llama_orchestrator.config import InstanceConfig, ModelConfig
from llama_orchestrator.engine.detection import EffectiveRuntimeSelection
from llama_orchestrator.engine.metadata import MetadataCache

# Fixed identity fields so two _config(name) calls serialise identically.
_FIXED_UID = "11111111-2222-4333-8444-555555555555"
_FIXED_TS = "2026-01-01T00:00:00Z"


def _config(name: str) -> InstanceConfig:
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path("model.gguf")),
        instance_uid=_FIXED_UID,
        created_at=_FIXED_TS,
        updated_at=_FIXED_TS,
    )


def _selection() -> EffectiveRuntimeSelection:
    return EffectiveRuntimeSelection(
        backend="cpu",
        threads=None,
        gpu_layers=0,
        cpu_active=True,
        gpu_active=False,
        gpu_labels=(),
        primary_gpu_label=None,
        primary_device_id=None,
    )


@pytest.fixture
def patched_detection(monkeypatch):
    """Keep load_all hermetic: no subprocesses, no filesystem stats."""
    monkeypatch.setattr(
        "llama_orchestrator.engine.detection.describe_effective_runtime",
        lambda config: _selection(),
    )
    monkeypatch.setattr(
        "llama_orchestrator.engine.detection.resolve_model_size_gb",
        lambda config: 1.0,
    )


class TestIsStale:
    """R15/D4: is_stale() covers added, mutated, and removed instances."""

    def test_unchanged_configs_not_stale(self, patched_detection):
        cache = MetadataCache()
        cache.load_all({"a": _config("a"), "b": _config("b")})
        assert not cache.is_stale({"a": _config("a"), "b": _config("b")})

    def test_added_instance_is_stale(self, patched_detection):
        cache = MetadataCache()
        cache.load_all({"a": _config("a")})
        assert cache.is_stale({"a": _config("a"), "b": _config("b")})

    def test_removed_instance_is_stale(self, patched_detection):
        """AC: removing an instance must invalidate the cache even though
        every remaining config hash is unchanged."""
        cache = MetadataCache()
        cache.load_all({"a": _config("a"), "b": _config("b")})
        assert cache.is_stale({"a": _config("a")})

    def test_removing_all_instances_is_stale(self, patched_detection):
        cache = MetadataCache()
        cache.load_all({"a": _config("a")})
        assert cache.is_stale({})

    def test_mutated_config_is_stale(self, patched_detection):
        cache = MetadataCache()
        cache.load_all({"a": _config("a")})
        mutated = copy.deepcopy(_config("a"))
        mutated.args = ["-ngl", "99"]
        assert cache.is_stale({"a": mutated})

    def test_removal_recovers_after_reload(self, patched_detection):
        """A reload on the shrunken set clears the stale flag."""
        cache = MetadataCache()
        cache.load_all({"a": _config("a"), "b": _config("b")})
        assert cache.is_stale({"a": _config("a")})
        cache.load_all({"a": _config("a")})
        assert not cache.is_stale({"a": _config("a")})
