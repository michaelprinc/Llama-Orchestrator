"""llama-orchestrator GUI package.

This package consolidates the GUI modules that were previously in the
flat ``gui.py`` file.  The main entry point is still ``gui.py`` for
backward compatibility; it re-exports from the new package.
"""

# Import launch_gui from the legacy gui.py file (still exists)
from pathlib import Path

from llama_orchestrator.gui.app import LlamaOrchestratorGui
from llama_orchestrator.gui.refresh import RefreshController, RenderDiffMixin
from llama_orchestrator.gui.usability import (
    SHORTCUT_REGISTRY,
    configure_status_tags,
    create_progress_bar,
    register_shortcuts,
)

_gui_path = Path(__file__).resolve().parent.parent / "gui.py"
if _gui_path.exists():
    import importlib.util
    _spec = importlib.util.spec_from_file_location("legacy_gui", _gui_path)
    if _spec and _spec.loader:
        _legacy_gui = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_legacy_gui)
        launch_gui = _legacy_gui.launch_gui
    else:
        raise ImportError("Failed to load gui.py module")
else:
    raise ImportError("gui.py not found")

__all__ = [
    "LlamaOrchestratorGui",
    "RefreshController",
    "RenderDiffMixin",
    "SHORTCUT_REGISTRY",
    "configure_status_tags",
    "create_progress_bar",
    "register_shortcuts",
    "launch_gui",
]
