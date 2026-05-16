"""Focused tests for V2 process lifecycle integration."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from llama_orchestrator.config.schema import GpuConfig, InstanceConfig, LogsConfig, ModelConfig, ServerConfig
from llama_orchestrator.engine.process import get_instance_status, list_instances, start_instance, stop_instance
from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus, RuntimeState


def _sample_config() -> InstanceConfig:
    return InstanceConfig(
        name="test-instance",
        model=ModelConfig(path=Path("models/test.gguf")),
        server=ServerConfig(host="127.0.0.1", port=8001),
        gpu=GpuConfig(backend="cpu", layers=0),
        logs=LogsConfig(max_size_mb=10, rotation=3),
    )


def test_start_instance_updates_runtime_and_events() -> None:
    config = _sample_config()
    proc = MagicMock()
    proc.pid = 4321
    proc.poll.return_value = None

    with TemporaryDirectory() as temp_dir:
        stdout_path = Path(temp_dir) / "stdout.log"
        stderr_path = Path(temp_dir) / "stderr.log"

        class FakeLogHandler:
            def get_file_handles(self):
                return (
                    open(stdout_path, "a", encoding="utf-8"),
                    open(stderr_path, "a", encoding="utf-8"),
                )

        with patch("llama_orchestrator.engine.process.instance_lock", return_value=nullcontext()) as mock_lock, \
             patch("llama_orchestrator.engine.process.get_instance_config", return_value=config), \
             patch("llama_orchestrator.engine.process.validate_executable", return_value=(True, "")), \
             patch("llama_orchestrator.engine.process.load_state", return_value=None), \
             patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
             patch("llama_orchestrator.health.ports.validate_port_for_instance", return_value=(True, "ok")), \
             patch("llama_orchestrator.engine.process.build_command", return_value=["llama-server", "--port", "8001"]), \
             patch("llama_orchestrator.engine.process.build_env", return_value={}), \
             patch("llama_orchestrator.engine.process.get_instance_log_handler", return_value=FakeLogHandler()), \
             patch("llama_orchestrator.engine.process.subprocess.Popen", return_value=proc), \
             patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
             patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
             patch("llama_orchestrator.engine.process.log_event") as mock_log_event:
            state = start_instance("test-instance")

        assert state.pid == 4321
        assert state.status == InstanceStatus.RUNNING
        assert state.health == HealthStatus.LOADING
        mock_lock.assert_called_once()
        saved_runtime = mock_save_runtime.call_args.args[0]
        assert saved_runtime.pid == 4321
        assert saved_runtime.port == 8001
        assert saved_runtime.status == InstanceStatus.RUNNING
        assert saved_runtime.cmdline == "llama-server --port 8001"
        assert any(call.kwargs.get("event_type") == "started" for call in mock_log_event.call_args_list)
        assert mock_save_state.called


def test_start_instance_uses_detached_launcher_when_requested() -> None:
    config = _sample_config()

    with patch("llama_orchestrator.engine.process.instance_lock", return_value=nullcontext()), \
         patch("llama_orchestrator.engine.process.get_instance_config", return_value=config), \
         patch("llama_orchestrator.engine.process.validate_executable", return_value=(True, "")), \
         patch("llama_orchestrator.engine.process.load_state", return_value=None), \
         patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
         patch("llama_orchestrator.health.ports.validate_port_for_instance", return_value=(True, "ok")), \
         patch("llama_orchestrator.engine.process.build_command", return_value=["llama-server", "--port", "8001"]), \
         patch("llama_orchestrator.engine.process.build_env", return_value={}), \
         patch("llama_orchestrator.engine.process.get_instance_log_handler"), \
         patch("llama_orchestrator.engine.process.start_detached") as mock_start_detached, \
         patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
         patch("llama_orchestrator.engine.process.save_runtime"):
        mock_start_detached.return_value = MagicMock(success=True, pid=2468)

        state = start_instance("test-instance", detach=True)

    assert state.pid == 2468
    assert state.status == InstanceStatus.RUNNING
    mock_start_detached.assert_called_once()
    assert mock_save_state.called


def test_stop_instance_uses_runtime_when_legacy_state_missing() -> None:
    runtime = RuntimeState(
        name="runtime-only",
        pid=9876,
        port=8002,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.HEALTHY,
        started_at=123.0,
        restart_attempts=2,
    )

    with patch("llama_orchestrator.engine.process.instance_lock", return_value=nullcontext()), \
         patch("llama_orchestrator.engine.process.load_state", return_value=None), \
         patch("llama_orchestrator.engine.process.load_runtime", side_effect=[runtime, runtime, runtime]), \
            patch("llama_orchestrator.engine.process.is_process_running", return_value=True), \
         patch("llama_orchestrator.engine.process.kill_process_tree", return_value=True), \
         patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
         patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
         patch("llama_orchestrator.engine.process.log_event") as mock_log_event, \
         patch("llama_orchestrator.engine.process.get_log_files", return_value=(Path("stdout.log"), Path("stderr.log"))), \
         patch("builtins.open", MagicMock()):
        state = stop_instance("runtime-only")

    assert state.status == InstanceStatus.STOPPED
    assert state.pid is None
    assert mock_save_state.called
    saved_runtime = mock_save_runtime.call_args.args[0]
    assert saved_runtime.status == InstanceStatus.STOPPED
    assert saved_runtime.pid is None
    assert any(call.kwargs.get("event_type") == "stopped" for call in mock_log_event.call_args_list)


def test_get_instance_status_falls_back_to_runtime() -> None:
    runtime = RuntimeState(
        name="runtime-status",
        pid=1234,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.LOADING,
        started_at=10.0,
        restart_attempts=1,
    )

    with patch("llama_orchestrator.engine.process.load_state", return_value=None), \
         patch("llama_orchestrator.engine.process.load_runtime", return_value=runtime), \
         patch("llama_orchestrator.engine.process.is_process_running", return_value=True):
        state = get_instance_status("runtime-status")

    assert state.pid == 1234
    assert state.status == InstanceStatus.RUNNING
    assert state.restart_count == 1


def test_list_instances_includes_runtime_only_instances() -> None:
    runtime = RuntimeState(
        name="runtime-only",
        pid=5555,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.HEALTHY,
    )

    with patch("llama_orchestrator.engine.process.load_all_states", return_value={}), \
         patch("llama_orchestrator.engine.process.load_all_runtime", return_value={"runtime-only": runtime}), \
         patch("llama_orchestrator.config.discover_instances", return_value=[]), \
         patch("llama_orchestrator.engine.process.is_process_running", return_value=True):
        states = list_instances()

    assert "runtime-only" in states
    assert states["runtime-only"].pid == 5555