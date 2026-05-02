"""
Configuration loader for llama-orchestrator.

Handles loading instance configs from JSON files and discovering
all configured instances.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from llama_orchestrator.config.schema import InstanceConfig

if TYPE_CHECKING:
    from collections.abc import Iterator


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""
    
    def __init__(self, path: Path, message: str, cause: Exception | None = None):
        self.path = path
        self.message = message
        self.cause = cause
        super().__init__(f"{path}: {message}")


def get_project_root() -> Path:
    """Get the llama-orchestrator project root directory."""
    # Walk up from this file to find the project root
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and parent.name == "llama-orchestrator":
            return parent
    # Fallback to current working directory
    return Path.cwd()


def get_instances_dir() -> Path:
    """Get the instances directory path."""
    return get_project_root() / "instances"


def get_bin_dir() -> Path:
    """Get the legacy bin directory path (deprecated, use bins/)."""
    return get_project_root() / "bin"


def get_bins_dir() -> Path:
    """Get the bins directory path (contains versioned binaries)."""
    bins_dir = get_project_root() / "bins"
    bins_dir.mkdir(exist_ok=True)
    return bins_dir


def get_llama_server_path(config: "InstanceConfig | None" = None) -> Path:
    """
    Get the path to llama-server executable.
    
    Resolution order:
    1. If config has binary.binary_id, lookup by UUID in registry
    2. If config has binary.version+variant, lookup by those
    3. Use default binary from registry
    4. Fall back to legacy bin/llama-server.exe
    
    Args:
        config: Optional InstanceConfig for binary resolution
        
    Returns:
        Path to llama-server.exe
        
    Raises:
        FileNotFoundError: If no valid binary found
    """
    from llama_orchestrator.binaries import get_binary_manager
    
    project_root = get_project_root()
    
    # Try new bins/ structure
    if config is not None and config.binary is not None:
        manager = get_binary_manager(project_root)
        server_path = manager.resolve_server_path(config.binary)
        if server_path is not None and server_path.exists():
            return server_path
    
    # Try default binary
    try:
        manager = get_binary_manager(project_root)
        default = manager.get_default()
        if default is not None:
            server_path = manager.registry.get_server_path(default.id)
            if server_path is not None and server_path.exists():
                return server_path
    except Exception:
        pass  # Fall through to legacy
    
    # Fall back to legacy bin/
    legacy_path = get_bin_dir() / "llama-server.exe"
    if legacy_path.exists():
        return legacy_path
    
    raise FileNotFoundError("No llama-server.exe found in bins/ or legacy bin/")


def get_state_dir() -> Path:
    """Get the state directory path."""
    state_dir = get_project_root() / "state"
    state_dir.mkdir(exist_ok=True)
    return state_dir


def get_logs_dir() -> Path:
    """Get the logs directory path."""
    logs_dir = get_project_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    return logs_dir


def load_config(path: Path) -> InstanceConfig:
    """
    Load an instance configuration from a JSON file.
    
    Args:
        path: Path to the config.json file
        
    Returns:
        Validated InstanceConfig model
        
    Raises:
        ConfigLoadError: If file cannot be read or validation fails
    """
    path = Path(path).resolve()
    
    if not path.exists():
        raise ConfigLoadError(path, "Configuration file not found")
    
    if not path.is_file():
        raise ConfigLoadError(path, "Path is not a file")
    
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigLoadError(path, f"Invalid JSON: {e}", e) from e
    except OSError as e:
        raise ConfigLoadError(path, f"Cannot read file: {e}", e) from e
    
    try:
        config = InstanceConfig.model_validate(data)
    except ValidationError as e:
        # Format validation errors nicely
        errors = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {loc}: {msg}")
        error_str = "\n".join(errors)
        raise ConfigLoadError(
            path, f"Validation failed:\n{error_str}", e
        ) from e
    
    return config


def load_config_from_dict(data: dict, name: str = "inline") -> InstanceConfig:
    """
    Load an instance configuration from a dictionary.
    
    Args:
        data: Configuration dictionary
        name: Name for error messages
        
    Returns:
        Validated InstanceConfig model
        
    Raises:
        ConfigLoadError: If validation fails
    """
    try:
        return InstanceConfig.model_validate(data)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            msg = err["msg"]
            errors.append(f"  - {loc}: {msg}")
        error_str = "\n".join(errors)
        raise ConfigLoadError(
            Path(name), f"Validation failed:\n{error_str}", e
        ) from e


def discover_instances() -> Iterator[tuple[str, Path]]:
    """
    Discover all configured instances.
    
    Yields:
        Tuples of (instance_name, config_path) for each found instance
    """
    instances_dir = get_instances_dir()
    
    if not instances_dir.exists():
        return
    
    for instance_dir in sorted(instances_dir.iterdir()):
        if not instance_dir.is_dir():
            continue
        
        config_path = instance_dir / "config.json"
        if config_path.exists():
            yield instance_dir.name, config_path


def load_all_instances() -> dict[str, InstanceConfig]:
    """
    Load all configured instances.
    
    Returns:
        Dictionary mapping instance names to their configs
        
    Raises:
        ConfigLoadError: If any instance config is invalid
    """
    instances: dict[str, InstanceConfig] = {}
    
    for name, config_path in discover_instances():
        config = load_config(config_path)
        # Ensure the name in config matches the directory name
        if config.name != name:
            raise ConfigLoadError(
                config_path,
                f"Instance name '{config.name}' in config does not match "
                f"directory name '{name}'"
            )
        instances[name] = config
    
    return instances


def get_instance_config(name: str) -> InstanceConfig:
    """
    Get configuration for a specific instance by name.
    
    Args:
        name: Instance name
        
    Returns:
        Instance configuration
        
    Raises:
        ConfigLoadError: If instance not found or config invalid
    """
    instances_dir = get_instances_dir()
    config_path = instances_dir / name / "config.json"
    
    if not config_path.exists():
        raise ConfigLoadError(
            config_path,
            f"Instance '{name}' not found. Use 'llama-orch init {name}' to create it."
        )
    
    return load_config(config_path)


def save_config(config: InstanceConfig, path: Path | None = None) -> Path:
    """
    Save an instance configuration to a JSON file.
    
    Args:
        config: Instance configuration to save
        path: Optional custom path (defaults to instances/<name>/config.json)
        
    Returns:
        Path where config was saved
    """
    if path is None:
        instances_dir = get_instances_dir()
        instance_dir = instances_dir / config.name
        instance_dir.mkdir(parents=True, exist_ok=True)
        path = instance_dir / "config.json"
    
    # Serialize with nice formatting
    data = config.model_dump(mode="json")
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    
    return path
