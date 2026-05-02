"""
Pydantic schemas for llama.cpp binary version management.

UUID is the primary identifier for all binaries. Version and variant
are supplementary metadata that can be used for filtering/searching.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator


# Supported Windows variants from llama.cpp releases
WindowsVariant = Literal[
    "win-cpu-x64",
    "win-cpu-arm64",
    "win-vulkan-x64",
    "win-cuda-12.4-x64",
    "win-cuda-13.1-x64",
    "win-hip-radeon-x64",
    "win-sycl-x64",
]

# All supported variants (extensible for future Linux/macOS support)
SupportedVariant = WindowsVariant


class BinaryConfig(BaseModel):
    """
    Binary configuration for instance config.json.
    
    The binary_id (UUID) is the PRIMARY identifier that joins to
    bins/registry.json. Version and variant are optional hints
    for resolution when binary_id is not set.
    """
    
    binary_id: Optional[UUID] = Field(
        default=None,
        description="Primary identifier - UUID of installed binary. Joins to registry.json"
    )
    version: Optional[str] = Field(
        default=None,
        description="llama.cpp version tag (e.g., 'b7572', 'latest'). Used when binary_id is None"
    )
    variant: SupportedVariant = Field(
        default="win-vulkan-x64",
        description="Platform/GPU variant. Used when binary_id is None"
    )
    source_url: Optional[HttpUrl] = Field(
        default=None,
        description="Custom download URL (overrides auto-generated URL)"
    )
    sha256: Optional[str] = Field(
        default=None,
        description="Expected SHA256 checksum for verification"
    )
    
    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, v: Optional[str]) -> Optional[str]:
        """Validate SHA256 format if provided."""
        if v is not None:
            v = v.lower().strip()
            if len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
                raise ValueError("SHA256 must be 64 hex characters")
        return v


class GitHubReleaseInfo(BaseModel):
    """Metadata from GitHub release API."""
    
    tag_name: str = Field(..., description="Release tag (e.g., 'b7572')")
    published_at: Optional[datetime] = Field(default=None, description="Release publish date")
    commit_sha: Optional[str] = Field(default=None, description="Commit SHA of the release")
    html_url: Optional[str] = Field(default=None, description="URL to release page")


class BinaryVersion(BaseModel):
    """
    Installed binary version metadata.
    
    Stored in bins/registry.json and bins/{uuid}/version.json.
    UUID is the primary key for all lookups and joins.
    """
    
    id: UUID = Field(default_factory=uuid4, description="Primary identifier (UUID4)")
    version: str = Field(..., description="llama.cpp version tag (e.g., 'b7572')")
    variant: str = Field(..., description="Platform/GPU variant (e.g., 'win-vulkan-x64')")
    download_url: str = Field(..., description="URL used to download this binary")
    sha256: Optional[str] = Field(default=None, description="Verified SHA256 checksum")
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Installation timestamp (UTC)"
    )
    path: Path = Field(..., description="Relative path to binary directory under bins/")
    size_bytes: Optional[int] = Field(default=None, description="Total size of extracted files")
    executables: list[str] = Field(
        default_factory=list,
        description="List of executable files in the directory"
    )
    github_release_info: Optional[GitHubReleaseInfo] = Field(
        default=None,
        description="Metadata from GitHub release API"
    )
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            Path: str,
            UUID: str,
        }
    
    def get_server_executable(self) -> Path:
        """Get path to llama-server executable."""
        return self.path / "llama-server.exe"
    
    def get_cli_executable(self) -> Path:
        """Get path to llama-cli executable."""
        return self.path / "llama-cli.exe"


class BinaryRegistry(BaseModel):
    """
    Registry of all installed binary versions.
    
    Stored in bins/registry.json. Provides lookup methods
    for finding binaries by UUID (primary) or version+variant.
    """
    
    schema_version: str = Field(default="1.0.0", description="Registry schema version")
    binaries: list[BinaryVersion] = Field(default_factory=list, description="Installed binaries")
    default_binary_id: Optional[UUID] = Field(
        default=None,
        description="UUID of default binary (used when config has no binary section)"
    )
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            Path: str,
            UUID: str,
        }
    
    def get_by_id(self, binary_id: UUID) -> Optional[BinaryVersion]:
        """
        Get binary by UUID (primary lookup method).
        
        This is the main join operation from config.json → registry.json.
        """
        return next((b for b in self.binaries if b.id == binary_id), None)
    
    def get_by_version(self, version: str, variant: str) -> Optional[BinaryVersion]:
        """
        Get binary by version and variant (fallback lookup).
        
        Used when config.json has version+variant but no binary_id.
        Returns the first match; use get_all_by_version for all matches.
        """
        return next(
            (b for b in self.binaries if b.version == version and b.variant == variant),
            None
        )
    
    def get_all_by_version(self, version: str, variant: str) -> list[BinaryVersion]:
        """Get all binaries matching version and variant."""
        return [b for b in self.binaries if b.version == version and b.variant == variant]
    
    def get_default(self) -> Optional[BinaryVersion]:
        """Get the default binary if set."""
        if self.default_binary_id is None:
            return None
        return self.get_by_id(self.default_binary_id)
    
    def add(self, binary: BinaryVersion) -> None:
        """Add a binary to the registry."""
        # Check for duplicate UUID
        if self.get_by_id(binary.id) is not None:
            raise ValueError(f"Binary with ID {binary.id} already exists")
        self.binaries.append(binary)
        
        # Set as default if first binary
        if self.default_binary_id is None:
            self.default_binary_id = binary.id
    
    def remove(self, binary_id: UUID) -> Optional[BinaryVersion]:
        """Remove a binary from the registry by UUID."""
        binary = self.get_by_id(binary_id)
        if binary is not None:
            self.binaries = [b for b in self.binaries if b.id != binary_id]
            # Clear default if removed
            if self.default_binary_id == binary_id:
                self.default_binary_id = self.binaries[0].id if self.binaries else None
        return binary
    
    def set_default(self, binary_id: UUID) -> bool:
        """Set the default binary by UUID."""
        if self.get_by_id(binary_id) is None:
            return False
        self.default_binary_id = binary_id
        return True
    
    def list_versions(self) -> list[tuple[str, str]]:
        """List all unique (version, variant) pairs."""
        seen = set()
        result = []
        for b in self.binaries:
            key = (b.version, b.variant)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result


# Constants for URL building
GITHUB_REPO = "ggml-org/llama.cpp"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"


def build_download_url(version: str, variant: SupportedVariant) -> str:
    """
    Build the download URL for a llama.cpp release.
    
    Args:
        version: Release version tag (e.g., 'b7572')
        variant: Platform variant (e.g., 'win-vulkan-x64')
    
    Returns:
        Full download URL for the release archive
    """
    # Windows uses .zip, Linux/macOS use .tar.gz
    extension = ".zip" if variant.startswith("win-") else ".tar.gz"
    filename = f"llama-{version}-bin-{variant}{extension}"
    return f"https://github.com/{GITHUB_REPO}/releases/download/{version}/{filename}"


def build_cudart_url(version: str, cuda_version: str = "12.4") -> str:
    """
    Build the download URL for CUDA runtime DLLs.
    
    Args:
        version: Release version tag (e.g., 'b7572')
        cuda_version: CUDA version (e.g., '12.4', '13.1')
    
    Returns:
        Full download URL for the CUDA runtime archive
    """
    filename = f"cudart-llama-bin-win-cuda-{cuda_version}-x64.zip"
    return f"https://github.com/{GITHUB_REPO}/releases/download/{version}/{filename}"
