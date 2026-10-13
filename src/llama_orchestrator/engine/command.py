"""
Command builder for llama.cpp server.

Builds the command line arguments for llama-server based on instance configuration.
Supports UUID-based binary resolution from bins/registry.json.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from llama_orchestrator.config import get_llama_server_path, get_project_root
from llama_orchestrator.naming import (
    DIFFUSION_RUNNER_BASE,
    SERVER_BASE,
    executable_name,
)

if TYPE_CHECKING:
    from llama_orchestrator.config import InstanceConfig


def resolve_model_path(config: InstanceConfig) -> Path:
    """Resolve the model path relative to project root."""
    model_path = config.model.path
    if not model_path.is_absolute():
        model_path = get_project_root() / model_path
    return model_path.resolve()


def build_command(config: InstanceConfig) -> list[str]:
    """
    Build the llama-server command line from configuration.

    Uses UUID-based binary resolution if config has binary section.
    Falls back to legacy bin/ if not.

    Args:
        config: Instance configuration

    Returns:
        List of command arguments (first element is the executable)
    """
    # Resolve binary path using config (UUID-aware)
    server_exe = get_llama_server_path(config)
    model_path = resolve_model_path(config)

    if config.env.get("LLAMA_ORCH_RUNTIME") == "diffusion-gemma":
        runner = server_exe.parent / executable_name(DIFFUSION_RUNNER_BASE)
        cmd_args = [
            sys.executable,
            "-m",
            "llama_orchestrator.diffusion_http_adapter",
            "--runner", str(runner),
            "--model", str(model_path),
            "--host", config.server.host,
            "--port", str(config.server.port),
            "--max-tokens", str(config.model.context_size),
            "--gpu-layers", str(config.gpu.layers),
        ]
        if config.args:
            cmd_args.extend(config.args)
        return cmd_args

    args = _base_llama_server_args(config)

    # Add any extra args from config (raw tokens; they may shadow built-in
    # flags — see find_raw_arg_overrides for the R18 override policy).
    if config.args:
        args.extend(config.args)

    return args


def _base_llama_server_args(config: InstanceConfig) -> list[str]:
    """
    Build the built-in llama-server arguments from structured config fields.

    Excludes raw ``config.args`` so that override detection (R18) can compare
    the two layers without duplication of the flag-emission logic.
    """
    server_exe = get_llama_server_path(config)
    model_path = resolve_model_path(config)

    args = [
        str(server_exe),
        "--model", str(model_path),
        "--host", config.server.host,
        "--port", str(config.server.port),
        "--ctx-size", str(config.model.context_size),
        "--batch-size", str(config.model.batch_size),
        "--threads", str(config.model.threads),
        "--alias", config.name,  # Use instance name as alias
        "--timeout", str(config.server.timeout),
    ]

    # GPU settings
    if config.gpu.backend != "cpu":
        args.extend(["--n-gpu-layers", str(config.gpu.layers)])

    # Parallel slots
    if config.server.parallel > 1:
        args.extend(["--parallel", str(config.server.parallel)])

    # Disable memory fit (configurable — some llama.cpp versions require it, others do not)
    if config.server.disable_fit:
        args.extend(["-fit", "off"])

    return args


def _parse_flag_pairs(args: list[str]) -> dict[str, str]:
    """
    Parse command-line tokens into flag→value pairs.

    Supports ``--flag value``, ``--flag=value`` and single-dash flags
    (e.g. ``-fit off``). Later occurrences win, mirroring llama.cpp's
    last-occurrence-wins semantics for repeated flags. Non-flag tokens are
    skipped.
    """
    pairs: dict[str, str] = {}
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--"):
            if "=" in tok:
                flag, _, value = tok.partition("=")
                pairs[flag] = value
                i += 1
            elif i + 1 < len(args):
                pairs[tok] = args[i + 1]
                i += 2
            else:
                pairs[tok] = ""
                i += 1
        elif tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            if i + 1 < len(args):
                pairs[tok] = args[i + 1]
                i += 2
            else:
                pairs[tok] = ""
                i += 1
        else:
            i += 1
    return pairs


def find_raw_arg_overrides(config: InstanceConfig) -> list[tuple[str, str, str]]:
    """
    Detect raw ``config.args`` tokens that re-specify a built-in flag (R18).

    Returns ``(flag, built_in_value, raw_value)`` triples in the order each
    flag first appears in the raw args. llama.cpp applies the last occurrence
    of a repeated flag, so when the values differ the RAW value is what takes
    effect at runtime even though the structured config says otherwise — this
    must be surfaced (describe + INFO log), never silent.
    """
    if not config.args:
        return []
    base_map = _parse_flag_pairs(_base_llama_server_args(config))
    raw_map = _parse_flag_pairs(list(config.args))
    overrides: list[tuple[str, str, str]] = []
    for flag, raw_value in raw_map.items():
        if flag in base_map:
            overrides.append((flag, base_map[flag], raw_value))
    return overrides


def build_env(config: InstanceConfig) -> dict[str, str]:
    """
    Build environment variables for the llama-server process.

    Args:
        config: Instance configuration

    Returns:
        Dictionary of environment variables to set
    """
    import os

    # Start with current environment
    env = dict(os.environ)

    # Add GPU-specific environment variables
    if config.gpu.backend == "vulkan":
        env["GGML_VULKAN_DEVICE"] = str(config.gpu.device_id)
    elif config.gpu.backend == "cuda":
        env["CUDA_VISIBLE_DEVICES"] = str(config.gpu.device_id)
    elif config.gpu.backend == "hip":
        env["HIP_VISIBLE_DEVICES"] = str(config.gpu.device_id)

    # Merge custom env vars from config
    env.update(config.env)

    return env


def format_command(args: list[str]) -> str:
    """Format command args as a shell-safe string for display."""
    import shlex
    return " ".join(shlex.quote(arg) for arg in args)


def validate_executable(config: InstanceConfig | None = None) -> tuple[bool, str]:
    """
    Check if llama-server executable exists and is runnable.

    Args:
        config: Optional instance config for binary resolution

    Returns:
        Tuple of (is_valid, message)
    """
    try:
        server_exe = get_llama_server_path(config)
    except FileNotFoundError as e:
        return False, str(e)

    if config is not None and config.env.get("LLAMA_ORCH_RUNTIME") == "diffusion-gemma":
        runner = server_exe.parent / executable_name(DIFFUSION_RUNNER_BASE)
        if not runner.is_file():
            return False, f"DiffusionGemma runner not found at {runner}"
        return True, f"DiffusionGemma runner found at {runner}"

    if not server_exe.exists():
        return False, f"{executable_name(SERVER_BASE)} not found at {server_exe}"

    if not server_exe.is_file():
        return False, f"{server_exe} is not a file"

    return True, f"llama-server found at {server_exe}"
