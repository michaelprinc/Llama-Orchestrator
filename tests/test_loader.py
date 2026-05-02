"""
Tests for configuration loader.
"""

import json
import tempfile
from pathlib import Path

import pytest

from llama_orchestrator.config import (
    ConfigLoadError,
    InstanceConfig,
    ModelConfig,
    load_config,
    save_config,
)
from llama_orchestrator.config.loader import (
    discover_instances,
    get_instances_dir,
    get_project_root,
    load_config_from_dict,
)


class TestLoadConfig:
    """Tests for load_config function."""
    
    def test_load_valid_config(self, tmp_path: Path) -> None:
        """Test loading a valid configuration file."""
        config_data = {
            "name": "test-instance",
            "model": {
                "path": "models/test.gguf",
                "context_size": 4096,
            },
            "server": {
                "port": 8001,
            },
        }
        
        config_file = tmp_path / "config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)
        
        config = load_config(config_file)
        
        assert config.name == "test-instance"
        assert config.model.path == Path("models/test.gguf")
        assert config.server.port == 8001
    
    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Test loading a non-existent file."""
        with pytest.raises(ConfigLoadError) as exc_info:
            load_config(tmp_path / "nonexistent.json")
        
        assert "not found" in str(exc_info.value).lower()
    
    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Test loading an invalid JSON file."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{ invalid json }")
        
        with pytest.raises(ConfigLoadError) as exc_info:
            load_config(config_file)
        
        assert "invalid json" in str(exc_info.value).lower()
    
    def test_load_invalid_schema(self, tmp_path: Path) -> None:
        """Test loading a file that doesn't match the schema."""
        config_data = {
            "name": "test",
            # Missing required "model" field
        }
        
        config_file = tmp_path / "config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)
        
        with pytest.raises(ConfigLoadError) as exc_info:
            load_config(config_file)
        
        assert "validation failed" in str(exc_info.value).lower()
    
    def test_load_with_defaults(self, tmp_path: Path) -> None:
        """Test that missing optional fields get default values."""
        config_data = {
            "name": "test",
            "model": {
                "path": "test.gguf",
            },
        }
        
        config_file = tmp_path / "config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f)
        
        config = load_config(config_file)
        
        # Check defaults are applied
        assert config.server.port == 8001
        assert config.server.host == "127.0.0.1"
        assert config.gpu.backend == "cpu"
        assert config.healthcheck.interval == 10


class TestLoadConfigFromDict:
    """Tests for load_config_from_dict function."""
    
    def test_load_valid_dict(self) -> None:
        """Test loading from a valid dictionary."""
        data = {
            "name": "test",
            "model": {"path": "test.gguf"},
        }
        
        config = load_config_from_dict(data)
        assert config.name == "test"
    
    def test_load_invalid_dict(self) -> None:
        """Test loading from an invalid dictionary."""
        with pytest.raises(ConfigLoadError):
            load_config_from_dict({"invalid": "data"})


class TestSaveConfig:
    """Tests for save_config function."""
    
    def test_save_config(self, tmp_path: Path) -> None:
        """Test saving a configuration to a file."""
        config = InstanceConfig(
            name="test",
            model=ModelConfig(path=Path("test.gguf")),
        )
        
        saved_path = save_config(config, tmp_path / "config.json")
        
        assert saved_path.exists()
        
        # Reload and verify
        with open(saved_path) as f:
            data = json.load(f)
        
        assert data["name"] == "test"
        assert data["model"]["path"] == "test.gguf"


class TestDiscoverInstances:
    """Tests for discover_instances function."""
    
    def test_discover_in_empty_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test discovery in empty instances directory."""
        instances_dir = tmp_path / "instances"
        instances_dir.mkdir()
        
        # Mock get_instances_dir to return our temp directory
        monkeypatch.setattr(
            "llama_orchestrator.config.loader.get_instances_dir",
            lambda: instances_dir
        )
        
        result = list(discover_instances())
        assert result == []
    
    def test_discover_with_instances(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test discovery with configured instances."""
        instances_dir = tmp_path / "instances"
        
        # Create two instances
        for name in ["instance-a", "instance-b"]:
            instance_dir = instances_dir / name
            instance_dir.mkdir(parents=True)
            config_file = instance_dir / "config.json"
            config_file.write_text(json.dumps({
                "name": name,
                "model": {"path": "test.gguf"},
            }))
        
        monkeypatch.setattr(
            "llama_orchestrator.config.loader.get_instances_dir",
            lambda: instances_dir
        )
        
        result = list(discover_instances())
        
        assert len(result) == 2
        names = [r[0] for r in result]
        assert "instance-a" in names
        assert "instance-b" in names


class TestProjectPaths:
    """Tests for path helper functions."""
    
    def test_get_project_root(self) -> None:
        """Test that project root is found correctly."""
        root = get_project_root()
        # Should contain pyproject.toml
        assert (root / "pyproject.toml").exists() or root == Path.cwd()
    
    def test_get_instances_dir(self) -> None:
        """Test getting instances directory."""
        instances_dir = get_instances_dir()
        assert instances_dir.name == "instances"
