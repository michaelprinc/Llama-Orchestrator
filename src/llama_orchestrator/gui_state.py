"""Persisted GUI table preferences for llama-orchestrator."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, TypeVar

from llama_orchestrator.config import get_state_dir

SortDirection = Literal["asc", "desc"]
RowT = TypeVar("RowT")


@dataclass(frozen=True)
class SortSpec:
    """One persisted table sort definition."""

    column: str
    direction: SortDirection = "asc"


@dataclass(frozen=True)
class GuiSettings:
    """Persisted GUI preferences for the main table."""

    visible_columns: tuple[str, ...]
    sort_order: tuple[SortSpec, ...] = ()


def get_gui_settings_path() -> Path:
    """Return the persisted GUI settings path."""

    return get_state_dir() / "gui_settings.json"


def default_gui_settings(valid_columns: tuple[str, ...] | list[str]) -> GuiSettings:
    """Return the default GUI table settings."""

    return GuiSettings(visible_columns=tuple(valid_columns))


def load_gui_settings(valid_columns: tuple[str, ...] | list[str]) -> GuiSettings:
    """Load persisted GUI settings, clamping to known columns only."""

    columns = tuple(valid_columns)
    defaults = default_gui_settings(columns)
    settings_path = get_gui_settings_path()
    if not settings_path.exists():
        return defaults

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults

    return GuiSettings(
        visible_columns=_coerce_visible_columns(data.get("visible_columns"), columns),
        sort_order=_coerce_sort_order(data.get("sort_order"), columns),
    )


def save_gui_settings(settings: GuiSettings) -> Path:
    """Persist GUI settings to the state directory."""

    settings_path = get_gui_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "visible_columns": list(settings.visible_columns),
                "sort_order": [
                    {"column": spec.column, "direction": spec.direction}
                    for spec in settings.sort_order
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return settings_path


def cycle_sort_order(current: tuple[SortSpec, ...] | list[SortSpec], column: str) -> tuple[SortSpec, ...]:
    """Cycle a clicked column through asc -> desc -> none.

    Clicking a different column promotes it to primary ascending sort and keeps the
    previous primary sort as secondary when available.
    """

    current_order = tuple(current[:2])
    primary = current_order[0] if current_order else None
    secondary = current_order[1] if len(current_order) > 1 else None

    if primary and primary.column == column:
        if primary.direction == "asc":
            next_order = [SortSpec(column=column, direction="desc")]
            if secondary is not None:
                next_order.append(secondary)
            return tuple(next_order)
        return ()

    next_order = [SortSpec(column=column, direction="asc")]
    if primary is not None and primary.column != column:
        next_order.append(primary)
    return tuple(next_order[:2])


def format_sort_heading(label: str, column: str, sort_order: tuple[SortSpec, ...] | list[SortSpec]) -> str:
    """Decorate a heading with the current sort arrow and priority."""

    ordered = tuple(sort_order)
    for index, spec in enumerate(ordered):
        if spec.column != column:
            continue
        arrow = "▲" if spec.direction == "asc" else "▼"
        priority = str(index + 1) if len(ordered) > 1 else ""
        return f"{label} {arrow}{priority}"
    return label


def stable_sort_rows(
    rows: list[RowT],
    sort_order: tuple[SortSpec, ...] | list[SortSpec],
    value_getter: Callable[[RowT, str], Any],
) -> list[RowT]:
    """Apply stable multi-column sorting while keeping blank values last."""

    ordered = list(rows)
    for spec in reversed(tuple(sort_order[:2])):
        populated: list[RowT] = []
        blanks: list[RowT] = []
        for row in ordered:
            value = value_getter(row, spec.column)
            if _is_blank_sort_value(value):
                blanks.append(row)
            else:
                populated.append(row)
        populated.sort(
            key=lambda row: _normalize_sort_value(value_getter(row, spec.column)),
            reverse=spec.direction == "desc",
        )
        ordered = [*populated, *blanks]
    return ordered


def _coerce_visible_columns(value: Any, valid_columns: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return valid_columns

    visible: list[str] = []
    seen: set[str] = set()
    for item in value:
        column = str(item)
        if column not in valid_columns or column in seen:
            continue
        visible.append(column)
        seen.add(column)
    return tuple(visible) or valid_columns


def _coerce_sort_order(value: Any, valid_columns: tuple[str, ...]) -> tuple[SortSpec, ...]:
    if not isinstance(value, list):
        return ()

    sort_order: list[SortSpec] = []
    seen: set[str] = set()
    for item in value[:2]:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "")
        direction = item.get("direction")
        if column not in valid_columns or column in seen:
            continue
        if direction not in {"asc", "desc"}:
            continue
        sort_order.append(SortSpec(column=column, direction=direction))
        seen.add(column)
    return tuple(sort_order)


def _is_blank_sort_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip() == "-"
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) == 0
    return False


def _normalize_sort_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _normalize_sort_value(value.value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return value.toordinal()
    if isinstance(value, Path):
        return str(value).casefold()
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_sort_value(item) for item in value)
    return str(value).casefold()