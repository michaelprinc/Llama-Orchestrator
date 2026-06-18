"""Immutable data classes shared across the GUI package.

These dataclasses are imported by multiple GUI modules and are kept in a
dedicated module to reduce context fill during independent refactoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llama_orchestrator.engine.detection import DetectedGpu
from llama_orchestrator.hf_import import (
    DownloadProgress,
    GGUFVariant,
    HuggingFaceRepoRef,
    ImportedModelSelection,
)


@dataclass(frozen=True)
class TableRow:
    """One rendered GUI table row plus raw sort values."""

    name: str
    values: tuple[str, ...]
    sort_values: dict[str, Any]


@dataclass(frozen=True)
class ImportDialogEvent:
    """One worker event consumed by the Hugging Face import dialog."""

    kind: str
    message: str = ""
    repo_ref: HuggingFaceRepoRef | None = None
    variants: list[GGUFVariant] | None = None
    progress: DownloadProgress | None = None
    selection: ImportedModelSelection | None = None


@dataclass(frozen=True)
class GuiRefreshSnapshot:
    """Immutable snapshot of data collected during a full refresh."""

    rows: tuple[TableRow, ...]
    detected_gpus: tuple[DetectedGpu, ...]
    all_tags: tuple[str, ...]
    collected_at: float
