"""Pytest configuration: mock tkinter before any gui imports."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def pytest_configure(config):
    """Inject tkinter mocks into sys.modules before any test collection."""
    # Prevent tkinter from being imported by any code under test
    _tk_mock = MagicMock()
    _ttk_mock = MagicMock()
    _filedialog_mock = MagicMock()
    _messagebox_mock = MagicMock()
    _colorchooser_mock = MagicMock()
    _simpledialog_mock = MagicMock()
    _font_mock = MagicMock()
    _scrolledtext_mock = MagicMock()

    sys.modules["tkinter"] = _tk_mock
    sys.modules["tkinter.ttk"] = _ttk_mock
    sys.modules["tkinter.filedialog"] = _filedialog_mock
    sys.modules["tkinter.messagebox"] = _messagebox_mock
    sys.modules["tkinter.colorchooser"] = _colorchooser_mock
    sys.modules["tkinter.simpledialog"] = _simpledialog_mock
    sys.modules["tkinter.font"] = _font_mock
    sys.modules["tkinter.scrolledtext"] = _scrolledtext_mock


@pytest.fixture(scope="session", autouse=True)
def ensure_state_db() -> None:
    """R15/D2: init_db() is no longer an import side effect of engine.state.

    The test harness initializes the shared state database explicitly once
    per session, mirroring what every production entry point (CLI callback,
    GUI launch, daemon service) now does.  init_db() is idempotent, so tests
    that re-initialize against an isolated state dir (see temp_root-style
    fixtures) are unaffected.
    """
    from llama_orchestrator.engine.state import init_db

    init_db()
    yield


@pytest.fixture(autouse=True)
def hermetic_llama_server_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R8/F4: no test may depend on an installed llama-server binary.

    Every command-construction path (engine.command.build_command, and via it
    describe_effective_runtime / estimate_instance_memory / benchmark device
    resolution) reaches binary resolution through this single seam. Patching it
    here makes the whole suite hermetic in a clean container; registry and
    resolution-order behavior is covered separately by the binaries tests.
    """
    fake = tmp_path / "llama-server.exe"
    monkeypatch.setattr(
        "llama_orchestrator.engine.command.get_llama_server_path",
        lambda config=None: fake,
    )
