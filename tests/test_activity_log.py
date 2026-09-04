"""Tests for Activity log interactions."""

from unittest.mock import MagicMock


def test_copy_activity_selection_places_only_selected_text_on_clipboard() -> None:
    from llama_orchestrator.gui.activity_log import copy_activity_selection

    activity = MagicMock()
    activity.get.return_value = "Selected log entry"

    assert copy_activity_selection(activity) is True
    activity.get.assert_called_once()
    activity.clipboard_clear.assert_called_once_with()
    activity.clipboard_append.assert_called_once_with("Selected log entry")


def test_copy_activity_selection_leaves_clipboard_unchanged_without_selection(monkeypatch) -> None:
    from llama_orchestrator.gui import activity_log

    class NoSelectionError(Exception):
        pass

    monkeypatch.setattr(activity_log.tk, "TclError", NoSelectionError)
    activity = MagicMock()
    activity.get.side_effect = NoSelectionError()

    assert activity_log.copy_activity_selection(activity) is False
    activity.clipboard_clear.assert_not_called()
    activity.clipboard_append.assert_not_called()


def test_copy_shortcut_stops_window_wide_cli_copy_binding() -> None:
    from llama_orchestrator.gui.activity_log import copy_activity_selection_and_stop

    activity = MagicMock()
    activity.get.return_value = "Selected log entry"

    assert copy_activity_selection_and_stop(activity) == "break"
    activity.clipboard_append.assert_called_once_with("Selected log entry")
