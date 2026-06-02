"""Helpers for additive model artifact metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llama_orchestrator.engine.command import resolve_model_path
from llama_orchestrator.hf_import import ImportedModelSelection, parse_gguf_quantization
from llama_orchestrator.memory_fit import GgufModelMetadata, load_gguf_metadata

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig
    from llama_orchestrator.config.schema import ModelMetadataUserMetadata

from llama_orchestrator.config.schema import (
    ModelMetadata,
    ModelMetadataArtifact,
    ModelMetadataCapabilities,
    ModelMetadataContext,
    ModelMetadataDerived,
    ModelMetadataGgufExtracted,
    ModelMetadataIdentity,
    ModelMetadataLicense,
    ModelMetadataQuantization,
    ModelMetadataSource,
    ModelMetadataSpeculativeDecoding,
    ModelMetadataUserMetadata,
)

_KV_FORMULA_VERSION = "kv_cache_v1_gqa"
_KV_SCENARIOS: tuple[int, ...] = (8_192, 32_768, 65_536, 131_072, 262_144)
_KV_BYTES_PER_ELEMENT: dict[str, float] = {
    "f16": 2.0,
    "q8_0": 1.0,
    "q4_0": 0.5,
    "q3_turbo": 0.375,
    "q2_turbo": 0.25,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _extract_hf_repo_id(tags: list[str]) -> str | None:
    for tag in tags:
        normalized = tag.strip()
        if normalized.lower().startswith("hf:") and len(normalized) > 3:
            return normalized[3:]
        if normalized.lower().startswith("hf_repo__"):
            parts = normalized.split("__", 2)
            if len(parts) == 3 and parts[1] and parts[2]:
                return f"{parts[1]}/{parts[2]}"
    return None


def _split_repo_id(repo_id: str | None) -> tuple[str, str]:
    if not repo_id or "/" not in repo_id:
        return "", ""
    publisher, repo = repo_id.split("/", 1)
    return publisher.strip(), repo.strip()


def _guess_family_from_repo(repo_name: str, filename: str) -> str:
    candidate = repo_name or filename
    return candidate.replace("_", "-").split("-")[0].strip().lower()


def _compute_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _build_derived_memory_model(metadata: GgufModelMetadata | None) -> dict[str, Any]:
    if metadata is None:
        return {
            "status": "missing_gguf_metadata",
            "supported_cache_types": list(_KV_BYTES_PER_ELEMENT),
        }

    layers = metadata.block_count
    kv_heads = metadata.attention_head_count_kv or metadata.attention_head_count
    head_count = metadata.attention_head_count
    head_dim_k = metadata.attention_key_length
    head_dim_v = metadata.attention_value_length

    if head_dim_k is None and metadata.embedding_length and head_count:
        if head_count > 0 and metadata.embedding_length % head_count == 0:
            head_dim_k = metadata.embedding_length // head_count
    if head_dim_v is None and metadata.embedding_length and head_count:
        if head_count > 0 and metadata.embedding_length % head_count == 0:
            head_dim_v = metadata.embedding_length // head_count

    if not layers or not kv_heads or not head_dim_k or not head_dim_v:
        return {
            "status": "insufficient_gguf_metadata",
            "inputs": {
                "layers": layers,
                "kv_heads": kv_heads,
                "head_dim_k": head_dim_k,
                "head_dim_v": head_dim_v,
            },
            "supported_cache_types": list(_KV_BYTES_PER_ELEMENT),
        }

    scenarios: dict[str, Any] = {}
    for cache_type, bytes_per_element in _KV_BYTES_PER_ELEMENT.items():
        kv_bytes_per_token = int(
            round(2 * layers * kv_heads * (head_dim_k + head_dim_v) * bytes_per_element)
        )
        by_context: dict[str, Any] = {}
        for context_length in _KV_SCENARIOS:
            total_bytes = kv_bytes_per_token * context_length
            by_context[f"{context_length // 1024}k"] = {
                "context_length": context_length,
                "bytes": total_bytes,
                "mb": round(total_bytes / (1024 * 1024), 3),
                "gb": round(total_bytes / (1024 * 1024 * 1024), 3),
            }
        scenarios[cache_type] = {
            "kv_bytes_per_token": kv_bytes_per_token,
            "contexts": by_context,
        }

    return {
        "status": "ok",
        "inputs": {
            "layers": layers,
            "kv_heads": kv_heads,
            "head_dim_k": head_dim_k,
            "head_dim_v": head_dim_v,
        },
        "supported_cache_types": list(_KV_BYTES_PER_ELEMENT),
        "scenarios": scenarios,
    }


def _resolve_hf_source_fields(
    config: InstanceConfig,
    imported_selection: ImportedModelSelection | None,
) -> tuple[str, str, str, int | None]:
    if imported_selection is not None:
        return (
            "huggingface",
            imported_selection.repo_id,
            imported_selection.filename,
            imported_selection.size_bytes,
        )

    repo_id = _extract_hf_repo_id(config.tags)
    if repo_id:
        return (
            "huggingface",
            repo_id,
            Path(config.model.path).name,
            None,
        )

    return (
        "local",
        "",
        Path(config.model.path).name,
        None,
    )


def _build_hf_url(repo_id: str, filename: str) -> str:
    if not repo_id:
        return ""
    if filename:
        return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    return f"https://huggingface.co/{repo_id}"


def _build_user_metadata(existing: ModelMetadataUserMetadata | None) -> ModelMetadataUserMetadata:
    if existing is None:
        return ModelMetadataUserMetadata()
    return ModelMetadataUserMetadata(
        notes=existing.notes,
        rating=existing.rating,
        favorite=existing.favorite,
        tags=list(existing.tags),
    )


def _extract_hf_license(repo_id: str, token: str | None) -> tuple[str, str]:
    if not repo_id:
        return "", ""
    try:
        from huggingface_hub import HfApi

        info = HfApi(token=token).model_info(repo_id=repo_id, token=token)
    except Exception:
        return "", ""

    card_data = getattr(info, "cardData", None) or {}
    if isinstance(card_data, dict):
        license_value = card_data.get("license")
        if isinstance(license_value, str):
            return license_value.strip(), "huggingface_model_card"
    return "", ""


def build_model_metadata(
    config: InstanceConfig,
    *,
    imported_selection: ImportedModelSelection | None = None,
    include_sha256: bool = False,
    fetch_hf_license: bool = False,
    hf_token: str | None = None,
) -> ModelMetadata:
    """Build additive artifact metadata without mutating runtime configuration."""

    try:
        resolved_model_path = resolve_model_path(config)
    except OSError:
        resolved_model_path = Path(config.model.path)

    local_size_bytes = None
    if resolved_model_path.exists() and resolved_model_path.is_file():
        try:
            local_size_bytes = resolved_model_path.stat().st_size
        except OSError:
            local_size_bytes = None

    gguf = load_gguf_metadata(resolved_model_path) if resolved_model_path.exists() else None
    hub, repo_id, filename, remote_size_bytes = _resolve_hf_source_fields(config, imported_selection)
    publisher, repo_name = _split_repo_id(repo_id)

    quant_name = ""
    if imported_selection is not None and imported_selection.quantization:
        quant_name = imported_selection.quantization
    elif (parsed_quant := parse_gguf_quantization(filename)) is not None:
        quant_name = parsed_quant

    sha256_actual = _compute_sha256(resolved_model_path) if include_sha256 else None
    verification_status = None
    if include_sha256:
        verification_status = "verified_sha256" if sha256_actual else "missing_local_file"

    license_id = ""
    license_source = ""
    if fetch_hf_license and repo_id:
        license_id, license_source = _extract_hf_license(repo_id, hf_token)

    mtp_available = "mtp" in filename.lower() or any("mtp" in tag.lower() for tag in config.tags)
    user_metadata = _build_user_metadata(config.model_metadata.user_metadata if config.model_metadata else None)

    return ModelMetadata(
        identity=ModelMetadataIdentity(
            display_name=config.display_name or config.name,
            family=_guess_family_from_repo(repo_name, filename),
            publisher=publisher,
            architecture=gguf.architecture if gguf and gguf.architecture else "",
            parameter_count_total=None,
            parameter_count_active=None,
        ),
        source=ModelMetadataSource(
            hub=hub,
            repo_id=repo_id,
            revision="",
            commit_hash="",
            filename=filename,
            url=_build_hf_url(repo_id, filename),
            fetched_at=_utc_now_iso() if hub == "huggingface" else "",
        ),
        artifact=ModelMetadataArtifact(
            format="gguf",
            remote_size_bytes=remote_size_bytes,
            local_size_bytes=local_size_bytes,
            etag=None,
            sha256_expected=None,
            sha256_actual=sha256_actual,
            verification_status=verification_status,
        ),
        quantization=ModelMetadataQuantization(
            name=quant_name,
            family=quant_name.split("_", 1)[0].lower() if quant_name else "",
            variant=quant_name,
            nominal_bits=int(quant_name[1]) if quant_name.upper().startswith("Q") and len(quant_name) > 1 and quant_name[1].isdigit() else None,
            imatrix=bool("imatrix" in filename.lower()),
        ),
        license=ModelMetadataLicense(
            id=license_id,
            source=license_source,
            verified_at=_utc_now_iso() if license_id else "",
        ),
        context=ModelMetadataContext(
            native_context_length=gguf.context_length if gguf else None,
            recommended_min_context_length=min(config.model.context_size, gguf.context_length) if gguf and gguf.context_length else config.model.context_size,
            source="gguf" if gguf and gguf.context_length else "config",
        ),
        capabilities=ModelMetadataCapabilities(
            text_generation=True,
            reasoning=True if any("reason" in tag.lower() for tag in config.tags) else None,
            tool_use=None,
            multilingual=None,
            vision_declared=True if any("vision" in tag.lower() for tag in config.tags) else None,
            vision_requires_mmproj=None,
        ),
        speculative_decoding=ModelMetadataSpeculativeDecoding(
            builtin_mtp={
                "available": mtp_available,
                "compatible": mtp_available,
                "runtime_requirements": ["llama.cpp_mtp_support"] if mtp_available else [],
                "experimental": None,
            },
            external_draft={
                "available": False,
                "compatible": None,
                "runtime_requirements": [],
                "experimental": True,
            },
            dflash={
                "available": False,
                "compatible": None,
                "runtime_requirements": [],
                "experimental": True,
            },
        ),
        gguf_extracted=ModelMetadataGgufExtracted(
            n_layers=gguf.block_count if gguf else None,
            n_embd=gguf.embedding_length if gguf else None,
            n_attention_heads=gguf.attention_head_count if gguf else None,
            n_kv_heads=gguf.attention_head_count_kv if gguf else None,
            head_dim_k=gguf.attention_key_length if gguf else None,
            head_dim_v=gguf.attention_value_length if gguf else None,
            rope_scaling=gguf.rope_scaling if gguf else None,
            tokenizer_model=gguf.tokenizer_model if gguf else None,
            chat_template=gguf.chat_template if gguf else None,
            n_experts=gguf.expert_count if gguf else None,
            n_experts_used=gguf.expert_used_count if gguf else None,
        ),
        derived=ModelMetadataDerived(
            formula_version=_KV_FORMULA_VERSION,
            memory_model=_build_derived_memory_model(gguf),
        ),
        user_metadata=user_metadata,
    )
