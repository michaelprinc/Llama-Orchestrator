"""
Binary version management for llama-orchestrator.

This module provides:
- Multi-version support for llama.cpp binaries
- UUID-based binary identification and tracking
- Automatic download from GitHub releases
- Registry for installed binary versions

UUID is the primary identifier for all binary lookups and joins
between instance config.json and bins/registry.json.
"""

from llama_orchestrator.binaries.downloader import (
    ChecksumError,
    DownloadError,
    DownloadProgress,
    calculate_sha256,
    download_and_extract,
    download_file,
    extract_archive,
    find_executables,
    get_directory_size,
    verify_checksum,
)
from llama_orchestrator.binaries.github import (
    GitHubClient,
    GitHubError,
    RateLimitError,
    get_download_url,
    get_latest_version,
)
from llama_orchestrator.binaries.manager import (
    BinaryInUseError,
    BinaryManager,
    BinaryManagerError,
    BinaryNotFoundError,
    get_binary_manager,
)
from llama_orchestrator.binaries.registry import (
    BinaryRegistryManager,
    RegistryError,
    load_registry,
    save_registry,
)
from llama_orchestrator.binaries.schema import (
    GITHUB_API_RELEASES_URL,
    GITHUB_RELEASES_URL,
    GITHUB_REPO,
    BinaryConfig,
    BinaryRegistry,
    BinaryVersion,
    GitHubReleaseInfo,
    SupportedVariant,
    WindowsVariant,
    build_cudart_url,
    build_download_url,
)

__all__ = [
    # Schema models
    "BinaryConfig",
    "BinaryVersion",
    "BinaryRegistry",
    "GitHubReleaseInfo",
    "SupportedVariant",
    "WindowsVariant",
    # URL builders
    "build_download_url",
    "build_cudart_url",
    "GITHUB_REPO",
    "GITHUB_RELEASES_URL",
    "GITHUB_API_RELEASES_URL",
    # GitHub client
    "GitHubClient",
    "GitHubError",
    "RateLimitError",
    "get_latest_version",
    "get_download_url",
    # Downloader
    "download_file",
    "download_and_extract",
    "extract_archive",
    "calculate_sha256",
    "verify_checksum",
    "find_executables",
    "get_directory_size",
    "DownloadError",
    "ChecksumError",
    "DownloadProgress",
    # Registry
    "BinaryRegistryManager",
    "RegistryError",
    "load_registry",
    "save_registry",
    # Manager
    "BinaryManager",
    "BinaryManagerError",
    "BinaryNotFoundError",
    "BinaryInUseError",
    "get_binary_manager",
]
