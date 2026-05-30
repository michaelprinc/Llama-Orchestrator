"""CLI integration tests for detach routing, exit codes, and daemon service commands."""

from pathlib import Path

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from llama_orchestrator.cli import app
from llama_orchestrator.cli_exit_codes import ExitCode
from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus


runner = CliRunner()


def _running_state(name: str = "test") -> InstanceState:
    return InstanceState(
        name=name,
        pid=1234,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.LOADING,
    )


def test_up_missing_instance_returns_instance_not_found() -> None:
    with patch("llama_orchestrator.config.get_instance_config", side_effect=FileNotFoundError()):
        result = runner.invoke(app, ["up", "missing-instance"])

    assert result.exit_code == int(ExitCode.INSTANCE_NOT_FOUND)


def test_up_passes_detach_flag_to_engine() -> None:
    config = MagicMock()
    config.name = "test"
    config.display_name = "Test"

    with patch("llama_orchestrator.cli._resolve_instance_token", return_value=("test", config)), \
         patch("llama_orchestrator.engine.validate_executable", return_value=(True, "")), \
         patch("llama_orchestrator.engine.start_instance", return_value=_running_state()) as mock_start_instance:
        result = runner.invoke(app, ["up", "test", "--no-detach"])

    assert result.exit_code == 0
    assert mock_start_instance.call_args.kwargs["detach"] is False


def test_daemon_stop_not_running_returns_standard_code() -> None:
    with patch("llama_orchestrator.daemon.is_daemon_running", return_value=False):
        result = runner.invoke(app, ["daemon", "stop"])

    assert result.exit_code == int(ExitCode.DAEMON_NOT_RUNNING)


def test_config_validate_invalid_returns_standard_code() -> None:
    invalid_result = MagicMock(is_valid=False, issues=[], error_count=1, warning_count=0)

    with patch("llama_orchestrator.config.validate_all_instances", return_value=invalid_result):
        result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == int(ExitCode.CONFIG_INVALID)


def test_describe_accepts_selector_and_uses_display_name_in_title() -> None:
    config = MagicMock()
    config.name = "test"
    config.display_name = "Friendly name"
    config.instance_uid = "550e8400-e29b-41d4-a716-446655440000"
    config.instance_no = "00000001"
    config.source_path = Path("instances/00000001_550e8400-e29b-41d4-a716-446655440000/config.json")
    config.model.path = Path("models/test.gguf")
    config.model.context_size = 4096
    config.model.batch_size = 512
    config.model.threads = 8
    config.server.port = 8001
    config.server.host = "127.0.0.1"
    config.gpu.backend = "cpu"
    config.gpu.device_id = 0
    config.gpu.layers = 0
    config.logs.stdout = "logs/{name}/stdout.log"
    config.logs.stderr = "logs/{name}/stderr.log"

    with patch("llama_orchestrator.cli._resolve_instance_token", return_value=("test", config)):
        result = runner.invoke(app, ["describe", "Friendly name"])

    assert result.exit_code == 0
    assert "Friendly name" in result.stdout


def test_daemon_install_calls_windows_service_helper() -> None:
    with patch("llama_orchestrator.daemon.win_service.install_windows_service") as mock_install:
        result = runner.invoke(app, ["daemon", "install", "--service-name", "test-svc"])

    assert result.exit_code == 0
    mock_install.assert_called_once_with(service_name="test-svc")


def test_health_requires_name_or_all_flag() -> None:
    result = runner.invoke(app, ["health"])

    assert result.exit_code == int(ExitCode.USAGE_ERROR)


def test_binary_info_missing_returns_binary_not_found() -> None:
    registry = MagicMock()
    registry.binaries = []

    with patch("llama_orchestrator.config.get_bins_dir"), \
         patch("llama_orchestrator.binaries.registry.load_registry", return_value=registry):
        result = runner.invoke(app, ["binary", "info", "missing"])

    assert result.exit_code == int(ExitCode.BINARY_NOT_FOUND)