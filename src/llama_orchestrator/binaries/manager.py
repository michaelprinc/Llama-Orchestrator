"""
Binary manager for llama-orchestrator.

Orchestrates binary installation, removal, and resolution.
UUID is the primary identifier for all operations.
"""

from __future__ import annotations

import hashlib
import json
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
)
from llama_orchestrator.binaries.registry import (
    BinaryRegistryManager,
    RegistryError,
)
from llama_orchestrator.binaries.schema import (
    BinaryConfig,
    BinaryVersion,
    SupportedVariant,
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


def _package_matches_build(package_dir: Path, build_dir: Path) -> bool:
    """Return whether a package manifest points at the selected build tree."""
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file() or not (package_dir / "llama-server.exe").is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    declared_build_bin = manifest.get("buildBinDir")
    if not isinstance(declared_build_bin, str) or not declared_build_bin.strip():
        return False
    try:
        return Path(declared_build_bin).expanduser().resolve() == (build_dir / "bin").resolve()
    except OSError:
        return False


def resolve_local_package_directory(package_dir: Path) -> Path:
    """Resolve a selected package or build directory to a runnable package root.

    The local ROCm workflow keeps CMake outputs in
    ``artifacts/build/<build-id>/bin`` but writes the matching, self-contained
    runtime bundle to ``artifacts/package/<build-id>``.  Registering the raw
    ``bin`` folder loses the ROCm DLL set, so a selected build root (or its
    ``bin`` child) is redirected to the matching packaged bundle.
    """
    selected = Path(package_dir).expanduser().resolve()
    if not selected.is_dir():
        raise BinaryManagerError(f"Local package directory does not exist: {selected}")

    build_dir: Path | None = None
    if (selected / "bin" / "llama-server.exe").is_file():
        build_dir = selected
    elif (
        selected.name.casefold() == "bin"
        and (selected / "llama-server.exe").is_file()
        and selected.parent.parent.name.casefold() == "build"
    ):
        build_dir = selected.parent

    if build_dir is not None:
        package_root = build_dir.parent.parent / "package"
        package_dir = package_root / build_dir.name
        if (package_dir / "llama-server.exe").is_file():
            return package_dir.resolve()

        # Package version IDs may intentionally differ from CMake build IDs.
        # Use the immutable manifest's buildBinDir link instead of guessing a
        # renamed directory, and fail closed if the link is ambiguous.
        linked_packages = []
        if package_root.is_dir():
            for candidate in package_root.iterdir():
                if candidate.is_dir() and _package_matches_build(candidate, build_dir):
                    linked_packages.append(candidate.resolve())
        if len(linked_packages) == 1:
            return linked_packages[0]
        if len(linked_packages) > 1:
            names = ", ".join(str(path) for path in linked_packages)
            raise BinaryManagerError(
                "Multiple packaged runtimes claim the selected build output. "
                f"Resolve the duplicate manifest links before importing: {names}"
            )
        raise BinaryManagerError(
            "The selected build output is not a runnable package. "
            f"Expected its matching packaged runtime at: {package_dir}, or a package "
            "whose manifest.json buildBinDir points to the selected build. "
            "Run Package-RocmRuntime.ps1 first, then select this build folder again "
            "or select the generated package folder."
        )

    if (selected / "llama-server.exe").is_file():
        return selected
    raise BinaryManagerError(f"llama-server.exe not found in local package: {selected}")


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
        insecure: bool = False,
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
            insecure: Disable TLS certificate verification (DANGEROUS)

        Returns:
            BinaryVersion with UUID for the installed binary

        Raises:
            BinaryManagerError: If installation fails
        """
        logger.info("Installing llama.cpp %s (%s)", version, variant)
        if insecure:
            logger.warning("TLS verification DISABLED for binary download")

        # Resolve the newest release that has this backend's actual archive.
        # GitHub's /releases/latest may refer to a source-only semantic release.
        actual_version = version
        download_url = source_url
        if source_url is None:
            try:
                with GitHubClient() as client:
                    if version == "latest":
                        actual_version = client.resolve_latest_version(variant)
                        logger.info(
                            "Resolved 'latest' to binary release %s for %s",
                            actual_version,
                            variant,
                        )
                    download_url = client.get_asset_url(actual_version, variant)
            except GitHubError as e:
                raise BinaryManagerError(f"Failed to locate binary download: {e}") from e

            if download_url is None:
                raise BinaryManagerError(
                    f"llama.cpp {actual_version} does not publish a '{variant}' archive"
                )

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
                verify_tls=not insecure,
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

    def register_local_package(
        self,
        package_dir: Path,
        *,
        version: str,
        variant: str,
        source_url: str | None = None,
    ) -> BinaryVersion:
        """Copy a complete local server package into the UUID-managed catalog.

        A package is copied as a unit so ``llama-server.exe`` always launches
        beside the DLLs it was built and packaged with.  The stored checksum is
        the imported server executable checksum; callers can retain a richer
        build manifest in the source package when available.
        """
        source = resolve_local_package_directory(package_dir)
        if not version.strip() or not variant.strip():
            raise BinaryManagerError("Local package version and variant must not be blank")

        try:
            source.relative_to(self.bins_dir.resolve())
        except ValueError:
            pass
        else:
            raise BinaryManagerError("Register the original package, not a directory already under bins/")

        binary_id = uuid4()
        destination = self.bins_dir / str(binary_id)
        try:
            self.bins_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            binary = BinaryVersion(
                id=binary_id,
                version=version.strip(),
                variant=variant.strip(),
                download_url=source_url or f"local://{source.name}",
                sha256=hashlib.sha256((destination / "llama-server.exe").read_bytes()).hexdigest(),
                path=Path(str(binary_id)),
                size_bytes=get_directory_size(destination),
                executables=find_executables(destination),
            )
            self.registry.add(binary)
            return binary
        except (OSError, RegistryError) as exc:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise BinaryManagerError(f"Local package registration failed: {exc}", cause=exc) from exc

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
                import re
                # Find all standard binaries with matching variant, sort by install date
                matches = [
                    b for b in self.registry.list_all()
                    if b.variant == config.variant and re.match(r"^b\d+$", b.version)
                ]
                if not matches:
                    # Fall back to any binary with matching variant
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
        # An explicit UUID is a reproducibility pin: never substitute another
        # package when it is missing or incomplete.
        if config is not None and config.binary_id is not None:
            binary = self.registry.get_by_id(config.binary_id)
            if binary is None:
                raise BinaryNotFoundError(str(config.binary_id))
            server_path = self.bins_dir / str(binary.id) / "llama-server.exe"
            if not server_path.is_file():
                raise BinaryManagerError(
                    f"Registered binary {config.binary_id} is incomplete: {server_path} is missing"
                )
            return server_path

        # Version/variant and default lookup retain compatibility for legacy
        # configurations that intentionally have no immutable UUID pin.
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
                latest = client.resolve_latest_version(binary.variant)

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
