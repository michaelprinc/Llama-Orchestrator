"""Focused tests for dashboard layout helpers."""

from unittest.mock import MagicMock, patch

from rich.panel import Panel

from llama_orchestrator.cli import _build_dashboard_layout


def test_dashboard_layout_includes_recent_events_panel() -> None:
    mock_state = MagicMock(pid=1234, status_symbol="●", health_symbol="●", uptime_str="10s")
    mock_state.status.value = "running"
    mock_state.health.value = "healthy"
    mock_config = MagicMock()
    mock_config.server.port = 8001
    mock_config.gpu.backend = "cpu"
    mock_config.model.path.name = "model.gguf"

    with patch("llama_orchestrator.engine.list_instances", return_value={"alpha": mock_state}), \
         patch("llama_orchestrator.config.discover_instances", return_value=[("alpha", None)]), \
         patch("llama_orchestrator.config.get_instance_config", return_value=mock_config), \
         patch("llama_orchestrator.engine.get_recent_events", return_value=[
             {"ts": 1_700_000_000.0, "instance_name": "alpha", "event_type": "started", "message": "Instance started"}
         ]):
        layout = _build_dashboard_layout(events_for="alpha")

    events_panel = layout["events"].renderable
    assert isinstance(events_panel, Panel)
    assert "Recent Events (alpha)" in str(events_panel.title)
    assert "started" in str(events_panel.renderable)