"""Focused tests for local package registration and per-instance switching."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from llama_orchestrator.binaries.manager import BinaryManager, BinaryManagerError, BinaryNotFoundError
from llama_orchestrator.binaries.schema import BinaryConfig
from llama_orchestrator.binaries.schema import BinaryVersion
from llama_orchestrator.engine import InstanceStatus


def _make_package(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "llama-server.exe").write_bytes(b"test-server")
    (path / "llama.dll").write_bytes(b"matching-dll")
    return path


def test_register_local_package_copies_complete_bundle_and_records_server_hash(tmp_path: Path) -> None:
    source = _make_package(tmp_path / "candidate")
    manager = BinaryManager(tmp_path / "project")

    binary = manager.register_local_package(
        source,
        version="rocm-r3-gfx1030",
        variant="win-hip-gfx1030-rocm10-r3",
    )

    destination = tmp_path / "project" / "bins" / str(binary.id)
    assert (destination / "llama-server.exe").read_bytes() == b"test-server"
    assert (destination / "llama.dll").read_bytes() == b"matching-dll"
    assert binary.sha256 is not None and len(binary.sha256) == 64
    assert (source / "llama.dll").exists()
    assert manager.resolve_server_path(BinaryConfig(binary_id=binary.id)) == destination / "llama-server.exe"


def test_register_rejects_incomplete_or_already_managed_package(tmp_path: Path) -> None:
    manager = BinaryManager(tmp_path / "project")
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(Exception, match="llama-server.exe"):
        manager.register_local_package(incomplete, version="r3", variant="win-hip-r3")

    managed = _make_package(tmp_path / "project" / "bins" / "managed")
    with pytest.raises(Exception, match="already under bins"):
        manager.register_local_package(managed, version="r3", variant="win-hip-r3")


def test_register_build_root_uses_matching_packaged_runtime_bundle(tmp_path: Path) -> None:
    """A CMake build selection must import its sibling packaged runtime, not bin/."""
    build = tmp_path / "artifacts" / "build" / "candidate-r3"
    _make_package(build / "bin")
    package = _make_package(tmp_path / "artifacts" / "package" / "candidate-r3")
    (package / "amdhip64_7.dll").write_bytes(b"matched-runtime")
    manager = BinaryManager(tmp_path / "project")

    binary = manager.register_local_package(build, version="r3", variant="win-hip-r3")

    destination = tmp_path / "project" / "bins" / str(binary.id)
    assert (destination / "llama-server.exe").read_bytes() == b"test-server"
    assert (destination / "amdhip64_7.dll").read_bytes() == b"matched-runtime"
    assert not (destination / "bin").exists()


def test_register_build_root_requires_matching_packaged_runtime_bundle(tmp_path: Path) -> None:
    build = tmp_path / "artifacts" / "build" / "candidate-r3"
    _make_package(build / "bin")
    manager = BinaryManager(tmp_path / "project")

    with pytest.raises(BinaryManagerError, match="matching packaged runtime"):
        manager.register_local_package(build, version="r3", variant="win-hip-r3")


def test_explicit_missing_binary_id_never_uses_default_or_legacy(tmp_path: Path) -> None:
    manager = BinaryManager(tmp_path / "project")
    legacy = tmp_path / "project" / "bin"
    legacy.mkdir(parents=True)
    (legacy / "llama-server.exe").write_bytes(b"legacy")

    with pytest.raises(BinaryNotFoundError):
        manager.resolve_server_path(BinaryConfig(binary_id=uuid4()))


def test_custom_rocm_variant_is_valid_for_instance_pins() -> None:
    config = BinaryConfig(
        binary_id=uuid4(),
        version="rocm-r3-gfx1030",
        variant="win-hip-gfx1030-rocm10-r3",
    )
    assert config.variant == "win-hip-gfx1030-rocm10-r3"


def test_switch_preserves_non_binary_settings_and_requires_explicit_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llama_orchestrator.binaries import switching

    binary = BinaryVersion(
        version="rocm-r3-gfx1030",
        variant="win-hip-gfx1030-rocm10-r3",
        download_url="local://r3",
        path=Path("unused"),
    )
    config = SimpleNamespace(
        name="test-instance",
        binary=None,
        model=SimpleNamespace(context_size=40000),
        args=["--flash-attn", "auto"],
    )
    saved: list[object] = []

    class Registry:
        def get_server_path(self, _binary_id: object) -> Path:
            return tmp_path / "llama-server.exe"

    class Manager:
        registry = Registry()

        def __init__(self, _root: Path) -> None:
            pass

        def list_installed(self) -> list[BinaryVersion]:
            return [binary]

    (tmp_path / "llama-server.exe").write_bytes(b"server")
    monkeypatch.setattr(switching, "BinaryManager", Manager)
    monkeypatch.setattr(switching, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(switching, "get_instance_config", lambda _name: config)
    monkeypatch.setattr(switching, "save_config", lambda value: saved.append(value))
    monkeypatch.setattr(
        switching,
        "list_instances",
        lambda: {"test-instance": SimpleNamespace(status=InstanceStatus.RUNNING)},
    )

    with pytest.raises(switching.BinarySwitchError, match="restart=True"):
        switching.switch_instance_binary("test-instance", str(binary.id))
    assert saved == []

    restarted: list[str] = []
    monkeypatch.setattr(switching, "restart_instance", lambda name: restarted.append(name))
    selected, did_restart = switching.switch_instance_binary(
        "test-instance", str(binary.id), restart=True
    )
    assert selected.id == binary.id
    assert did_restart is True
    assert config.binary.binary_id == binary.id
    assert config.model.context_size == 40000
    assert config.args == ["--flash-attn", "auto"]
    assert saved == [config]
    assert restarted == ["test-instance"]
