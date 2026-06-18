"""Grid benchmark dialog support utilities and data classes.

This module contains the GridDialogParameterVars dataclass and helper
functions used by GridBenchmarkDialog and KvCacheProfileDialog. These
are extracted from app.py to reduce context fill during independent
refactoring.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import tkinter as tk

from llama_orchestrator.benchmark_grid import (
    DEFAULT_KV_CACHE_PROFILE_IDS,
    GridParameterSpec,
)


@dataclass(frozen=True)
class GridDialogParameterVars:
    """Tk variables backing one Grid benchmark dialog row."""

    spec: GridParameterSpec
    enabled: tk.BooleanVar
    minimum: tk.StringVar
    maximum: tk.StringVar
    step_or_values: tk.StringVar


def format_kv_cache_profile_summary(profile_ids: Sequence[str]) -> str:
    """Return the compact main-grid label for selected KV cache profiles."""
    count = len(tuple(profile_ids))
    if count == 0:
        return "Custom: 0 selected"
    if tuple(profile_ids) == DEFAULT_KV_CACHE_PROFILE_IDS:
        return f"Paired profiles: {count} selected"
    return f"Custom: {count} selected"


def parse_grid_values(text: str, value_type: str) -> tuple[int | float | str | bool, ...]:
    """Parse comma-separated grid values from the dialog."""
    raw_values = [part.strip() for part in text.split(",") if part.strip()]
    if not raw_values:
        raise ValueError("Enter at least one value.")
    parsed: list[int | float | str | bool] = []
    for value in raw_values:
        if value_type == "int":
            parsed.append(int(value))
        elif value_type == "float":
            parsed.append(float(value))
        elif value_type == "bool":
            normalized = value.lower()
            if normalized in {"1", "true", "yes", "on"}:
                parsed.append(True)
            elif normalized in {"0", "false", "no", "off"}:
                parsed.append(False)
            else:
                raise ValueError(f"Invalid boolean value: {value}")
        else:
            parsed.append(value)
    return tuple(parsed)


def parse_grid_number(text: str, value_type: str) -> int | float | None:
    """Parse an optional numeric grid bound."""
    value = text.strip()
    if not value:
        return None
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    raise ValueError(f"{value_type} is not a numeric grid type.")


def _default_grid_values_for_spec(value: int | float | str | bool | None) -> str:
    if isinstance(value, bool):
        return "false,true" if value is False else "true,false"
    if isinstance(value, float):
        return f"{value:g}"
    if value is None:
        return ""
    return str(value)


def _default_grid_bound(value: int | float | str | bool | None) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return ""


def _default_step_or_values(spec: GridParameterSpec) -> str:
    if spec.value_type == "int":
        return "1"
    if spec.value_type == "float":
        return "0.1"
    if spec.choices:
        return ",".join(
            str(choice).lower() if isinstance(choice, bool) else str(choice)
            for choice in spec.choices
        )
    return _default_grid_values_for_spec(spec.default)


def _format_grid_values(values: Sequence[int | float | str | bool]) -> str:
    return ",".join(
        str(value).lower() if isinstance(value, bool) else str(value)
        for value in values
    )


def _grid_dialog_status(spec: GridParameterSpec) -> str:
    if spec.read_only:
        return "read-only"
    if not spec.execution_supported:
        return "disabled"
    return "ready"


def _grid_spec_label(spec: GridParameterSpec) -> str:
    return spec.display_name or spec.name
