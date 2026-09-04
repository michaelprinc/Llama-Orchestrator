"""Tests for persisted GUI table settings and sorting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from llama_orchestrator.gui_state import (
    GuiSettings,
    SortSpec,
    cycle_sort_order,
    format_sort_heading,
    load_gui_settings,
    save_gui_settings,
    stable_sort_rows,
)


@dataclass(frozen=True)
class DummyRow:
    name: str
    sort_values: dict[str, object]


def test_gui_settings_roundtrip(tmp_path, monkeypatch) -> None:
    """Visible columns and sort order should survive reload."""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.gui_state.get_state_dir", lambda: state_dir)

    settings = GuiSettings(
        visible_columns=("name", "status", "tps"),
        sort_order=(
            SortSpec(column="tps", direction="desc"),
            SortSpec(column="name", direction="asc"),
        ),
        add_model_min_port=8074,
    )

    save_gui_settings(settings)
    loaded = load_gui_settings(["name", "status", "tps", "latency"])

    assert loaded == settings


def test_gui_settings_clamps_invalid_add_model_min_port(tmp_path, monkeypatch) -> None:
    """Persisted Add model port settings should stay in user-space port range."""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.gui_state.get_state_dir", lambda: state_dir)
    (state_dir / "gui_settings.json").write_text(
        '{"visible_columns":["name"],"add_model_min_port":80}',
        encoding="utf-8",
    )

    loaded = load_gui_settings(["name", "status"])

    assert loaded.add_model_min_port == 8001


def test_gui_settings_roundtrips_global_live_metrics_options(tmp_path, monkeypatch) -> None:
    """Live monitoring is global and preserves its bounded administration settings."""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.gui_state.get_state_dir", lambda: state_dir)

    settings = GuiSettings(
        visible_columns=("name",),
        live_metrics_enabled=True,
        live_metrics_poll_interval_seconds=2.5,
        live_metrics_request_timeout_seconds=1.5,
        live_metrics_history_capacity=120,
        live_metrics_max_parallel_polls=3,
    )

    save_gui_settings(settings)

    assert load_gui_settings(["name"]) == settings


def test_gui_settings_rejects_invalid_global_live_metrics_options(tmp_path, monkeypatch) -> None:
    """Malformed saved values cannot create an aggressive monitor on startup."""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.gui_state.get_state_dir", lambda: state_dir)
    (state_dir / "gui_settings.json").write_text(
        """{
            "visible_columns": ["name"],
            "live_metrics_enabled": "yes",
            "live_metrics_poll_interval_seconds": 0.01,
            "live_metrics_request_timeout_seconds": 99,
            "live_metrics_history_capacity": 1,
            "live_metrics_max_parallel_polls": 99
        }""",
        encoding="utf-8",
    )

    loaded = load_gui_settings(["name"])

    assert loaded.live_metrics_enabled is False
    assert loaded.live_metrics_poll_interval_seconds == 1.0
    assert loaded.live_metrics_request_timeout_seconds == 1.0
    assert loaded.live_metrics_history_capacity == 60
    assert loaded.live_metrics_max_parallel_polls == 4


def test_gui_settings_adds_columns_introduced_by_an_upgrade(tmp_path, monkeypatch) -> None:
    """New display columns should appear without discarding existing choices."""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("llama_orchestrator.gui_state.get_state_dir", lambda: state_dir)
    (state_dir / "gui_settings.json").write_text(
        '{"visible_columns":["name","tps"]}',
        encoding="utf-8",
    )

    loaded = load_gui_settings(
        ["name", "tps", "prefill_tps", "latency"],
        auto_add_columns=("prefill_tps",),
    )

    assert loaded.visible_columns == ("name", "tps", "prefill_tps")


def test_cycle_sort_order_promotes_previous_primary_to_secondary() -> None:
    """A new clicked column becomes primary and keeps the old primary as secondary."""

    assert cycle_sort_order([SortSpec(column="name", direction="asc")], "tps") == (
        SortSpec(column="tps", direction="asc"),
        SortSpec(column="name", direction="asc"),
    )


def test_cycle_sort_order_cycles_primary_column_to_desc_then_none() -> None:
    """Repeated clicks on one header should cycle asc -> desc -> none."""

    descending = cycle_sort_order([SortSpec(column="name", direction="asc")], "name")
    cleared = cycle_sort_order(descending, "name")

    assert descending == (SortSpec(column="name", direction="desc"),)
    assert cleared == ()


def test_cycle_sort_order_clears_secondary_sort_on_primary_third_click() -> None:
    """The third click on the primary column should restore the default row order."""

    current = (
        SortSpec(column="tps", direction="desc"),
        SortSpec(column="name", direction="asc"),
    )

    assert cycle_sort_order(current, "tps") == ()


def test_format_sort_heading_marks_primary_and_secondary_columns() -> None:
    """Active headings should show arrows and sort priority."""

    sort_order = (
        SortSpec(column="tps", direction="asc"),
        SortSpec(column="name", direction="desc"),
    )

    assert format_sort_heading("TPS", "tps", sort_order) == "TPS ▲1"
    assert format_sort_heading("Name", "name", sort_order) == "Name ▼2"
    assert format_sort_heading("Status", "status", sort_order) == "Status"


def test_stable_sort_rows_respects_numeric_secondary_sort_and_blank_values() -> None:
    """Rows should sort by typed values, keep blanks last, and preserve stable ties."""

    rows = [
        DummyRow(name="alpha", sort_values={"tps": 10.0, "name": "Alpha"}),
        DummyRow(name="charlie", sort_values={"tps": 20.0, "name": "Charlie"}),
        DummyRow(name="bravo", sort_values={"tps": 20.0, "name": "Bravo"}),
        DummyRow(name="blank", sort_values={"tps": None, "name": "Blank"}),
    ]

    ordered = stable_sort_rows(
        rows,
        [
            SortSpec(column="tps", direction="desc"),
            SortSpec(column="name", direction="asc"),
        ],
        lambda row, column: row.sort_values.get(column),
    )

    assert [row.name for row in ordered] == ["bravo", "charlie", "alpha", "blank"]


def test_stable_sort_rows_supports_date_values() -> None:
    """Date columns should sort by actual date order instead of string formatting."""

    rows = [
        DummyRow(name="newer", sort_values={"created": date(2026, 5, 27)}),
        DummyRow(name="older", sort_values={"created": date(2026, 5, 20)}),
    ]

    ordered = stable_sort_rows(
        rows,
        [SortSpec(column="created", direction="asc")],
        lambda row, column: row.sort_values.get(column),
    )

    assert [row.name for row in ordered] == ["older", "newer"]
