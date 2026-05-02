"""
Binary registry storage for llama-orchestrator.

Manages bins/registry.json file with atomic writes and CRUD operations.
All lookups use UUID as the primary key.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional
from uuid import UUID

from llama_orchestrator.binaries.schema import BinaryRegistry, BinaryVersion

logger = logging.getLogger(__name__)

# Registry filename
REGISTRY_FILENAME = "registry.json"

# Version metadata filename (in each binary directory)
VERSION_FILENAME = "version.json"


class RegistryError(Exception):
    """Error during registry operations."""
    
    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.message = message
        self.cause = cause
        super().__init__(message)


def get_registry_path(bins_dir: Path) -> Path:
    """Get path to registry.json file."""
    return bins_dir / REGISTRY_FILENAME


def get_version_path(binary_dir: Path) -> Path:
    """Get path to version.json file in binary directory."""
    return binary_dir / VERSION_FILENAME


def load_registry(bins_dir: Path) -> BinaryRegistry:
    """
    Load binary registry from bins/registry.json.
    
    Creates empty registry if file doesn't exist.
    
    Args:
        bins_dir: Path to bins/ directory
        
    Returns:
        BinaryRegistry model
        
    Raises:
        RegistryError: If registry file is corrupted
    """
    registry_path = get_registry_path(bins_dir)
    
    if not registry_path.exists():
        logger.debug(f"Registry not found at {registry_path}, creating empty registry")
        return BinaryRegistry()
    
    try:
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
        
        registry = BinaryRegistry.model_validate(data)
        logger.debug(f"Loaded registry with {len(registry.binaries)} binaries")
        return registry
        
    except json.JSONDecodeError as e:
        raise RegistryError(f"Invalid JSON in registry: {e}", cause=e) from e
    except Exception as e:
        raise RegistryError(f"Failed to load registry: {e}", cause=e) from e


def save_registry(bins_dir: Path, registry: BinaryRegistry) -> None:
    """
    Save binary registry to bins/registry.json with atomic write.
    
    Uses temp file + rename for atomic operation to prevent corruption.
    
    Args:
        bins_dir: Path to bins/ directory
        registry: BinaryRegistry model to save
        
    Raises:
        RegistryError: If save fails
    """
    bins_dir.mkdir(parents=True, exist_ok=True)
    registry_path = get_registry_path(bins_dir)
    
    try:
        # Serialize to JSON
        data = registry.model_dump(mode="json")
        json_str = json.dumps(data, indent=2, default=str)
        
        # Atomic write: write to temp file, then rename
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=bins_dir,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(json_str)
            temp_path = Path(f.name)
        
        # Rename (atomic on most filesystems)
        shutil.move(str(temp_path), str(registry_path))
        logger.debug(f"Saved registry with {len(registry.binaries)} binaries")
        
    except Exception as e:
        # Clean up temp file if it exists
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink()
        raise RegistryError(f"Failed to save registry: {e}", cause=e) from e


def save_version_metadata(binary: BinaryVersion, bins_dir: Path) -> None:
    """
    Save version.json metadata file in binary directory.
    
    Args:
        binary: BinaryVersion model to save
        bins_dir: Path to bins/ directory
        
    Raises:
        RegistryError: If save fails
    """
    binary_dir = bins_dir / str(binary.id)
    version_path = get_version_path(binary_dir)
    
    try:
        binary_dir.mkdir(parents=True, exist_ok=True)
        
        data = binary.model_dump(mode="json")
        json_str = json.dumps(data, indent=2, default=str)
        
        with open(version_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        
        logger.debug(f"Saved version metadata to {version_path}")
        
    except Exception as e:
        raise RegistryError(f"Failed to save version metadata: {e}", cause=e) from e


def load_version_metadata(binary_dir: Path) -> Optional[BinaryVersion]:
    """
    Load version.json metadata from binary directory.
    
    Args:
        binary_dir: Path to binary directory
        
    Returns:
        BinaryVersion model or None if not found
    """
    version_path = get_version_path(binary_dir)
    
    if not version_path.exists():
        return None
    
    try:
        with open(version_path, encoding="utf-8") as f:
            data = json.load(f)
        
        return BinaryVersion.model_validate(data)
        
    except Exception as e:
        logger.warning(f"Failed to load version metadata from {version_path}: {e}")
        return None


class BinaryRegistryManager:
    """
    Manager for binary registry operations.
    
    Provides a higher-level interface for CRUD operations on the registry.
    All lookups use UUID as the primary key.
    """
    
    def __init__(self, bins_dir: Path):
        """
        Initialize registry manager.
        
        Args:
            bins_dir: Path to bins/ directory
        """
        self.bins_dir = bins_dir
        self._registry: Optional[BinaryRegistry] = None
    
    @property
    def registry(self) -> BinaryRegistry:
        """Get the registry, loading if necessary."""
        if self._registry is None:
            self._registry = load_registry(self.bins_dir)
        return self._registry
    
    def reload(self) -> BinaryRegistry:
        """Force reload of registry from disk."""
        self._registry = load_registry(self.bins_dir)
        return self._registry
    
    def save(self) -> None:
        """Save current registry to disk."""
        if self._registry is not None:
            save_registry(self.bins_dir, self._registry)
    
    def get_by_id(self, binary_id: UUID) -> Optional[BinaryVersion]:
        """
        Get binary by UUID (primary lookup).
        
        Args:
            binary_id: UUID of binary
            
        Returns:
            BinaryVersion or None if not found
        """
        return self.registry.get_by_id(binary_id)
    
    def get_by_version(self, version: str, variant: str) -> Optional[BinaryVersion]:
        """
        Get binary by version and variant (fallback lookup).
        
        Args:
            version: llama.cpp version tag
            variant: Platform variant
            
        Returns:
            BinaryVersion or None if not found
        """
        return self.registry.get_by_version(version, variant)
    
    def get_default(self) -> Optional[BinaryVersion]:
        """Get the default binary if set."""
        return self.registry.get_default()
    
    def add(self, binary: BinaryVersion) -> None:
        """
        Add a binary to the registry.
        
        Also saves version.json in the binary directory.
        
        Args:
            binary: BinaryVersion to add
        """
        self.registry.add(binary)
        save_version_metadata(binary, self.bins_dir)
        self.save()
    
    def remove(self, binary_id: UUID) -> Optional[BinaryVersion]:
        """
        Remove a binary from the registry.
        
        Note: Does NOT delete files, only removes from registry.
        
        Args:
            binary_id: UUID of binary to remove
            
        Returns:
            Removed BinaryVersion or None if not found
        """
        binary = self.registry.remove(binary_id)
        if binary is not None:
            self.save()
        return binary
    
    def set_default(self, binary_id: UUID) -> bool:
        """
        Set the default binary.
        
        Args:
            binary_id: UUID of binary to set as default
            
        Returns:
            True if successful, False if binary not found
        """
        success = self.registry.set_default(binary_id)
        if success:
            self.save()
        return success
    
    def list_all(self) -> list[BinaryVersion]:
        """List all installed binaries."""
        return list(self.registry.binaries)
    
    def list_versions(self) -> list[tuple[str, str]]:
        """List all unique (version, variant) pairs."""
        return self.registry.list_versions()
    
    def exists(self, binary_id: UUID) -> bool:
        """Check if a binary exists by UUID."""
        return self.get_by_id(binary_id) is not None
    
    def count(self) -> int:
        """Get the number of installed binaries."""
        return len(self.registry.binaries)
    
    def get_binary_path(self, binary_id: UUID) -> Optional[Path]:
        """
        Get the full path to a binary directory.
        
        Args:
            binary_id: UUID of binary
            
        Returns:
            Path to binary directory or None if not found
        """
        binary = self.get_by_id(binary_id)
        if binary is None:
            return None
        return self.bins_dir / str(binary.id)
    
    def get_server_path(self, binary_id: UUID) -> Optional[Path]:
        """
        Get the path to llama-server.exe for a binary.
        
        Args:
            binary_id: UUID of binary
            
        Returns:
            Path to llama-server.exe or None if not found
        """
        binary_path = self.get_binary_path(binary_id)
        if binary_path is None:
            return None
        return binary_path / "llama-server.exe"
    
    def verify_binary_exists(self, binary_id: UUID) -> bool:
        """
        Check if binary directory and executable exist on disk.
        
        Args:
            binary_id: UUID of binary
            
        Returns:
            True if binary files exist
        """
        server_path = self.get_server_path(binary_id)
        return server_path is not None and server_path.exists()
