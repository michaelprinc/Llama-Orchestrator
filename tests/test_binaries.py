"""
Unit tests for the llama-orchestrator binaries module.

Tests cover:
- Schema validation (BinaryConfig, BinaryVersion, BinaryRegistry)
- Registry CRUD operations
- GitHub client functionality
- Download URL construction
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from llama_orchestrator.binaries.schema import (
    BinaryConfig,
    BinaryRegistry,
    BinaryVersion,
    GitHubReleaseInfo,
    build_cudart_url,
    build_download_url,
)


# =============================================================================
# Schema Tests
# =============================================================================


class TestBinaryConfig:
    """Tests for BinaryConfig Pydantic model."""

    def test_binary_config_with_uuid(self):
        """Test BinaryConfig with UUID primary key."""
        binary_id = uuid4()
        config = BinaryConfig(
            binary_id=binary_id,
            version="b7572",
            variant="win-vulkan-x64",
        )
        assert config.binary_id == binary_id
        assert config.version == "b7572"
        assert config.variant == "win-vulkan-x64"

    def test_binary_config_uuid_from_string(self):
        """Test that UUID can be parsed from string."""
        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        config = BinaryConfig(binary_id=uuid_str)
        assert isinstance(config.binary_id, UUID)
        assert str(config.binary_id) == uuid_str

    def test_binary_config_optional_fields(self):
        """Test BinaryConfig with optional fields."""
        config = BinaryConfig()
        assert config.binary_id is None
        assert config.version is None
        assert config.variant == "win-vulkan-x64"  # default

    def test_binary_config_with_source_url(self):
        """Test BinaryConfig with custom source URL."""
        config = BinaryConfig(
            version="b7572",
            variant="win-vulkan-x64",
            source_url="https://custom.example.com/binary.zip",
        )
        # source_url is HttpUrl type, convert to string for comparison
        assert str(config.source_url) == "https://custom.example.com/binary.zip"


class TestBinaryVersion:
    """Tests for BinaryVersion Pydantic model."""

    def test_binary_version_creation(self):
        """Test creating a BinaryVersion."""
        binary_id = uuid4()
        now = datetime.now(timezone.utc)
        
        version = BinaryVersion(
            id=binary_id,  # Note: field is 'id', not 'binary_id'
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
            installed_at=now,
            path=Path("bins") / str(binary_id),
        )
        
        assert version.id == binary_id
        assert version.version == "b7572"
        assert version.variant == "win-vulkan-x64"
        assert version.installed_at == now

    def test_binary_version_auto_uuid(self):
        """Test that UUID is auto-generated if not provided."""
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/binary.zip",
            path=Path("bins/test"),
        )
        assert version.id is not None
        assert isinstance(version.id, UUID)

    def test_binary_version_sha256(self):
        """Test BinaryVersion with SHA256 checksum."""
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/binary.zip",
            path=Path("bins/test"),
            sha256="abc123def456789012345678901234567890123456789012345678901234",
        )
        assert version.sha256 == "abc123def456789012345678901234567890123456789012345678901234"


class TestBinaryRegistry:
    """Tests for BinaryRegistry Pydantic model."""

    def test_empty_registry(self):
        """Test creating an empty registry."""
        registry = BinaryRegistry()
        assert registry.binaries == []

    def test_registry_with_binaries(self):
        """Test registry with multiple binaries."""
        v1 = BinaryVersion(
            version="b7572", 
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=Path("bins/v1"),
        )
        v2 = BinaryVersion(
            version="b7571", 
            variant="win-cpu-x64",
            download_url="https://example.com/v2.zip",
            path=Path("bins/v2"),
        )
        
        registry = BinaryRegistry(binaries=[v1, v2])
        assert len(registry.binaries) == 2

    def test_registry_get_by_id(self):
        """Test finding binary by UUID."""
        v1 = BinaryVersion(
            version="b7572", 
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=Path("bins/v1"),
        )
        v2 = BinaryVersion(
            version="b7571", 
            variant="win-cpu-x64",
            download_url="https://example.com/v2.zip",
            path=Path("bins/v2"),
        )
        
        registry = BinaryRegistry(binaries=[v1, v2])
        
        found = registry.get_by_id(v1.id)
        assert found is not None
        assert found.version == "b7572"

    def test_registry_get_by_id_not_found(self):
        """Test get_by_id returns None for unknown UUID."""
        registry = BinaryRegistry()
        assert registry.get_by_id(uuid4()) is None

    def test_registry_get_by_version(self):
        """Test finding binary by version and variant."""
        v1 = BinaryVersion(
            version="b7572", 
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=Path("bins/v1"),
        )
        v2 = BinaryVersion(
            version="b7571", 
            variant="win-cpu-x64",
            download_url="https://example.com/v2.zip",
            path=Path("bins/v2"),
        )
        
        registry = BinaryRegistry(binaries=[v1, v2])
        
        found = registry.get_by_version("b7572", "win-vulkan-x64")
        assert found is not None
        assert found.id == v1.id

    def test_registry_get_by_version_not_found(self):
        """Test get_by_version returns None for unknown version."""
        registry = BinaryRegistry()
        assert registry.get_by_version("b9999", "win-vulkan-x64") is None


class TestGitHubReleaseInfo:
    """Tests for GitHubReleaseInfo model."""

    def test_github_release_info(self):
        """Test GitHubReleaseInfo creation."""
        info = GitHubReleaseInfo(
            tag_name="b7572",  # Note: field is 'tag_name', not 'version'
            published_at=datetime.now(timezone.utc),
        )
        assert info.tag_name == "b7572"


# =============================================================================
# URL Builder Tests
# =============================================================================


class TestBuildDownloadUrl:
    """Tests for download URL construction."""

    def test_build_download_url_vulkan(self):
        """Test building Vulkan download URL."""
        url = build_download_url("b7572", "win-vulkan-x64")
        expected = "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip"
        assert url == expected

    def test_build_download_url_cuda(self):
        """Test building CUDA download URL."""
        url = build_download_url("b7572", "win-cuda-12.4-x64")
        expected = "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-cuda-12.4-x64.zip"
        assert url == expected

    def test_build_download_url_cpu(self):
        """Test building CPU download URL."""
        url = build_download_url("b7572", "win-cpu-x64")
        expected = "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-cpu-x64.zip"
        assert url == expected


class TestBuildCudartUrl:
    """Tests for CUDA runtime download URL construction."""

    def test_build_cudart_url(self):
        """Test building CUDA runtime URL."""
        url = build_cudart_url("b7572", "12.4")
        expected = "https://github.com/ggml-org/llama.cpp/releases/download/b7572/cudart-llama-bin-win-cuda-12.4-x64.zip"
        assert url == expected


# =============================================================================
# Registry Functions Tests
# =============================================================================


class TestRegistryFunctions:
    """Tests for registry CRUD functions."""

    @pytest.fixture
    def temp_bins_dir(self, tmp_path: Path) -> Path:
        """Create a temporary bins directory."""
        bins_dir = tmp_path / "bins"
        bins_dir.mkdir()
        return bins_dir

    def test_load_empty_registry(self, temp_bins_dir: Path):
        """Test loading registry when file doesn't exist."""
        from llama_orchestrator.binaries.registry import load_registry
        
        registry = load_registry(temp_bins_dir)
        assert registry.binaries == []

    def test_save_and_load_registry(self, temp_bins_dir: Path):
        """Test saving and loading registry."""
        from llama_orchestrator.binaries.registry import load_registry, save_registry
        
        # Create a registry with one binary
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=Path("bins/test"),
        )
        registry = BinaryRegistry(binaries=[version])
        
        # Save it
        save_registry(temp_bins_dir, registry)
        
        # Load it back
        loaded = load_registry(temp_bins_dir)
        assert len(loaded.binaries) == 1
        assert loaded.binaries[0].version == "b7572"

    def test_add_and_remove_binary(self, temp_bins_dir: Path):
        """Test adding and removing a binary from registry using save_registry."""
        from llama_orchestrator.binaries.registry import load_registry, save_registry
        
        # Add a binary by loading, modifying, and saving
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=temp_bins_dir / "test",
        )
        registry = load_registry(temp_bins_dir)
        registry.binaries.append(version)
        save_registry(temp_bins_dir, registry)
        
        # Verify it was added
        registry = load_registry(temp_bins_dir)
        assert len(registry.binaries) == 1
        
        # Remove it by filtering
        registry.binaries = [b for b in registry.binaries if b.id != version.id]
        save_registry(temp_bins_dir, registry)
        
        # Verify it was removed
        registry = load_registry(temp_bins_dir)
        assert len(registry.binaries) == 0

    def test_get_binary_by_id(self, temp_bins_dir: Path):
        """Test getting binary by UUID using registry.get_by_id()."""
        from llama_orchestrator.binaries.registry import load_registry, save_registry
        
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=temp_bins_dir / "test",
        )
        registry = BinaryRegistry(binaries=[version])
        save_registry(temp_bins_dir, registry)
        
        loaded = load_registry(temp_bins_dir)
        found = loaded.get_by_id(version.id)
        assert found is not None
        assert found.version == "b7572"

    def test_get_binary_by_version(self, temp_bins_dir: Path):
        """Test getting binary by version and variant using registry.get_by_version()."""
        from llama_orchestrator.binaries.registry import load_registry, save_registry
        
        version = BinaryVersion(
            version="b7572",
            variant="win-vulkan-x64",
            download_url="https://example.com/v1.zip",
            path=temp_bins_dir / "test",
        )
        registry = BinaryRegistry(binaries=[version])
        save_registry(temp_bins_dir, registry)
        
        loaded = load_registry(temp_bins_dir)
        found = loaded.get_by_version("b7572", "win-vulkan-x64")
        assert found is not None
        assert found.id == version.id


# =============================================================================
# Config Integration Tests
# =============================================================================


class TestConfigBinaryIntegration:
    """Tests for binary config integration with InstanceConfig."""

    def test_instance_config_with_binary(self):
        """Test InstanceConfig with binary section."""
        from llama_orchestrator.config.schema import InstanceConfig
        
        binary_id = uuid4()
        config = InstanceConfig(
            name="test-instance",
            binary={
                "binary_id": str(binary_id),
                "version": "b7572",
                "variant": "win-vulkan-x64",
            },
            model={"path": "models/test.gguf"},
        )
        
        assert config.binary is not None
        assert config.binary.binary_id == binary_id
        assert config.binary.version == "b7572"

    def test_instance_config_without_binary(self):
        """Test InstanceConfig without binary section (backward compat)."""
        from llama_orchestrator.config.schema import InstanceConfig
        
        config = InstanceConfig(
            name="test-instance",
            model={"path": "models/test.gguf"},
        )
        
        assert config.binary is None
