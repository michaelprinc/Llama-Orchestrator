"""Platform-neutral executable naming and resolution (R17).

Covers the leaf naming helper, cross-platform package discovery, and the
start-path acceptance: on the running platform a start resolves the
platform-native binary name (extensionless on POSIX, ``.exe`` on Windows)
and legacy packages created on the other platform stay locatable.
"""

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from llama_orchestrator.naming import (
    CLI_BASE,
    DIFFUSION_RUNNER_BASE,
    SERVER_BASE,
    WINDOWS_EXECUTABLE_SUFFIX,
    executable_candidates,
    executable_name,
    locate_executable,
)


def _expected_name(base: str) -> str:
    return f"{base}{WINDOWS_EXECUTABLE_SUFFIX}" if os.name == "nt" else base


def test_executable_name_is_platform_native() -> None:
    for base in (SERVER_BASE, CLI_BASE, DIFFUSION_RUNNER_BASE):
        assert executable_name(base) == _expected_name(base)


def test_executable_candidates_put_native_first_and_keep_cross_fallback() -> None:
    candidates = executable_candidates(SERVER_BASE)
    assert candidates[0] == executable_name(SERVER_BASE)
    if os.name == "nt":
        assert candidates == (f"{SERVER_BASE}{WINDOWS_EXECUTABLE_SUFFIX}",)
    else:
        assert f"{SERVER_BASE}{WINDOWS_EXECUTABLE_SUFFIX}" in candidates


def test_locate_executable_finds_cross_platform_package(tmp_path: Path) -> None:
    (tmp_path / f"{SERVER_BASE}{WINDOWS_EXECUTABLE_SUFFIX}").write_bytes(b"windows-package")
    assert locate_executable(tmp_path, SERVER_BASE) == tmp_path / f"{SERVER_BASE}{WINDOWS_EXECUTABLE_SUFFIX}"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert locate_executable(empty, SERVER_BASE) is None


def test_locate_executable_prefers_platform_native_name(tmp_path: Path) -> None:
    (tmp_path / f"{SERVER_BASE}{WINDOWS_EXECUTABLE_SUFFIX}").write_bytes(b"cross")
    (tmp_path / executable_name(SERVER_BASE)).write_bytes(b"native")
    assert locate_executable(tmp_path, SERVER_BASE) == tmp_path / executable_name(SERVER_BASE)


def test_legacy_bin_fallback_resolves_native_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R17 acceptance: a start on this platform resolves the native bin/ file.

    On POSIX that means an extensionless ``bin/llama-server``; on Windows the
    same test exercises ``bin/llama-server.exe`` through the same seam.
    """
    from llama_orchestrator.config import loader

    monkeypatch.setattr(loader, "get_project_root", lambda: tmp_path)
    legacy_bin = tmp_path / "bin"
    legacy_bin.mkdir()
    (legacy_bin / executable_name(SERVER_BASE)).write_bytes(b"server")

    assert loader.get_llama_server_path() == legacy_bin / executable_name(SERVER_BASE)


def test_missing_binary_raises_platform_native_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llama_orchestrator.config import loader

    monkeypatch.setattr(loader, "get_project_root", lambda: tmp_path)
    with pytest.raises(FileNotFoundError, match=re.escape(executable_name(SERVER_BASE))):
        loader.get_llama_server_path()


def test_validate_executable_accepts_native_named_server(tmp_path: Path) -> None:
    from llama_orchestrator.engine.command import validate_executable

    server = tmp_path / executable_name(SERVER_BASE)
    server.touch()
    with patch("llama_orchestrator.engine.command.get_llama_server_path", return_value=server):
        valid, message = validate_executable(None)
    assert valid is True
    assert str(server) in message
