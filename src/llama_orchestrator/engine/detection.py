"""Read-only helpers for effective runtime CPU/GPU display metadata."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass

from .command import build_command, build_env, resolve_model_path
from .process import get_log_files

if False:  # pragma: no cover
    from llama_orchestrator.config import InstanceConfig

BYTES_PER_GB = 1024 ** 3
_BACKEND_PREFIXES = {
    "vulkan": "Vulkan",
    "cuda": "CUDA",
    "hip": "HIP",
    "metal": "Metal",
}
_DEVICE_ENV_VARS = {
    "vulkan": ("GGML_VULKAN_DEVICE",),
}
_INVENTORY_PATTERN = re.compile(
    r"\b(?P<label>(?:Vulkan|CUDA|HIP|Metal)\d+)\s*:\s*(?P<name>.+?)\s*\((?:\d+(?:\.\d+)?)\s*(?:MiB|MB|GiB|GB)",
    re.IGNORECASE,
)
_ACTIVE_DEVICE_PATTERN = re.compile(
    r"using device\s+(?P<label>(?:Vulkan|CUDA|HIP|Metal)\d+)\s*\((?P<name>.+?)\)",
    re.IGNORECASE,
)
_LABEL_PATTERN = re.compile(r"^(?P<prefix>[A-Za-z]+)(?P<index>\d+)$")
_VULKANINFO_GPU_PATTERN = re.compile(r"^GPU(?P<index>\d+):\s*$", re.IGNORECASE)
_VULKANINFO_DEVICE_NAME_PATTERN = re.compile(r"^\s*deviceName\s*=\s*(?P<name>.+?)\s*$")
_VULKANINFO_CACHE_TTL_SECONDS = 30.0
_vulkaninfo_cache: tuple[float, tuple[DetectedGpu, ...]] | None = None


@dataclass(frozen=True)
class EffectiveRuntimeSelection:
    """Display metadata derived from the final command line and env vars."""

    backend: str
    threads: int | None
    gpu_layers: int
    cpu_active: bool
    gpu_active: bool
    gpu_labels: tuple[str, ...]
    primary_gpu_label: str | None
    primary_device_id: int | None

    @property
    def gpu_display(self) -> str:
        """Return a compact GPU label list for the GUI table."""
        if not self.gpu_active or not self.gpu_labels:
            return "-"
        return ", ".join(self.gpu_labels)


@dataclass(frozen=True)
class DetectedGpu:
    """Known GPU label and best-effort adapter name."""

    label: str
    name: str | None = None


def get_backend_device_label(backend: str, device_id: int) -> str | None:
    """Return the llama.cpp log label for a backend/device pair."""
    prefix = _BACKEND_PREFIXES.get(backend.lower())
    if prefix is None:
        return None
    return f"{prefix}{device_id}"


def _parse_last_flag_int(args: list[str], *flags: str) -> int | None:
    value: int | None = None
    for index, arg in enumerate(args[:-1]):
        if arg not in flags:
            continue
        try:
            value = int(args[index + 1])
        except ValueError:
            continue
    return value


def _parse_last_flag_value(args: list[str], *flags: str) -> str | None:
    value: str | None = None
    for index, arg in enumerate(args[:-1]):
        if arg in flags:
            value = args[index + 1]
    return value


def _parse_device_ids(raw_value: str | None) -> tuple[int, ...]:
    if not raw_value:
        return ()

    labels: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[,;\s]+", raw_value.strip()):
        if not token:
            continue
        try:
            device_id = int(token)
        except ValueError:
            continue
        if device_id in seen:
            continue
        labels.append(device_id)
        seen.add(device_id)
    return tuple(labels)


def _parse_device_labels(raw_value: str | None, backend: str) -> tuple[str, ...]:
    if not raw_value:
        return ()

    expected_prefix = _BACKEND_PREFIXES.get(backend.lower())
    if expected_prefix is None:
        return ()

    labels: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[,;\s]+", raw_value.strip()):
        if not token:
            continue
        try:
            label = get_backend_device_label(backend, int(token))
        except ValueError:
            label = _normalize_gpu_label(token)
        if label is None or not label.startswith(expected_prefix):
            continue
        if label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return tuple(labels)


def _device_id_from_label(label: str | None) -> int | None:
    if not label:
        return None
    match = _LABEL_PATTERN.match(label)
    if not match:
        return None
    return int(match.group("index"))


def _resolve_primary_gpu_labels(
    command: list[str],
    env: dict[str, str],
    backend: str,
    config: InstanceConfig,
) -> tuple[str, ...]:
    explicit_labels = list(_parse_device_labels(_parse_last_flag_value(command, "--device"), backend))
    if explicit_labels:
        return tuple(explicit_labels)

    for env_name in _DEVICE_ENV_VARS.get(backend, ()):  # pragma: no branch - tiny mapping
        env_labels = tuple(
            label
            for device_id in _parse_device_ids(env.get(env_name))
            if (label := get_backend_device_label(backend, device_id)) is not None
        )
        if env_labels:
            return env_labels

    if backend == "cpu":
        return ()
    fallback = get_backend_device_label(backend, int(config.gpu.device_id))
    return (fallback,) if fallback is not None else ()


def describe_effective_runtime(config: InstanceConfig) -> EffectiveRuntimeSelection:
    """Resolve the effective CPU/GPU selection without mutating runtime behavior."""
    backend = config.gpu.backend.lower()
    command = build_command(config)
    env = build_env(config)

    threads = _parse_last_flag_int(command, "--threads", "-t")
    gpu_layers = _parse_last_flag_int(command, "--n-gpu-layers", "-ngl")
    if gpu_layers is None:
        gpu_layers = 0 if backend == "cpu" else int(config.gpu.layers)

    main_gpu_labels = _resolve_primary_gpu_labels(command, env, backend, config)
    gpu_labels = list(main_gpu_labels)
    for label in _parse_device_labels(_parse_last_flag_value(command, "--device-draft"), backend):
        if label not in gpu_labels:
            gpu_labels.append(label)
    gpu_labels_tuple = tuple(gpu_labels)
    gpu_active = backend != "cpu" and gpu_layers > 0 and bool(gpu_labels)
    cpu_active = backend == "cpu" or gpu_layers <= 0

    primary_gpu_label = None
    main_gpu_id = _parse_last_flag_int(command, "--main-gpu", "-mg")
    if main_gpu_labels:
        if main_gpu_id is not None:
            expected_label = get_backend_device_label(backend, main_gpu_id)
            if expected_label in main_gpu_labels:
                primary_gpu_label = expected_label
        if primary_gpu_label is None:
            primary_gpu_label = main_gpu_labels[0]

    return EffectiveRuntimeSelection(
        backend=backend,
        threads=threads,
        gpu_layers=gpu_layers,
        cpu_active=cpu_active,
        gpu_active=gpu_active,
        gpu_labels=gpu_labels_tuple,
        primary_gpu_label=primary_gpu_label,
        primary_device_id=_device_id_from_label(primary_gpu_label),
    )


def resolve_model_size_gb(config: InstanceConfig) -> float | None:
    """Return the model file size using the same base-1024 GB convention as memory parsing."""
    try:
        model_path = resolve_model_path(config)
        return model_path.stat().st_size / BYTES_PER_GB
    except OSError:
        return None


def parse_detected_gpus(text: str) -> list[DetectedGpu]:
    """Parse best-effort GPU label/name pairs from llama.cpp stderr text."""
    detected: dict[str, DetectedGpu] = {}
    for pattern in (_INVENTORY_PATTERN, _ACTIVE_DEVICE_PATTERN):
        for match in pattern.finditer(text):
            label = _normalize_gpu_label(match.group("label"))
            name = match.group("name").strip()
            if label not in detected or detected[label].name is None:
                detected[label] = DetectedGpu(label=label, name=name)
    return sorted(detected.values(), key=_sort_gpu_label)


def parse_vulkaninfo_summary(text: str) -> list[DetectedGpu]:
    """Parse VulkanN adapter names from ``vulkaninfo --summary`` output."""
    detected: dict[str, DetectedGpu] = {}
    current_label: str | None = None

    for line in text.splitlines():
        gpu_match = _VULKANINFO_GPU_PATTERN.match(line.strip())
        if gpu_match:
            current_label = f"Vulkan{int(gpu_match.group('index'))}"
            detected.setdefault(current_label, DetectedGpu(label=current_label))
            continue

        name_match = _VULKANINFO_DEVICE_NAME_PATTERN.match(line)
        if name_match and current_label is not None:
            detected[current_label] = DetectedGpu(
                label=current_label,
                name=name_match.group("name").strip(),
            )

    return sorted(detected.values(), key=_sort_gpu_label)


def probe_vulkan_gpu_inventory(*, refresh: bool = False) -> list[DetectedGpu]:
    """Return current Vulkan labels and adapter names from the local Vulkan loader."""
    global _vulkaninfo_cache

    now = time.monotonic()
    if (
        not refresh
        and _vulkaninfo_cache is not None
        and now - _vulkaninfo_cache[0] < _VULKANINFO_CACHE_TTL_SECONDS
    ):
        return list(_vulkaninfo_cache[1])

    vulkaninfo = shutil.which("vulkaninfo")
    if vulkaninfo is None:
        _vulkaninfo_cache = (now, ())
        return []

    try:
        completed = subprocess.run(
            [vulkaninfo, "--summary"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _vulkaninfo_cache = (now, ())
        return []

    text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    inventory = tuple(parse_vulkaninfo_summary(text))
    _vulkaninfo_cache = (now, inventory)
    return list(inventory)


def collect_detected_gpu_inventory(configs: Iterable[InstanceConfig]) -> list[DetectedGpu]:
    """Aggregate known GPU labels and adapter names, preferring live Vulkan inventory."""
    detected: dict[str, DetectedGpu] = {
        gpu.label: gpu for gpu in probe_vulkan_gpu_inventory()
    }

    for config in configs:
        selection = describe_effective_runtime(config)
        if not selection.gpu_active:
            continue

        for label in selection.gpu_labels:
            detected.setdefault(label, DetectedGpu(label=label))

        stderr_log = get_log_files(config.name)[1]
        try:
            text = stderr_log.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for gpu in parse_detected_gpus(text):
            existing = detected.get(gpu.label)
            if existing is None or existing.name is None:
                detected[gpu.label] = gpu

    return sorted(detected.values(), key=_sort_gpu_label)


def _normalize_gpu_label(label: str) -> str:
    match = _LABEL_PATTERN.match(label.strip())
    if not match:
        return label.strip()
    prefix = match.group("prefix").capitalize()
    if prefix.upper() == "CUDA":
        prefix = "CUDA"
    if prefix.upper() == "HIP":
        prefix = "HIP"
    return f"{prefix}{int(match.group('index'))}"


def _sort_gpu_label(gpu: DetectedGpu) -> tuple[str, int]:
    match = _LABEL_PATTERN.match(gpu.label)
    if not match:
        return gpu.label, 0
    return match.group("prefix").lower(), int(match.group("index"))
