"""Estimated GPU memory fit helpers for llama.cpp instance configs."""

from __future__ import annotations

import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from llama_orchestrator.engine.command import build_command, resolve_model_path
from llama_orchestrator.engine.detection import describe_effective_runtime, get_backend_device_label
from llama_orchestrator.hf_import import parse_gguf_quantization

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig


BYTES_PER_MB = 1024 * 1024
GGUF_MAGIC = b"GGUF"

_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_INT32 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
_GGUF_TYPE_UINT64 = 10
_GGUF_TYPE_INT64 = 11
_GGUF_TYPE_FLOAT64 = 12

_SUPPORTED_FIT_CLASSIFICATIONS = {
    "fits_dedicated_vram",
    "borderline",
    "likely_shared_ram",
    "cpu_only",
    "unknown",
}

_SUPPORTED_CONFIDENCE = {"high", "medium", "low"}

_QUANT_BITS_RE = re.compile(r"(?:^|[^a-z])(iq|q|tq)(\d)", re.IGNORECASE)


@dataclass(frozen=True)
class GgufModelMetadata:
    """Minimal GGUF metadata needed for memory estimation."""

    architecture: str | None = None
    context_length: int | None = None
    block_count: int | None = None
    embedding_length: int | None = None
    attention_head_count: int | None = None
    attention_head_count_kv: int | None = None
    attention_key_length: int | None = None
    attention_value_length: int | None = None
    file_type: int | None = None


@dataclass(frozen=True)
class MemoryFitEstimate:
    """Estimated fit result for one instance configuration."""

    classification: str
    confidence: str
    basis: str = "estimated"
    estimated_model_resident_mb: float | None = None
    estimated_kv_cache_mb: float | None = None
    estimated_runtime_overhead_mb: float | None = None
    estimated_total_required_mb: float | None = None
    budget_mb_used: float | None = None
    estimated_shared_ram_mb_lower_bound: float | None = None
    model_size_bytes: int | None = None
    context_size: int | None = None
    parallel_slots: int | None = None
    batch_size: int | None = None
    ubatch_size: int | None = None
    cache_type_k: str | None = None
    cache_type_v: str | None = None
    quantization: str | None = None
    gpu_layers: int | None = None
    split_mode: str | None = None
    architecture: str | None = None
    kv_offload_enabled: bool = True
    reasons: tuple[str, ...] = field(default_factory=tuple)
    unsupported_inputs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the estimate for JSON output."""
        return asdict(self)


def load_gguf_metadata(model_path: Path) -> GgufModelMetadata | None:
    """Read the minimal metadata surface from a GGUF file."""
    try:
        with model_path.open("rb") as handle:
            if handle.read(4) != GGUF_MAGIC:
                return None
            version = _read_u32(handle)
            if version not in {2, 3}:
                return None
            _ = _read_u64(handle)
            metadata_kv_count = _read_u64(handle)
            values: dict[str, Any] = {}
            for _ in range(metadata_kv_count):
                key = _read_string(handle)
                value_type = _read_u32(handle)
                values[key] = _read_metadata_value(handle, value_type)
    except (OSError, struct.error, UnicodeDecodeError, ValueError):
        return None

    architecture = _as_string(values.get("general.architecture"))
    prefix = architecture or None

    def prefixed(suffix: str) -> Any:
        if prefix is None:
            return None
        return values.get(f"{prefix}.{suffix}")

    return GgufModelMetadata(
        architecture=architecture,
        context_length=_as_int(prefixed("context_length")),
        block_count=_as_int(prefixed("block_count")),
        embedding_length=_as_int(prefixed("embedding_length")),
        attention_head_count=_as_int(prefixed("attention.head_count")),
        attention_head_count_kv=_as_int(prefixed("attention.head_count_kv")),
        attention_key_length=_as_int(prefixed("attention.key_length")),
        attention_value_length=_as_int(prefixed("attention.value_length")),
        file_type=_as_int(values.get("general.file_type")),
    )


def parse_dedicated_vram_budget_from_log(
    stderr_log: Path,
    *,
    backend: str,
    device_id: int,
) -> float | None:
    """Return total dedicated VRAM from prior llama.cpp device inventory lines."""
    if not stderr_log.exists():
        return None

    device_label = get_backend_device_label(backend, device_id)
    if device_label is None:
        return None

    inventory_pattern = re.compile(
        rf"{re.escape(device_label)}\s*:.*\((\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB),\s*(\d+(?:\.\d+)?)\s*(MiB|MB|GiB|GB)\s+free\)",
        re.IGNORECASE,
    )
    try:
        for line in stderr_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = inventory_pattern.search(line)
            if match:
                return _normalize_mb(float(match.group(1)), match.group(2))
    except OSError:
        return None
    return None


def estimate_instance_memory(
    config: InstanceConfig,
    *,
    effective_command: list[str] | None = None,
    model_size_bytes: int | None = None,
    dedicated_vram_budget_mb: float | None = None,
    stderr_log: Path | None = None,
) -> MemoryFitEstimate:
    """Estimate model residency, KV cache, and fit against a dedicated VRAM budget."""
    command = effective_command or build_command(config)
    parsed = _parse_runtime_arguments(command)
    selection = describe_effective_runtime(config)

    backend = selection.backend
    gpu_layers = parsed["gpu_layers"] if parsed["gpu_layers"] is not None else selection.gpu_layers
    split_mode = parsed["split_mode"] or "none"
    cache_type_k = parsed["cache_type_k"] or "f16"
    cache_type_v = parsed["cache_type_v"] or "f16"
    context_size = parsed["context_size"] or config.model.context_size
    parallel_slots = parsed["parallel"] or config.server.parallel or 1
    batch_size = parsed["batch_size"] or config.model.batch_size
    ubatch_size = parsed["ubatch_size"]
    primary_device_id = parsed["main_gpu"]
    if primary_device_id is None:
        primary_device_id = parsed["device_id"]
    quantization = parse_gguf_quantization(str(config.model.path))
    kv_offload_enabled = not parsed["no_kv_offload"]

    reasons: list[str] = []
    unsupported_inputs: list[str] = []

    try:
        resolved_model_path = resolve_model_path(config)
    except OSError:
        resolved_model_path = None

    if model_size_bytes is None and resolved_model_path is not None:
        try:
            model_size_bytes = resolved_model_path.stat().st_size
        except OSError:
            model_size_bytes = None

    metadata = load_gguf_metadata(resolved_model_path) if resolved_model_path is not None else None

    if backend == "cpu" or not selection.gpu_active or gpu_layers <= 0:
        reasons.append("GPU backend is inactive or GPU layers are disabled, so the instance is CPU-only.")
        return MemoryFitEstimate(
            classification="cpu_only",
            confidence="high",
            model_size_bytes=model_size_bytes,
            context_size=context_size,
            parallel_slots=parallel_slots,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
            quantization=quantization,
            gpu_layers=gpu_layers,
            split_mode=split_mode,
            architecture=metadata.architecture if metadata else None,
            kv_offload_enabled=kv_offload_enabled,
            reasons=tuple(reasons),
        )

    if parsed["multi_gpu"]:
        unsupported_inputs.append("multi_gpu_device_list")
    if parallel_slots > 1:
        unsupported_inputs.append("parallel_slots")
    if split_mode != "none":
        unsupported_inputs.append(f"split_mode:{split_mode}")
    if parsed["tensor_split"]:
        unsupported_inputs.append("tensor_split")
    if parsed["draft_model"] or parsed["speculative"]:
        unsupported_inputs.append("speculative_draft_runtime")
    if parsed["n_cpu_moe"] is not None:
        unsupported_inputs.append("n_cpu_moe")

    budget_mb = dedicated_vram_budget_mb
    if budget_mb is None and stderr_log is not None:
        if primary_device_id is None:
            primary_device_id = (
                selection.primary_device_id if selection.primary_device_id is not None else int(config.gpu.device_id)
            )
        budget_mb = parse_dedicated_vram_budget_from_log(
            stderr_log,
            backend=backend,
            device_id=primary_device_id,
        )
        if budget_mb is not None:
            reasons.append("Dedicated VRAM budget was inferred from prior llama.cpp device inventory log lines.")

    if model_size_bytes is None:
        reasons.append("Model file size is unavailable, so resident model memory cannot be estimated.")
    if parallel_slots > 1:
        reasons.append("Parallel server slots are not fully modeled, so fit classification is conservative.")

    estimated_model_resident_mb: float | None = None
    if model_size_bytes is not None:
        model_size_mb = model_size_bytes / BYTES_PER_MB
        if metadata and metadata.block_count and gpu_layers > 0:
            offload_ratio = min(1.0, gpu_layers / max(metadata.block_count, 1))
            estimated_model_resident_mb = round(model_size_mb * offload_ratio, 3)
            reasons.append(
                f"Model residency uses GGUF block_count={metadata.block_count} with gpu_layers={gpu_layers}."
            )
        elif gpu_layers >= 999:
            estimated_model_resident_mb = round(model_size_mb, 3)
            reasons.append("GPU layers request full offload, so model residency uses the full GGUF size.")
        else:
            estimated_model_resident_mb = round(model_size_mb, 3)
            unsupported_inputs.append("partial_gpu_offload_without_block_count")
            reasons.append("GGUF block_count is unavailable; partial offload is conservatively treated as near-full residency.")

    estimated_kv_cache_mb: float | None = None
    if kv_offload_enabled:
        estimated_kv_cache_mb = _estimate_kv_cache_mb(
            metadata,
            context_size=context_size,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
        )
        if estimated_kv_cache_mb is not None:
            reasons.append("KV cache estimate is derived from GGUF attention metadata and effective ctx-size.")
        else:
            reasons.append("KV cache metadata is incomplete, so KV cache size stays unknown.")
    else:
        reasons.append("KV offload is disabled, so KV cache is treated as host RAM instead of VRAM.")

    estimated_runtime_overhead_mb: float | None = None
    if estimated_model_resident_mb is not None:
        batch_workspace_mb = max(batch_size, ubatch_size or 0) * 0.5
        estimated_runtime_overhead_mb = round(
            max(256.0, estimated_model_resident_mb * 0.03, batch_workspace_mb),
            3,
        )
        reasons.append(
            "Runtime overhead adds a heuristic margin for graph, scratch, allocator buffers, and configured batch size."
        )

    estimated_total_required_mb = _sum_known_mb(
        estimated_model_resident_mb,
        estimated_kv_cache_mb,
        estimated_runtime_overhead_mb,
    )

    classification = "unknown"
    shared_ram_lower_bound: float | None = None
    confidence = "low"

    if budget_mb is None:
        reasons.append("Dedicated VRAM budget is unavailable, so fit classification remains unknown.")
    elif estimated_total_required_mb is None:
        reasons.append("Required GPU memory is incomplete, so fit classification remains unknown.")
    else:
        margin_mb = budget_mb - estimated_total_required_mb
        shared_ram_lower_bound = round(max(-margin_mb, 0.0), 3)
        headroom_threshold_mb = max(512.0, budget_mb * 0.05)
        if margin_mb >= headroom_threshold_mb:
            classification = "fits_dedicated_vram"
        elif margin_mb >= 0:
            classification = "borderline"
        else:
            classification = "likely_shared_ram"
        reasons.append(
            f"Fit classification compares estimated required memory against a dedicated VRAM budget of {budget_mb:.0f} MiB."
        )
        if parallel_slots > 1 and classification in {"fits_dedicated_vram", "borderline"}:
            classification = "unknown"
            reasons.append("Parallel slot allocation semantics are ambiguous, so fit classification remains unknown.")

    if classification == "cpu_only":
        confidence = "high"
    elif budget_mb is None or estimated_total_required_mb is None or unsupported_inputs:
        confidence = "low"
    elif metadata and metadata.block_count and metadata.embedding_length and metadata.attention_head_count:
        confidence = "high"
    else:
        confidence = "medium"

    return MemoryFitEstimate(
        classification=classification,
        confidence=confidence,
        estimated_model_resident_mb=estimated_model_resident_mb,
        estimated_kv_cache_mb=estimated_kv_cache_mb,
        estimated_runtime_overhead_mb=estimated_runtime_overhead_mb,
        estimated_total_required_mb=estimated_total_required_mb,
        budget_mb_used=round(budget_mb, 3) if budget_mb is not None else None,
        estimated_shared_ram_mb_lower_bound=shared_ram_lower_bound,
        model_size_bytes=model_size_bytes,
        context_size=context_size,
        parallel_slots=parallel_slots,
        batch_size=batch_size,
        ubatch_size=ubatch_size,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        quantization=quantization,
        gpu_layers=gpu_layers,
        split_mode=split_mode,
        architecture=metadata.architecture if metadata else None,
        kv_offload_enabled=kv_offload_enabled,
        reasons=tuple(dict.fromkeys(reasons)),
        unsupported_inputs=tuple(dict.fromkeys(unsupported_inputs)),
    )


def _read_metadata_value(handle: BinaryIO, value_type: int) -> Any:
    if value_type == _GGUF_TYPE_UINT8:
        return _unpack(handle, "<B")
    if value_type == _GGUF_TYPE_INT8:
        return _unpack(handle, "<b")
    if value_type == _GGUF_TYPE_UINT16:
        return _unpack(handle, "<H")
    if value_type == _GGUF_TYPE_INT16:
        return _unpack(handle, "<h")
    if value_type == _GGUF_TYPE_UINT32:
        return _unpack(handle, "<I")
    if value_type == _GGUF_TYPE_INT32:
        return _unpack(handle, "<i")
    if value_type == _GGUF_TYPE_FLOAT32:
        return _unpack(handle, "<f")
    if value_type == _GGUF_TYPE_BOOL:
        return bool(_unpack(handle, "<?"))
    if value_type == _GGUF_TYPE_STRING:
        return _read_string(handle)
    if value_type == _GGUF_TYPE_ARRAY:
        nested_type = _read_u32(handle)
        length = _read_u64(handle)
        return [_read_metadata_value(handle, nested_type) for _ in range(length)]
    if value_type == _GGUF_TYPE_UINT64:
        return _unpack(handle, "<Q")
    if value_type == _GGUF_TYPE_INT64:
        return _unpack(handle, "<q")
    if value_type == _GGUF_TYPE_FLOAT64:
        return _unpack(handle, "<d")
    raise ValueError(f"Unsupported GGUF metadata value type: {value_type}")


def _read_u32(handle: BinaryIO) -> int:
    return _unpack(handle, "<I")


def _read_u64(handle: BinaryIO) -> int:
    return _unpack(handle, "<Q")


def _read_string(handle: BinaryIO) -> str:
    length = _read_u64(handle)
    return handle.read(length).decode("utf-8")


def _unpack(handle: BinaryIO, fmt: str) -> Any:
    size = struct.calcsize(fmt)
    data = handle.read(size)
    if len(data) != size:
        raise struct.error("Unexpected end of GGUF file")
    return struct.unpack(fmt, data)[0]


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    return None


def _as_string(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _normalize_mb(value: float, unit: str | None) -> float:
    if unit and unit.lower() in {"gib", "gb"}:
        return round(value * 1024, 3)
    return round(value, 3)


def _parse_runtime_arguments(command: list[str]) -> dict[str, Any]:
    tokens = command[1:]
    parsed: dict[str, Any] = {
        "context_size": None,
        "parallel": None,
        "batch_size": None,
        "ubatch_size": None,
        "device_id": None,
        "main_gpu": None,
        "gpu_layers": None,
        "cache_type_k": None,
        "cache_type_v": None,
        "split_mode": None,
        "tensor_split": None,
        "no_kv_offload": False,
        "multi_gpu": False,
        "draft_model": None,
        "speculative": False,
        "n_cpu_moe": None,
    }

    index = 0
    while index < len(tokens):
        token = tokens[index]
        inline_value: str | None = None
        step = 1
        if token.startswith("--") and "=" in token:
            token, inline_value = token.split("=", 1)
        else:
            inline_value = tokens[index + 1] if index + 1 < len(tokens) else None
            step = 2
        next_value = inline_value
        if token == "--ctx-size" and next_value is not None:
            parsed["context_size"] = _safe_int(next_value)
            index += step
            continue
        if token in {"--batch-size", "-b"} and next_value is not None:
            parsed["batch_size"] = _safe_int(next_value)
            index += step
            continue
        if token in {"--ubatch-size", "-ub"} and next_value is not None:
            parsed["ubatch_size"] = _safe_int(next_value)
            index += step
            continue
        if token == "--parallel" and next_value is not None:
            parsed["parallel"] = _safe_int(next_value)
            index += step
            continue
        if token == "--main-gpu" and next_value is not None:
            parsed["main_gpu"] = _safe_int(next_value)
            index += step
            continue
        if token in {"--n-gpu-layers", "-ngl"} and next_value is not None:
            parsed["gpu_layers"] = _safe_int(next_value)
            index += step
            continue
        if token == "--cache-type-k" and next_value is not None:
            parsed["cache_type_k"] = next_value.lower()
            index += step
            continue
        if token == "--cache-type-v" and next_value is not None:
            parsed["cache_type_v"] = next_value.lower()
            index += step
            continue
        if token == "--split-mode" and next_value is not None:
            parsed["split_mode"] = next_value.lower()
            index += step
            continue
        if token == "--tensor-split" and next_value is not None:
            parsed["tensor_split"] = next_value
            index += step
            continue
        if token == "--device" and next_value is not None:
            parsed["device_id"] = _parse_primary_device_id(next_value)
            parsed["multi_gpu"] = len([part for part in re.split(r"[,;\s]+", next_value) if part]) > 1
            index += step
            continue
        if token in {"--no-kv-offload", "-nkvo"}:
            parsed["no_kv_offload"] = True
            index += 1
            continue
        if token in {"--model-draft", "-md"} and next_value is not None:
            parsed["draft_model"] = next_value
            index += step
            continue
        if token == "--spec-type" and next_value is not None:
            parsed["speculative"] = next_value.lower() != "none"
            index += step
            continue
        if token in {"--n-cpu-moe", "-ncmoe"} and next_value is not None:
            parsed["n_cpu_moe"] = _safe_int(next_value)
            index += step
            continue
        index += 1

    return parsed


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_primary_device_id(raw_value: str) -> int | None:
    for token in re.split(r"[,;\s]+", raw_value.strip()):
        if not token:
            continue
        if (device_id := _safe_int(token)) is not None:
            return device_id
        match = re.search(r"(\d+)$", token)
        if match:
            return int(match.group(1))
    return None


def _estimate_kv_cache_mb(
    metadata: GgufModelMetadata | None,
    *,
    context_size: int,
    cache_type_k: str,
    cache_type_v: str,
) -> float | None:
    if metadata is None:
        return None
    if not metadata.block_count or not metadata.embedding_length or not metadata.attention_head_count:
        return None

    head_count = metadata.attention_head_count
    kv_head_count = metadata.attention_head_count_kv or head_count
    if head_count <= 0 or kv_head_count <= 0:
        return None

    key_length = metadata.attention_key_length
    value_length = metadata.attention_value_length
    if key_length is None:
        if metadata.embedding_length % head_count != 0:
            return None
        key_length = metadata.embedding_length // head_count
    if value_length is None:
        if metadata.embedding_length % head_count != 0:
            return None
        value_length = metadata.embedding_length // head_count

    bytes_k = _bytes_per_cache_element(cache_type_k)
    bytes_v = _bytes_per_cache_element(cache_type_v)
    kv_bytes_per_token = metadata.block_count * kv_head_count * ((key_length * bytes_k) + (value_length * bytes_v))
    return round((kv_bytes_per_token * context_size) / BYTES_PER_MB, 3)


def _bytes_per_cache_element(cache_type: str) -> float:
    normalized = cache_type.lower()
    if normalized == "f32":
        return 4.0
    if normalized in {"f16", "bf16"}:
        return 2.0
    if normalized in {"q8_0", "q8_1"}:
        return 1.0

    match = _QUANT_BITS_RE.search(normalized)
    if match:
        bits = int(match.group(2))
        return bits / 8.0
    if normalized in {"iq4_nl", "mxfp4"}:
        return 0.5
    return 2.0


def _sum_known_mb(*values: float | None) -> float | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return round(sum(known), 3)
