"""Main GUI application class.

This module contains the ``LlamaOrchestratorGui`` class that manages the
desktop interface for instance lifecycle management.

The class is extracted from the legacy ``gui.py`` file.  Only the core
class is included here; supporting dialogs and helper functions have
been moved to separate submodules in this package.

TODO: Full extraction of the ``LlamaOrchestratorGui`` class from the
legacy ``gui.py`` file.  Until then, this module re-exports from
``gui.py`` directly to avoid circular imports.
"""

from __future__ import annotations

# Import directly from the gui.py file using importlib to avoid
# circular imports with the gui/ package.
import importlib.util
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_orchestrator.gui import LlamaOrchestratorGui as _LlamaOrchestratorGui
else:
    _LlamaOrchestratorGui = None

# Resolve the path to gui.py relative to this module
_gui_py_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "gui.py",
)

# Load gui.py as a module
_spec = importlib.util.spec_from_file_location("llama_orchestrator._gui_legacy", _gui_py_path)
if _spec is not None and _spec.loader is not None:
    _gui_legacy = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_gui_legacy)
    LlamaOrchestratorGui = _gui_legacy.LlamaOrchestratorGui
else:
    # Fallback: import from the legacy gui module
    from llama_orchestrator.gui import LlamaOrchestratorGui  # type: ignore[misc, assignment]

__all__ = ["LlamaOrchestratorGui"]
