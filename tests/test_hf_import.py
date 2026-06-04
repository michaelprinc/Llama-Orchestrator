"""Tests for Hugging Face GGUF import helpers."""

import json
import struct
from pathlib import Path

import pytest

from llama_orchestrator.hf_import import (
    HuggingFaceImportError,
    ImportedModelSelection,
    ImportSettings,
    build_add_model_prefill,
    build_model_tags,
    infer_model_size_tag,
    load_import_settings,
    normalize_hf_model_reference,
    parse_gguf_quantization,
    plan_download_target,
    resolve_local_variant_path,
    save_import_settings,
    split_gguf_note,
    suggest_model_name,
    write_import_metadata_sidecar,
)

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


def test_normalize_hf_model_reference_accepts_owner_repo() -> None:
    ref = normalize_hf_model_reference("Qwen/Qwen3-8B-GGUF")

    assert ref.repo_id == "Qwen/Qwen3-8B-GGUF"
    assert ref.filename is None


def test_normalize_hf_model_reference_accepts_repo_url() -> None:
    ref = normalize_hf_model_reference("https://huggingface.co/Qwen/Qwen3-8B-GGUF")

    assert ref.repo_id == "Qwen/Qwen3-8B-GGUF"
    assert ref.filename is None


def test_normalize_hf_model_reference_accepts_file_url() -> None:
    ref = normalize_hf_model_reference(
        "https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/main/Qwen3-8B-Q4_K_M.gguf"
    )

    assert ref.repo_id == "Qwen/Qwen3-8B-GGUF"
    assert ref.filename == "Qwen3-8B-Q4_K_M.gguf"


def test_normalize_hf_model_reference_rejects_invalid_value() -> None:
    with pytest.raises(HuggingFaceImportError):
        normalize_hf_model_reference("not a valid repo")


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Qwen3-8B-Q4_K_M.gguf", "Q4_K_M"),
        ("google_gemma-4-27b-it-IQ4_XS.gguf", "IQ4_XS"),
        ("gemma-4-E2B-it-Q8_0.gguf", "Q8_0"),
        ("model-fp16.gguf", "FP16"),
        ("plain-model.gguf", None),
    ],
)
def test_parse_gguf_quantization(filename: str, expected: str | None) -> None:
    assert parse_gguf_quantization(filename) == expected


def test_split_gguf_note_marks_split_files() -> None:
    assert split_gguf_note("model-00001-of-00003.gguf") == "Split GGUF part 1/3"
    assert split_gguf_note("model.gguf") == ""


def test_plan_download_target_handles_existing_file_choices(tmp_path: Path) -> None:
    final_path = tmp_path / "models" / "repo" / "model.gguf"
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"existing")

    use_existing = plan_download_target(final_path, existing_choice="use_existing")
    redownload = plan_download_target(final_path, existing_choice="redownload")
    cancel = plan_download_target(final_path, existing_choice="cancel")

    assert use_existing.action == "use_existing"
    assert use_existing.temp_path is None
    assert redownload.action == "download"
    assert redownload.temp_path is not None
    assert redownload.temp_path.parent == final_path.parent
    assert cancel.action == "cancel"


def test_plan_download_target_requires_existing_choice_for_existing_file(tmp_path: Path) -> None:
    final_path = tmp_path / "model.gguf"
    final_path.write_bytes(b"existing")

    with pytest.raises(HuggingFaceImportError):
        plan_download_target(final_path)


def test_import_settings_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.hf_import.get_state_dir", lambda: state_dir)
    monkeypatch.setattr("llama_orchestrator.hf_import.get_models_dir", lambda: models_dir)

    save_import_settings(ImportSettings(local_models_directory=str(tmp_path / "custom-models")))
    loaded = load_import_settings()

    assert loaded.local_models_directory == str(tmp_path / "custom-models")


def test_resolve_local_variant_path_uses_repo_scoped_directory(tmp_path: Path) -> None:
    target = resolve_local_variant_path(
        "Qwen/Qwen3-8B-GGUF",
        "subdir/Qwen3-8B-Q4_K_M.gguf",
        tmp_path,
    )

    assert target == tmp_path / "Qwen__Qwen3-8B-GGUF" / "subdir" / "Qwen3-8B-Q4_K_M.gguf"


@pytest.mark.parametrize("filename", ["../escape.gguf", "/absolute.gguf", "C:/escape.gguf"])
def test_resolve_local_variant_path_rejects_path_traversal(filename: str, tmp_path: Path) -> None:
    with pytest.raises(HuggingFaceImportError):
        resolve_local_variant_path("Qwen/Qwen3-8B-GGUF", filename, tmp_path)


def test_model_name_and_tags_include_repo_quant_and_size() -> None:
    name = suggest_model_name("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Q4_K_M")
    tags = build_model_tags("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Q4_K_M")

    assert name == "Qwen3 8B GGUF Q4_K_M"
    assert tags == ["hf", "hf_repo__qwen__qwen3-8b-gguf", "gguf", "q4_k_m", "8b"]
    assert infer_model_size_tag("Qwen/Qwen3-8B-GGUF") == "8b"


def test_build_add_model_prefill_returns_name_absolute_path_and_tags(tmp_path: Path) -> None:
    selection = ImportedModelSelection(
        repo_id="Qwen/Qwen3-8B-GGUF",
        filename="Qwen3-8B-Q4_K_M.gguf",
        local_path=tmp_path / "Qwen3-8B-Q4_K_M.gguf",
        quantization="Q4_K_M",
        size_bytes=10,
    )

    name, model_path, tags = build_add_model_prefill(selection)

    assert name == "Qwen3 8B GGUF Q4_K_M"
    assert model_path == str(selection.local_path.resolve())
    assert tags == ["hf", "hf_repo__qwen__qwen3-8b-gguf", "gguf", "q4_k_m", "8b"]


def test_write_import_metadata_sidecar_validates_gguf_and_model_card(tmp_path: Path) -> None:
    model_path = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    _write_test_gguf(
        model_path,
        {
            "general.architecture": "qwen3",
            "general.name": "Qwen3 8B",
            "general.basename": "Qwen3",
            "general.file_type": 15,
            "general.quantization_version": 2,
            "tokenizer.chat_template": "{{ messages }}",
            "qwen3.context_length": 262144,
            "qwen3.embedding_length": 4096,
            "qwen3.block_count": 36,
            "qwen3.attention.head_count": 32,
            "qwen3.attention.head_count_kv": 8,
            "qwen3.rope.freq_base": 1000000,
            "qwen3.nextn_predict_layers": 1,
        },
    )
    selection = ImportedModelSelection(
        repo_id="Qwen/Qwen3-8B-GGUF",
        filename=model_path.name,
        local_path=model_path,
        quantization="Q4_K_M",
        size_bytes=model_path.stat().st_size,
    )

    enriched = write_import_metadata_sidecar(
        selection,
        model_card_text="# Qwen3 8B\n\nContext length: 262144. MTP supported. Use llama.cpp.",
    )

    assert enriched.validation_status == "ok"
    assert enriched.validation_confidence == "high"
    assert enriched.metadata_sidecar_path is not None
    payload = json.loads(enriched.metadata_sidecar_path.read_text(encoding="utf-8"))
    assert payload["source"]["provider"] == "huggingface"
    assert payload["gguf_metadata"]["architecture"] == "qwen3"
    assert payload["gguf_metadata"]["context_length"] == 262144
    assert payload["gguf_metadata"]["file_type"] == "Q4_K_M"
    assert payload["gguf_metadata"]["nextn_predict_layers"] == 1
    assert payload["model_card_metadata"]["claimed_mtp_support"] is True
    assert payload["validation"]["warnings"] == []


def test_write_import_metadata_sidecar_warns_when_gguf_metadata_is_missing(tmp_path: Path) -> None:
    model_path = tmp_path / "broken-Q4_K_M.gguf"
    model_path.write_bytes(b"not-a-gguf")
    selection = ImportedModelSelection(
        repo_id="Author/Broken-GGUF",
        filename=model_path.name,
        local_path=model_path,
        quantization="Q4_K_M",
        size_bytes=model_path.stat().st_size,
    )

    enriched = write_import_metadata_sidecar(selection, model_card_text="")

    assert enriched.validation_status == "warning"
    assert enriched.validation_confidence == "low"
    assert enriched.validation_warnings == ("GGUF metadata could not be read from the local artifact.",)
    payload = json.loads(enriched.metadata_sidecar_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
    assert payload["gguf_metadata"] == {}
