"""
Command builder for llama.cpp server.

Builds the command line arguments for llama-server based on instance configuration.
Supports UUID-based binary resolution from bins/registry.json.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from llama_orchestrator.config import get_llama_server_path, get_project_root

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
    
    # Disable memory fit (can cause issues)
    args.extend(["-fit", "off"])
    
    # Add any extra args from config
    if config.args:
        args.extend(config.args)
    
    return args


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
    
    # Merge custom env vars from config
    env.update(config.env)
    
    return env


def format_command(args: list[str]) -> str:
    """Format command args as a shell-safe string for display."""
    import shlex
    return " ".join(shlex.quote(arg) for arg in args)


def validate_executable(config: "InstanceConfig | None" = None) -> tuple[bool, str]:
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
    
    if not server_exe.exists():
        return False, f"llama-server.exe not found at {server_exe}"
    
    if not server_exe.is_file():
        return False, f"{server_exe} is not a file"
    
    return True, f"llama-server found at {server_exe}"
