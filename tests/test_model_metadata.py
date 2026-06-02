"""Tests for additive model metadata generation and migration."""

from __future__ import annotations

import struct
from pathlib import Path

from llama_orchestrator.config import InstanceConfig, ModelConfig, ModelMetadata, ModelMetadataUserMetadata, save_config
from llama_orchestrator.config.migration import migrate_model_metadata
from llama_orchestrator.hf_import import ImportedModelSelection
from llama_orchestrator.model_metadata import build_model_metadata

_GGUF_TYPE_STRING = 8
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_UINT64 = 10


def _write_gguf_string(handle, value: str) -> None:
    encoded = value.encode("utf-8")
    handle.write(struct.pack("<Q", len(encoded)))
    handle.write(encoded)


def _write_gguf_value(handle, value) -> None:
    if isinstance(value, str):
        handle.write(struct.pack("<I", _GGUF_TYPE_STRING))
        _write_gguf_string(handle, value)
        return
    if isinstance(value, int):
        gguf_type = _GGUF_TYPE_UINT32 if value <= 0xFFFFFFFF else _GGUF_TYPE_UINT64
        handle.write(struct.pack("<I", gguf_type))
        handle.write(struct.pack("<I" if gguf_type == _GGUF_TYPE_UINT32 else "<Q", value))
        return
    raise TypeError(f"Unsupported test GGUF metadata value: {value!r}")


def _write_test_gguf(path: Path, metadata: dict[str, object]) -> None:
    with path.open("wb") as handle:
        handle.write(b"GGUF")
        handle.write(struct.pack("<I", 3))
        handle.write(struct.pack("<Q", 0))
        handle.write(struct.pack("<Q", len(metadata)))
        for key, value in metadata.items():
            _write_gguf_string(handle, key)
            _write_gguf_value(handle, value)


def test_build_model_metadata_uses_gguf_and_preserves_user_metadata(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "demo-Q4_0.gguf"
    model_path.parent.mkdir(parents=True)
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "llama",
            "llama.context_length": 131072,
            "llama.block_count": 32,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
            "llama.attention.value_length": 128,
        },
    )

    config = InstanceConfig(
        name="demo",
        model=ModelConfig(path=model_path),
        tags=["local", "reasoning"],
        model_metadata=ModelMetadata(user_metadata=ModelMetadataUserMetadata(notes="keep-me", favorite=True)),
    )

    metadata = build_model_metadata(config)

    assert metadata.identity.architecture == "llama"
    assert metadata.context.native_context_length == 131072
    assert metadata.quantization.name == "Q4_0"
    assert metadata.derived.formula_version == "kv_cache_v1_gqa"
    assert metadata.derived.memory_model.get("status") == "ok"
    assert metadata.user_metadata.notes == "keep-me"
    assert metadata.user_metadata.favorite is True


def test_build_model_metadata_uses_hf_selection_when_available(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "Qwen3-8B-Q4_K_M.gguf"
    model_path.parent.mkdir(parents=True)
    _write_test_gguf(model_path, {"general.architecture": "llama"})

    config = InstanceConfig(
        name="demo-hf",
        model=ModelConfig(path=model_path),
        tags=["hf", "hf_repo__qwen__qwen3-8b-gguf"],
    )
    selection = ImportedModelSelection(
        repo_id="Qwen/Qwen3-8B-GGUF",
        filename="Qwen3-8B-Q4_K_M.gguf",
        local_path=model_path,
        quantization="Q4_K_M",
        size_bytes=123,
    )

    metadata = build_model_metadata(config, imported_selection=selection)

    assert metadata.source.hub == "huggingface"
    assert metadata.source.repo_id == "Qwen/Qwen3-8B-GGUF"
    assert metadata.source.filename == "Qwen3-8B-Q4_K_M.gguf"
    assert metadata.artifact.remote_size_bytes == 123
    assert metadata.quantization.name == "Q4_K_M"


def test_migrate_model_metadata_preview_and_apply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
    model_path = tmp_path / "models" / "demo-Q8_0.gguf"
    model_path.parent.mkdir(parents=True)
    _write_test_gguf(model_path, {"general.architecture": "llama"})

    config = InstanceConfig(
        name="demo-migrate",
        model=ModelConfig(path=Path("models/demo-Q8_0.gguf")),
        tags=["tag-1"],
    )
    save_config(config)

    preview = migrate_model_metadata(apply=False)
    assert preview.total == 1
    assert preview.changed == 1
    assert preview.records[0].backup_path is None

    applied = migrate_model_metadata(apply=True)
    assert applied.total == 1
    assert applied.changed == 1
    assert applied.records[0].backup_path is not None
    assert applied.records[0].backup_path.exists()
