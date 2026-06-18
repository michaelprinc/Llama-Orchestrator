"""Pytest configuration: mock tkinter before any gui imports."""

import sys
from unittest.mock import MagicMock


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
