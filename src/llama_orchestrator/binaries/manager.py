"""
Binary manager for llama-orchestrator.

Orchestrates binary installation, removal, and resolution.
UUID is the primary identifier for all operations.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID, uuid4

from llama_orchestrator.binaries.downloader import (
    DownloadError,
    download_and_extract,
    find_executables,
    get_directory_size,
)
from llama_orchestrator.binaries.github import (
    GitHubClient,
    GitHubError,
    get_download_url,
)
from llama_orchestrator.binaries.registry import (
    BinaryRegistryManager,
    RegistryError,
)
from llama_orchestrator.binaries.schema import (
    BinaryConfig,
    BinaryVersion,
    SupportedVariant,
    build_download_url,
)

logger = logging.getLogger(__name__)


class BinaryManagerError(Exception):
    """Error during binary management operations."""
    
    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.message = message
        self.cause = cause
        super().__init__(message)


class BinaryNotFoundError(BinaryManagerError):
    """Binary not found in registry."""
    
    def __init__(self, identifier: str):
        self.identifier = identifier
        super().__init__(f"Binary not found: {identifier}")


class BinaryInUseError(BinaryManagerError):
    """Binary is in use and cannot be removed."""
    
    def __init__(self, binary_id: UUID, instances: list[str]):
        self.binary_id = binary_id
        self.instances = instances
        super().__init__(
            f"Binary {binary_id} is in use by instances: {', '.join(instances)}"
        )


# Progress callback type
ProgressCallback = Callable[[int, Optional[int]], None]


class BinaryManager:
    """
    Manager for llama.cpp binary versions.
    
    Handles installation, removal, and resolution of binaries.
    UUID is the primary identifier for all operations.
    """
    
    def __init__(self, project_root: Path):
        """
        Initialize binary manager.
        
        Args:
            project_root: Path to llama-orchestrator project root
        """
        self.project_root = project_root
        self.bins_dir = project_root / "bins"
        self.legacy_bin_dir = project_root / "bin"
        self._registry_manager: Optional[BinaryRegistryManager] = None
    
    @property
    def registry(self) -> BinaryRegistryManager:
        """Get the registry manager."""
        if self._registry_manager is None:
            self._registry_manager = BinaryRegistryManager(self.bins_dir)
        return self._registry_manager
    
    def install(
        self,
        version: str,
        variant: SupportedVariant,
        source_url: Optional[str] = None,
        expected_sha256: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
        set_as_default: bool = False,
    ) -> BinaryVersion:
        """
        Install a llama.cpp binary version.
        
        Downloads from GitHub releases, extracts to bins/{uuid}/, and
        registers in the registry.
        
        Args:
            version: Version tag (e.g., 'b7572') or 'latest'
            variant: Platform variant (e.g., 'win-vulkan-x64')
            source_url: Custom download URL (overrides auto-generated)
            expected_sha256: Expected SHA256 for verification
            progress_callback: Optional download progress callback
            set_as_default: Whether to set as default binary
            
        Returns:
            BinaryVersion with UUID for the installed binary
            
        Raises:
            BinaryManagerError: If installation fails
        """
        logger.info(f"Installing llama.cpp {version} ({variant})")
        
        # Resolve 'latest' to actual version
        actual_version = version
        if version == "latest":
            try:
                with GitHubClient() as client:
                    actual_version = client.resolve_latest_version()
                logger.info(f"Resolved 'latest' to version {actual_version}")
            except GitHubError as e:
                raise BinaryManagerError(f"Failed to resolve latest version: {e}") from e
        
        # Build download URL
        if source_url:
            download_url = source_url
        else:
            download_url = build_download_url(actual_version, variant)
        
        logger.info(f"Download URL: {download_url}")
        
        # Generate UUID for this installation
        binary_id = uuid4()
        binary_dir = self.bins_dir / str(binary_id)
        
        try:
            # Download and extract
            _, actual_sha256 = download_and_extract(
                url=download_url,
                dest_dir=binary_dir,
                expected_sha256=expected_sha256,
                progress_callback=progress_callback,
            )
            
            # Get release info from GitHub
            github_info = None
            try:
                with GitHubClient() as client:
                    github_info = client.get_release_info(actual_version)
            except GitHubError as e:
                logger.warning(f"Failed to get GitHub release info: {e}")
            
            # Create BinaryVersion model
            binary = BinaryVersion(
                id=binary_id,
                version=actual_version,
                variant=variant,
                download_url=download_url,
                sha256=actual_sha256,
                path=Path(str(binary_id)),  # Relative path
                size_bytes=get_directory_size(binary_dir),
                executables=find_executables(binary_dir),
                github_release_info=github_info,
            )
            
            # Register in registry
            self.registry.add(binary)
            
            # Set as default if requested or first binary
            if set_as_default or self.registry.count() == 1:
                self.registry.set_default(binary_id)
            
            logger.info(f"Installed {actual_version} ({variant}) with UUID {binary_id}")
            return binary
            
        except (DownloadError, RegistryError) as e:
            # Clean up on failure
            if binary_dir.exists():
                shutil.rmtree(binary_dir, ignore_errors=True)
            raise BinaryManagerError(f"Installation failed: {e}", cause=e) from e
    
    def uninstall(self, binary_id: UUID, force: bool = False) -> BinaryVersion:
        """
        Uninstall a binary by UUID.
        
        Removes from registry and deletes files.
        
        Args:
            binary_id: UUID of binary to remove
            force: Force removal even if in use
            
        Returns:
            Removed BinaryVersion
            
        Raises:
            BinaryNotFoundError: If binary not found
            BinaryInUseError: If binary in use and not force
        """
        binary = self.registry.get_by_id(binary_id)
        if binary is None:
            raise BinaryNotFoundError(str(binary_id))
        
        # TODO: Check if in use by any instances (requires instance config scanning)
        # if not force:
        #     instances = self._find_instances_using(binary_id)
        #     if instances:
        #         raise BinaryInUseError(binary_id, instances)
        
        # Remove from registry
        self.registry.remove(binary_id)
        
        # Delete files
        binary_dir = self.bins_dir / str(binary_id)
        if binary_dir.exists():
            shutil.rmtree(binary_dir)
            logger.info(f"Deleted binary directory {binary_dir}")
        
        logger.info(f"Uninstalled {binary.version} ({binary.variant}) UUID {binary_id}")
        return binary
    
    def resolve(self, config: BinaryConfig) -> Optional[BinaryVersion]:
        """
        Resolve binary configuration to installed binary.
        
        Resolution order:
        1. If binary_id is set, lookup by UUID (primary)
        2. If version+variant set, lookup by those (fallback)
        3. If only variant set, use default binary
        4. Return None if not found
        
        Args:
            config: BinaryConfig from instance config
            
        Returns:
            BinaryVersion or None if not found/installed
        """
        # Primary lookup by UUID
        if config.binary_id is not None:
            binary = self.registry.get_by_id(config.binary_id)
            if binary is not None:
                return binary
            logger.warning(f"Binary ID {config.binary_id} not found in registry")
        
        # Fallback lookup by version+variant
        if config.version is not None:
            # Handle 'latest' by finding newest installation
            if config.version == "latest":
                # Find all binaries with matching variant, sort by install date
                matches = [
                    b for b in self.registry.list_all()
                    if b.variant == config.variant
                ]
                if matches:
                    # Return most recently installed
                    return max(matches, key=lambda b: b.installed_at)
            else:
                binary = self.registry.get_by_version(config.version, config.variant)
                if binary is not None:
                    return binary
        
        # Use default
        return self.registry.get_default()
    
    def resolve_server_path(self, config: Optional[BinaryConfig]) -> Optional[Path]:
        """
        Resolve binary config to llama-server.exe path.
        
        Falls back to legacy bin/ if no config or binary not found.
        
        Args:
            config: BinaryConfig from instance config (or None)
            
        Returns:
            Path to llama-server.exe
        """
        # Try new bins/ structure first
        if config is not None:
            binary = self.resolve(config)
            if binary is not None:
                server_path = self.bins_dir / str(binary.id) / "llama-server.exe"
                if server_path.exists():
                    return server_path
        
        # Fall back to legacy bin/
        legacy_path = self.legacy_bin_dir / "llama-server.exe"
        if legacy_path.exists():
            logger.debug("Using legacy bin/ for llama-server.exe")
            return legacy_path
        
        return None
    
    def get(self, binary_id: UUID) -> Optional[BinaryVersion]:
        """Get binary by UUID."""
        return self.registry.get_by_id(binary_id)
    
    def get_by_version(self, version: str, variant: str) -> Optional[BinaryVersion]:
        """Get binary by version and variant."""
        return self.registry.get_by_version(version, variant)
    
    def list_installed(self) -> list[BinaryVersion]:
        """List all installed binaries."""
        return self.registry.list_all()
    
    def get_default(self) -> Optional[BinaryVersion]:
        """Get the default binary."""
        return self.registry.get_default()
    
    def set_default(self, binary_id: UUID) -> bool:
        """Set the default binary by UUID."""
        return self.registry.set_default(binary_id)
    
    def check_for_updates(self, binary_id: UUID) -> Optional[str]:
        """
        Check if a newer version is available.
        
        Args:
            binary_id: UUID of binary to check
            
        Returns:
            Latest version tag if newer, None otherwise
        """
        binary = self.registry.get_by_id(binary_id)
        if binary is None:
            return None
        
        try:
            with GitHubClient() as client:
                latest = client.resolve_latest_version()
            
            # Compare version numbers (assumes b{number} format)
            current_num = int(binary.version.lstrip("b"))
            latest_num = int(latest.lstrip("b"))
            
            if latest_num > current_num:
                return latest
            
        except (GitHubError, ValueError) as e:
            logger.warning(f"Failed to check for updates: {e}")
        
        return None
    
    def migrate_legacy_bin(self) -> Optional[BinaryVersion]:
        """
        Migrate legacy bin/ directory to bins/ structure.
        
        Creates a new UUID-based entry for existing binary.
        Does NOT delete the original bin/ directory.
        
        Returns:
            BinaryVersion for migrated binary, or None if nothing to migrate
        """
        legacy_server = self.legacy_bin_dir / "llama-server.exe"
        
        if not legacy_server.exists():
            logger.debug("No legacy bin/ to migrate")
            return None
        
        logger.info("Migrating legacy bin/ directory...")
        
        # Generate new UUID
        binary_id = uuid4()
        binary_dir = self.bins_dir / str(binary_id)
        
        # Copy files (don't move, keep original for safety)
        shutil.copytree(self.legacy_bin_dir, binary_dir)
        
        # Create BinaryVersion (we don't know the exact version)
        binary = BinaryVersion(
            id=binary_id,
            version="unknown",
            variant="unknown",
            download_url="migrated-from-legacy-bin",
            path=Path(str(binary_id)),
            size_bytes=get_directory_size(binary_dir),
            executables=find_executables(binary_dir),
        )
        
        # Register
        self.registry.add(binary)
        
        logger.info(f"Migrated legacy bin/ to UUID {binary_id}")
        return binary
    
    def prune_unused(self, dry_run: bool = True) -> list[BinaryVersion]:
        """
        Find binaries not used by any instance.
        
        Args:
            dry_run: If True, only report; if False, delete
            
        Returns:
            List of unused binaries (deleted if not dry_run)
        """
        # TODO: Implement instance config scanning
        # For now, return empty list
        logger.warning("Prune not yet implemented (requires instance scanning)")
        return []


# Convenience function for getting binary manager
def get_binary_manager(project_root: Optional[Path] = None) -> BinaryManager:
    """
    Get a BinaryManager instance.
    
    Args:
        project_root: Project root path. If None, auto-detect.
        
    Returns:
        BinaryManager instance
    """
    if project_root is None:
        # Auto-detect from this file's location
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists() and parent.name == "llama-orchestrator":
                project_root = parent
                break
        
        if project_root is None:
            project_root = Path.cwd()
    
    return BinaryManager(project_root)
