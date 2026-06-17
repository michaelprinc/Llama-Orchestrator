"""llama-orchestrator GUI package.

The package entrypoint loads the current GUI implementation from
``gui/app.py`` and re-exports its public API.  The sibling ``gui.py`` module
is retained only as a compatibility fallback while the extraction workstream
continues.
"""

import importlib
import sys
from pathlib import Path

_package_dir = Path(__file__).resolve().parent
_app_path = _package_dir / "app.py"
_fallback_path = _package_dir.parent / "gui.py"

for _source_path in (_app_path, _fallback_path):
    if not _source_path.exists():
        continue
    try:
        _gui_source = _source_path.read_text(encoding="utf-8")
        exec(compile(_gui_source, str(_source_path), "exec"), globals())
        GUI_IMPLEMENTATION_SOURCE = _source_path.name
        if _source_path == _app_path:
            sys.modules[f"{__name__}.app"] = sys.modules[__name__]
        break
    except Exception:
        if _source_path == _fallback_path:
            raise
else:
    raise ImportError("Neither gui/app.py nor gui.py could be loaded")

_app_public_names = {name for name in globals() if not name.startswith("_")}

_refresh = importlib.import_module("llama_orchestrator.gui.refresh")
RefreshController = _refresh.RefreshController
RenderDiffMixin = _refresh.RenderDiffMixin

_usability = importlib.import_module("llama_orchestrator.gui.usability")
SHORTCUT_REGISTRY = _usability.SHORTCUT_REGISTRY
configure_status_tags = _usability.configure_status_tags
create_progress_bar = _usability.create_progress_bar
register_shortcuts = _usability.register_shortcuts

__all__ = sorted(
    _app_public_names
    | {
        "RefreshController",
        "RenderDiffMixin",
        "SHORTCUT_REGISTRY",
        "configure_status_tags",
        "create_progress_bar",
        "register_shortcuts",
    }
)
