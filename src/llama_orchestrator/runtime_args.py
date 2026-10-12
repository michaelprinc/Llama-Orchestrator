"""Runtime argument parser and normalizer for llama-orchestrator.

Parses, normalizes, and manipulates llama-server CLI arguments in a structured way.
Supports ``--flag value``, ``--flag=value``, boolean flags, duplicate arguments,
and unknown arguments (kept queryable and marked via :attr:`RuntimeArgs.unknown`).

Catalog design (R9/F15, Decision 4): the previous catalog was machine-generated
garbage (hundreds of recursively nested ``--sorta-kv-head-id-slot-id-...``
variants) and missed nearly every real llama-server flag, while a broken boolean
heuristic misclassified dash-less flags as booleans and swallowed their values.
The catalog below is a curated list of the real llama.cpp / llama-server flag
surface used by this project (build_command output + all ``instances/*.json``
configs) plus well-known upstream flags. Anything not listed is still parsed,
preserved, and queryable — it is simply reported in :attr:`RuntimeArgs.unknown`.

Classification rules (deterministic, no heuristics):

* A token starting with ``--`` or a single ``-`` (length > 1) is a flag token.
* ``--name=value`` (first ``=`` after the dashes) is an assignment; the name is
  recorded in both ``named`` and ``assignments``.
* A flag in :data:`KNOWN_BOOLEAN_FLAGS` never consumes a following token.
* Every other flag (known value flag **or unknown**) consumes the next token as
  its value **iff** that token exists and is not itself a flag token; otherwise
  the flag is recorded as a boolean presence (trailing flag).
* Negative numeric values cannot be passed space-separated — use ``--flag=-1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class ParsedArg:
    """A single parsed CLI argument."""

    name: str  # e.g. '--device' or '--flash-attn'
    value: str | None  # e.g. 'Vulkan1' or None for boolean flags
    is_flag: bool  # True if this is a boolean flag (--flag with no value)
    is_assignment: bool  # True if parsed from --flag=value syntax
    raw: str  # The original raw token


@dataclass(frozen=True)
class RuntimeArgs:
    """Structured representation of llama-server runtime arguments.

    Attributes:
        positional: Positional arguments (tokens that are not flags).
        named: Dict mapping flag names to their values — **including unknown
            flags**, so every parsed argument stays queryable via :meth:`get`.
        flags: Set of boolean flags present (known booleans, trailing value
            flags without a value, and unknown boolean-style flags).
        assignments: Dict mapping flag names to raw ``--flag=value`` strings.
        extra: Kept for API compatibility; always empty after R9 — unknown
            arguments are no longer demoted out of ``named``/``flags``.
        unknown: Names present in ``named`` or ``flags`` that are not part of
            the curated catalog (see :data:`KNOWN_ARGS`).
    """

    positional: list[str] = field(default_factory=list)
    named: dict[str, str] = field(default_factory=dict)
    flags: frozenset[str] = field(default_factory=frozenset)
    assignments: dict[str, str] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)
    unknown: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Ensure flags/unknown are frozensets for immutability."""
        object.__setattr__(self, "flags", frozenset(self.flags))
        object.__setattr__(self, "unknown", frozenset(self.unknown))

    @property
    def duplicates(self) -> list[ParsedArg]:
        """Return arguments that appear more than once (excluding extra)."""
        # Only the last value wins in the named dicts, so no duplicates exist.
        # Duplicates are detected during parsing, not stored here.
        return []

    def to_list(self) -> list[str]:
        """Convert back to a flat argument list suitable for subprocess."""
        result: list[str] = list(self.positional)

        # Named arguments: --flag value (skip names emitted via assignments)
        for name, value in sorted(self.named.items()):
            if name in self.assignments:
                continue
            result.extend([name, value])

        # Assignment arguments: raw --flag=value tokens
        for value in sorted(self.assignments.values()):
            result.append(value)

        # Boolean flags
        for flag in sorted(self.flags):
            result.append(flag)

        # Extra arguments (preserved verbatim)
        result.extend(self.extra)

        return result

    def set(self, name: str, value: str | bool | None) -> RuntimeArgs:
        """Set or update a named argument.

        Args:
            name: The flag name (e.g. '--device').
            value: The value to set. If True, converts to boolean flag.
                   If None, removes the argument.

        Returns:
            A new RuntimeArgs with the modification applied; ``self`` is not mutated.
        """
        if value is None:
            return self.remove(name)

        if value is True:
            named = {k: v for k, v in self.named.items() if k != name}
            assignments = {k: v for k, v in self.assignments.items() if k != name}
            flags = frozenset(self.flags | {name})
            unknown = set(self.unknown)
            if not _is_catalogued(name):
                unknown.add(name)
            return replace(
                self,
                named=named,
                assignments=assignments,
                flags=flags,
                unknown=frozenset(unknown),
            )

        # Set as named argument (assignment form is preserved when present)
        named = dict(self.named)
        named[name] = str(value)
        assignments = dict(self.assignments)
        if name in assignments:
            assignments[name] = f"{name}={value}"
        flags = frozenset(self.flags - {name})
        unknown = set(self.unknown)
        if not _is_catalogued(name):
            unknown.add(name)
        return replace(
            self,
            named=named,
            assignments=assignments,
            flags=flags,
            unknown=frozenset(unknown),
        )

    def remove(self, name: str) -> RuntimeArgs:
        """Remove a named argument or flag.

        Args:
            name: The flag name to remove.

        Returns:
            A new RuntimeArgs without the argument; ``self`` is not mutated.
        """
        return replace(
            self,
            named={k: v for k, v in self.named.items() if k != name},
            flags=frozenset(self.flags - {name}),
            assignments={k: v for k, v in self.assignments.items() if k != name},
        )

    def merge(self, other: RuntimeArgs) -> RuntimeArgs:
        """Merge another RuntimeArgs into this one.

        Other's values take precedence for overlapping keys.

        Args:
            other: Another RuntimeArgs to merge.

        Returns:
            A new RuntimeArgs with merged results; neither input is mutated.
        """
        named = dict(self.named)
        named.update(other.named)

        flags = frozenset(self.flags | other.flags)

        assignments = dict(self.assignments)
        assignments.update(other.assignments)

        positional = list(self.positional) + list(other.positional)
        extra = list(self.extra) + list(other.extra)
        unknown = frozenset(self.unknown | other.unknown)

        return replace(
            self,
            named=named,
            flags=flags,
            assignments=assignments,
            positional=positional,
            extra=extra,
            unknown=unknown,
        )

    def find_conflicts(self, other: RuntimeArgs) -> list[str]:
        """Find conflicting arguments between two RuntimeArgs.

        Args:
            other: Another RuntimeArgs to compare against.

        Returns:
            List of conflicting argument names.
        """
        conflicts: list[str] = []
        for name in self.named:
            if name in other.named and self.named[name] != other.named[name]:
                conflicts.append(name)
        return conflicts

    def has_conflicts_with(self, other: RuntimeArgs) -> bool:
        """Check if this RuntimeArgs has conflicts with another.

        Args:
            other: Another RuntimeArgs to compare against.

        Returns:
            True if there are any conflicting arguments.
        """
        return len(self.find_conflicts(other)) > 0

    def get(self, name: str) -> str | None:
        """Get the value of a named argument (known or unknown).

        Args:
            name: The flag name to look up.

        Returns:
            The value, or None if not found.
        """
        return self.named.get(name)

    def has(self, name: str) -> bool:
        """Check if a named argument or flag is present.

        Args:
            name: The flag name to check.

        Returns:
            True if present as a named arg or boolean flag.
        """
        return name in self.named or name in self.flags


# ---------------------------------------------------------------------------
# Curated flag catalog (R9/F15, Decision 4)
# ---------------------------------------------------------------------------
# Real llama-server / llama.cpp flags used by this project: everything emitted
# by engine/command.py::build_command and the diffusion adapter, every flag
# appearing in instances/*/config.json, plus well-known upstream flags.
# No generated variants; no recursion; unknown flags remain queryable.

#: Flags that take a value (space-separated or ``=``).
_KNOWN_VALUE_FLAGS: frozenset[str] = frozenset({
    # --- server / HTTP ---
    "--host", "--port", "--alias", "--api-key", "--api-key-file",
    "--slot-save-path", "--timeout",
    # --- model loading ---
    "--model", "--model-draft", "--lora", "--lora-scale", "--lora-init",
    "--mmproj",
    # --- GPU / device ---
    "--device", "--device-draft", "--main-gpu", "--gpu-layers",
    "--gpu-layers-draft", "--n-gpu-layers", "--n-gpu-layers-draft",
    "--split-mode", "--tensor-split", "--numa", "--cpu-moe", "--n-cpu-moe",
    # --- context / batching ---
    "--ctx-size", "--batch-size", "--ubatch-size", "--threads",
    "--threads-batch", "--parallel", "--cont-batching", "--max-tokens",
    "--n-predict",
    # --- sampling / penalties ---
    "--temp", "--temperature", "--top-k", "--top-p", "--min-p",
    "--repeat-penalty", "--presence-penalty", "--dry-base",
    "--dry-multiplier", "--dry-allowed-length", "--dry-penalty-last-n",
    "--seed",
    # --- KV cache / memory ---
    "--cache-type-k", "--cache-type-v", "--cache-type-k-draft",
    "--cache-type-v-draft", "--cache-reuse", "--cache-ram",
    "--cache-idle-slots", "--override-tensor",
    # --- prompt / chat ---
    "--prompt", "--system", "--instruct", "--chat-template-kwargs",
    # --- speculative decoding ---
    "--spec-type", "--spec-draft-n-max", "--spec-draft-n-min",
    "--spec-draft-p-min", "--spec-draft-type-k", "--spec-draft-type-v",
    "--draft-max", "--draft-min", "--draft-p-min", "--draft-block-size",
    "--mtp-head",
    # --- checkpoints / context checkpoints ---
    "--checkpoint-every-n-tokens", "--checkpoint-min-step", "--ctx-checkpoints",
    # --- misc real flags ---
    "--pooling", "--runner", "--flash-attn",
})

#: Boolean flags — take no value, never swallow a following token.
KNOWN_BOOLEAN_FLAGS: frozenset[str] = frozenset({
    "--help", "--version", "--verbose", "--public", "--metrics",
    "--no-webui", "--log-disable", "--log-timestamps",
    "--mlock", "--mmap", "--no-mmap", "--numa-distribute",
    "--ignore-eos", "--embedding", "--no-cont-batching",
    "--logits-all", "--no-warmup", "--warmup",
    "--cpu-moe",
    # project config booleans
    "--jinja", "--kv-unified", "--reasoning-preserve",
    "--no-mmproj",
})

#: Value-taking flags used by project configs but not part of upstream llama.cpp.
_PROJECT_VALUE_FLAGS: frozenset[str] = frozenset({
    "--reasoning",  # on / off / auto in this project's instance configs
    "--reasoning-budget", "--reasoning-budget-message",
})

#: Union of everything the parser knows about (two-dash flags).
KNOWN_ARGS: frozenset[str] = _KNOWN_VALUE_FLAGS | KNOWN_BOOLEAN_FLAGS | _PROJECT_VALUE_FLAGS

#: Single-dash flags emitted by this project (build_command emits "-fit off").
_SINGLE_DASH_VALUE_FLAGS: frozenset[str] = frozenset({"-fit"})
_SINGLE_DASH_BOOLEAN_FLAGS: frozenset[str] = frozenset()

#: Device-label prefixes per GPU backend (mirrors validator._BACKEND_DEVICE_PREFIX;
#: duplicated here to avoid a circular import between runtime_args and config).
_BACKEND_DEVICE_PREFIX: dict[str, str] = {
    "vulkan": "Vulkan",
    "cuda": "CUDA",
    "hip": "ROCm",
    "metal": "Metal",
}


def _is_flag_token(token: str) -> bool:
    """True if the token is a flag (``--name`` or single-dash ``-x``)."""
    return token.startswith("--") or (token.startswith("-") and len(token) > 1)


def _assignment_split(token: str) -> tuple[str, str] | None:
    """Split ``--name=value`` at the first ``=`` after the dashes.

    Returns (name, value) or None if the token carries no assignment.
    """
    start = 2 if token.startswith("--") else 1
    eq_pos = token.find("=", start)
    if eq_pos == -1:
        return None
    return token[:eq_pos], token[eq_pos + 1:]


def _is_known_boolean(token: str) -> bool:
    return token in KNOWN_BOOLEAN_FLAGS or token in _SINGLE_DASH_BOOLEAN_FLAGS


def _is_catalogued(name: str) -> bool:
    """True if the flag is part of the curated catalog (value or boolean)."""
    return (
        name in KNOWN_ARGS
        or name in _SINGLE_DASH_VALUE_FLAGS
        or name in _SINGLE_DASH_BOOLEAN_FLAGS
    )


def parse_args_list(args: list[str]) -> RuntimeArgs:
    """Parse a list of CLI arguments into a structured RuntimeArgs.

    Supports:
    - ``--flag value`` (space-separated)
    - ``--flag=value`` (assignment syntax)
    - ``--boolean-flag`` (no value; see :data:`KNOWN_BOOLEAN_FLAGS`)
    - Duplicate arguments (last value wins for named args)
    - Unknown flags (kept in ``named``/``flags``, reported in ``unknown``)
    - Trailing value flags without a value (recorded as boolean presence)

    Args:
        args: List of CLI argument strings.

    Returns:
        A RuntimeArgs with parsed structure.
    """
    positional: list[str] = []
    named: dict[str, str] = {}
    flags: set[str] = set()
    assignments: dict[str, str] = {}
    unknown: set[str] = set()

    def _mark(token: str) -> None:
        if token not in KNOWN_ARGS and token not in _SINGLE_DASH_VALUE_FLAGS \
                and token not in _SINGLE_DASH_BOOLEAN_FLAGS:
            unknown.add(token)

    i = 0
    n = len(args)
    while i < n:
        token = args[i]

        if not _is_flag_token(token):
            positional.append(token)
            i += 1
            continue

        # --flag=value / -f=value assignment syntax
        split = _assignment_split(token)
        if split is not None:
            name, value = split
            assignments[name] = token
            named[name] = value
            _mark(name)
            i += 1
            continue

        # Known boolean flags never consume a following token
        if _is_known_boolean(token):
            flags.add(token)
            i += 1
            continue

        # Value-taking flag (known or unknown): consume the next token only if
        # it exists and is not itself a flag. Otherwise record as a trailing
        # boolean presence so the flag stays queryable via .has().
        if i + 1 < n and not _is_flag_token(args[i + 1]):
            named[token] = args[i + 1]
            i += 2
        else:
            flags.add(token)
            i += 1

        _mark(token)

    return RuntimeArgs(
        positional=positional,
        named=named,
        flags=frozenset(flags),
        assignments=assignments,
        extra=[],
        unknown=frozenset(unknown),
    )


def to_args_list(runtime_args: RuntimeArgs) -> list[str]:
    """Convert a RuntimeArgs back to a flat argument list.

    Args:
        runtime_args: The structured runtime args.

    Returns:
        Flat list of CLI argument strings.
    """
    return runtime_args.to_list()


def set_arg(args: list[str], name: str, value: str | bool | None) -> list[str]:
    """Set or update an argument in a flat argument list.

    Args:
        args: The original argument list.
        name: The flag name to set.
        value: The value to set. If True, converts to boolean flag.
               If None, removes the argument.

    Returns:
        New argument list with the modification applied.
    """
    parsed = parse_args_list(args)
    updated = parsed.set(name, value)
    return updated.to_list()


def remove_arg(args: list[str], name: str) -> list[str]:
    """Remove an argument from a flat argument list.

    Args:
        args: The original argument list.
        name: The flag name to remove.

    Returns:
        New argument list without the specified argument.
    """
    parsed = parse_args_list(args)
    updated = parsed.remove(name)
    return updated.to_list()


def find_duplicates(args: list[str]) -> list[tuple[str, int, int]]:
    """Find duplicate arguments in a flat argument list.

    Args:
        args: The argument list to check.

    Returns:
        List of (flag_name, first_index, second_index) tuples for duplicates.
    """
    seen: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []

    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--") and "=" not in token[2:]:
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                # --flag value pair
                if token in seen:
                    duplicates.append((token, seen[token], i))
                seen[token] = i
                i += 2
                continue
            elif token in seen:
                duplicates.append((token, seen[token], i))
            seen[token] = i
        i += 1

    return duplicates


def find_conflicts(config: dict[str, Any], args_list: list[str]) -> list[dict[str, Any]]:
    """Find conflicts between structured config and args list.

    Args:
        config: The structured config dict (expects optional ``gpu`` and
            ``server`` sub-dicts mirroring InstanceConfig).
        args_list: The flat args list.

    Returns:
        List of conflict dicts with 'severity', 'code', 'message', and
        'suggested_fix'.
    """
    conflicts: list[dict[str, Any]] = []

    parsed = parse_args_list(args_list)

    # Check gpu.device_id vs --device (backend-aware label prefix)
    gpu = config.get("gpu") or {}
    gpu_device_id = gpu.get("device_id")
    backend = gpu.get("backend")
    device_arg = parsed.get("--device")

    if gpu_device_id is not None and device_arg is not None:
        expected_prefix = _BACKEND_DEVICE_PREFIX.get(str(backend))
        if expected_prefix is not None:
            expected_label = f"{expected_prefix}{gpu_device_id}"
            if device_arg != expected_label:
                conflicts.append({
                    "severity": "warning",
                    "code": "GPU_DEVICE_CONFLICT",
                    "message": (
                        f"gpu.device_id={gpu_device_id} but args contain --device {device_arg} "
                        f"(expected '{expected_label}' for backend '{backend}')"
                    ),
                    "suggested_fix": "sync_gpu_from_args",
                })

    # Check server.parallel vs --parallel
    server = config.get("server") or {}
    server_parallel = server.get("parallel")
    parallel_arg = parsed.get("--parallel")

    if (
        server_parallel is not None
        and parallel_arg is not None
        and str(server_parallel) != parallel_arg
    ):
        conflicts.append({
            "severity": "warning",
            "code": "PARALLEL_CONFLICT",
            "message": f"server.parallel={server_parallel} but args contain --parallel {parallel_arg}",
            "suggested_fix": "sync_parallel_from_server",
        })

    # Check for duplicate arguments
    dupes = find_duplicates(args_list)
    for flag, first_idx, second_idx in dupes:
        conflicts.append({
            "severity": "warning",
            "code": "DUPLICATE_ARG",
            "message": f"Duplicate argument {flag} at indices {first_idx} and {second_idx}",
            "suggested_fix": "remove_duplicate",
        })

    return conflicts
