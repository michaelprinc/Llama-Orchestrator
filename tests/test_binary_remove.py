"""R5/F1 tests: `llama-orch binary remove` (delegates to BinaryManager.uninstall).

Acceptance criteria from the implementation plan (R5):
- success path leaves no registry entry and no directory;
- failure path (in use) leaves both intact;
- importing the command module raises nothing.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import llama_orchestrator.cli  # noqa: F401 - R5 AC: importing the command module must not raise
from llama_orchestrator.binaries.manager import (
    BinaryManager,
    BinaryNotFoundError,
)
from llama_orchestrator.binaries.registry import load_registry, save_registry
from llama_orchestrator.binaries.schema import BinaryRegistry, BinaryVersion
from llama_orchestrator.cli import app
from llama_orchestrator.cli_exit_codes import ExitCode
from llama_orchestrator.config.schema import (
    BinaryConfig,
    InstanceConfig,
    ModelConfig,
)

runner = CliRunner()


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Hermetic project root: patch every get_project_root seam to tmp_path."""
    import llama_orchestrator.config as config_pkg
    import llama_orchestrator.config.loader as loader_mod

    root = tmp_path / "project"
    (root / "bins").mkdir(parents=True)

    def _fake_get_project_root() -> Path:
        return root

    monkeypatch.setattr(loader_mod, "get_project_root", _fake_get_project_root)
    monkeypatch.setattr(config_pkg, "get_project_root", _fake_get_project_root)
    return root


def _install_fixture_binary(root: Path) -> BinaryVersion:
    """Create a fake installed binary: registry entry + on-disk package dir."""
    binary_id = uuid4()
    binary_dir = root / "bins" / str(binary_id)
    (binary_dir / "bin").mkdir(parents=True)
    (binary_dir / "llama-server.exe").write_bytes(b"\x00fake\x00")

    binary = BinaryVersion(
        id=binary_id,
        version="b7572",
        variant="win-vulkan-x64",
        download_url="https://example.invalid/b7572.zip",
        path=Path(str(binary_id)),
    )
    save_registry(root / "bins", BinaryRegistry(binaries=[binary]))
    return binary


def _no_pinned_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """Instance config enumeration finds nothing (no pins)."""
    import llama_orchestrator.config.loader as loader_mod

    monkeypatch.setattr(loader_mod, "load_all_instances", lambda: {})


def test_importing_cli_module_raises_nothing() -> None:
    # Covered by the module-level import above; keep explicit for the R5 AC.
    assert hasattr(llama_orchestrator.cli, "binary_remove")


def test_binary_remove_success_removes_registry_entry_and_directory(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_pinned_instances(monkeypatch)
    binary = _install_fixture_binary(project_root)
    binary_dir = project_root / "bins" / str(binary.id)
    assert binary_dir.is_dir()

    result = runner.invoke(app, ["binary", "remove", str(binary.id), "--force"])

    assert result.exit_code == 0, result.output
    # Registry entry gone (persisted to disk).
    assert load_registry(project_root / "bins").get_by_id(binary.id) is None
    # Directory gone.
    assert not binary_dir.exists()


def test_binary_remove_in_use_fails_and_leaves_everything_intact(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import llama_orchestrator.config.loader as loader_mod

    binary = _install_fixture_binary(project_root)
    binary_dir = project_root / "bins" / str(binary.id)
    pinned = InstanceConfig(
        name="pinned",
        model=ModelConfig(path=Path("model.gguf")),
        binary=BinaryConfig(binary_id=binary.id),
    )
    monkeypatch.setattr(loader_mod, "load_all_instances", lambda: {"pinned": pinned})

    # No --force and no input: the in-use pre-check must fire before any prompt.
    result = runner.invoke(app, ["binary", "remove", str(binary.id)])

    assert result.exit_code == int(ExitCode.BINARY_INVALID), result.output
    assert "pinned" in result.output
    # Both registry entry and directory remain intact.
    assert load_registry(project_root / "bins").get_by_id(binary.id) is not None
    assert (binary_dir / "llama-server.exe").is_file()


def test_uninstall_persists_registry_before_deleting_files(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import llama_orchestrator.binaries.manager as manager_mod

    binary = _install_fixture_binary(project_root)
    binary_dir = project_root / "bins" / str(binary.id)
    manager = BinaryManager(project_root)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(manager_mod.shutil, "rmtree", _boom)

    with pytest.raises(RuntimeError, match="simulated disk failure"):
        manager.uninstall(binary.id, force=True)

    # Registry was persisted BEFORE file deletion: entry is already gone from
    # disk while the directory still exists (recoverable state, not a dangling
    # registry entry).
    assert load_registry(project_root / "bins").get_by_id(binary.id) is None
    assert binary_dir.is_dir()


def test_uninstall_missing_binary_raises(project_root: Path) -> None:
    manager = BinaryManager(project_root)
    with pytest.raises(BinaryNotFoundError):
        manager.uninstall(uuid4(), force=True)
