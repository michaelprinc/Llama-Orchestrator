"""Focused tests for V2 process lifecycle integration."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from llama_orchestrator.config.schema import (
    GpuConfig,
    HealthcheckConfig,
    InstanceConfig,
    LogsConfig,
    ModelConfig,
    ServerConfig,
)
from llama_orchestrator.engine.process import (
    get_instance_status,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus, RuntimeState
from llama_orchestrator.health.checker import HealthCheckResult, HealthCheckStatus


def _sample_config() -> InstanceConfig:
    return InstanceConfig(
        name="test-instance",
        model=ModelConfig(path=Path("models/test.gguf")),
        server=ServerConfig(host="127.0.0.1", port=8001),
        gpu=GpuConfig(backend="cpu", layers=0),
        logs=LogsConfig(max_size_mb=10, rotation=3),
        healthcheck=HealthcheckConfig(timeout=2, retry_delay=0.25, start_period=3),
    )


def test_start_instance_updates_runtime_and_events() -> None:
    config = _sample_config()
    proc = MagicMock()
    proc.pid = 4321
    proc.poll.return_value = None
    healthy_result = HealthCheckResult(status=HealthCheckStatus.OK, response_time_ms=12.5)

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
             patch("llama_orchestrator.health.checker.check_instance_health", return_value=healthy_result) as mock_check_health, \
             patch("llama_orchestrator.engine.process.record_health_check") as mock_record_health, \
             patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
             patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
             patch("llama_orchestrator.engine.process.log_event") as mock_log_event, \
             patch("llama_orchestrator.engine.process.time.sleep"):
            state = start_instance("test-instance")

        assert state.pid == 4321
        assert state.status == InstanceStatus.RUNNING
        assert state.health == HealthStatus.HEALTHY
        mock_lock.assert_called_once()
        saved_runtime = mock_save_runtime.call_args.args[0]
        assert saved_runtime.pid == 4321
        assert saved_runtime.port == 8001
        assert saved_runtime.status == InstanceStatus.RUNNING
        assert saved_runtime.health == HealthStatus.HEALTHY
        assert saved_runtime.last_health_ok_at is not None
        assert saved_runtime.cmdline == "llama-server --port 8001"
        mock_check_health.assert_called_once_with("test-instance", timeout=2.0)
        mock_record_health.assert_called_once()
        assert any(call.kwargs.get("event_type") == "started" for call in mock_log_event.call_args_list)
        assert mock_save_state.called


def test_start_instance_stays_loading_when_readiness_never_succeeds_within_budget() -> None:
    config = _sample_config()
    config.healthcheck = HealthcheckConfig(timeout=1, retry_delay=0.5, start_period=1)
    proc = MagicMock()
    proc.pid = 4321
    proc.poll.return_value = None
    loading_result = HealthCheckResult(
        status=HealthCheckStatus.LOADING,
        response_time_ms=30.0,
        error_message="still loading",
    )

    with TemporaryDirectory() as temp_dir:
        stdout_path = Path(temp_dir) / "stdout.log"
        stderr_path = Path(temp_dir) / "stderr.log"

        class FakeLogHandler:
            def get_file_handles(self):
                return (
                    open(stdout_path, "a", encoding="utf-8"),
                    open(stderr_path, "a", encoding="utf-8"),
                )

        with patch("llama_orchestrator.engine.process.instance_lock", return_value=nullcontext()), \
             patch("llama_orchestrator.engine.process.get_instance_config", return_value=config), \
             patch("llama_orchestrator.engine.process.validate_executable", return_value=(True, "")), \
             patch("llama_orchestrator.engine.process.load_state", return_value=None), \
             patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
             patch("llama_orchestrator.health.ports.validate_port_for_instance", return_value=(True, "ok")), \
             patch("llama_orchestrator.engine.process.build_command", return_value=["llama-server", "--port", "8001"]), \
             patch("llama_orchestrator.engine.process.build_env", return_value={}), \
             patch("llama_orchestrator.engine.process.get_instance_log_handler", return_value=FakeLogHandler()), \
             patch("llama_orchestrator.engine.process.subprocess.Popen", return_value=proc), \
             patch("llama_orchestrator.health.checker.check_instance_health", return_value=loading_result) as mock_check_health, \
             patch("llama_orchestrator.engine.process.record_health_check") as mock_record_health, \
             patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
             patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
             patch("llama_orchestrator.engine.process.log_event"), \
             patch("llama_orchestrator.engine.process.time.sleep"), \
             patch("llama_orchestrator.engine.process.time.monotonic", side_effect=[0.0, 0.0, 1.1]):
            state = start_instance("test-instance")

    assert state.status == InstanceStatus.RUNNING
    assert state.health == HealthStatus.LOADING
    mock_check_health.assert_called_once_with("test-instance", timeout=1.0)
    mock_record_health.assert_called_once_with(
        "test-instance",
        HealthStatus.LOADING,
        response_time_ms=30.0,
        error_message="still loading",
    )
    final_runtime = mock_save_runtime.call_args.args[0]
    assert final_runtime.health == HealthStatus.LOADING
    assert final_runtime.last_health_ok_at is None
    assert mock_save_state.called


def test_start_instance_fails_if_process_exits_during_readiness_wait() -> None:
    config = _sample_config()
    proc = MagicMock()
    proc.pid = 4321
    proc.returncode = 17
    proc.poll.side_effect = [None, None, 17]
    loading_result = HealthCheckResult(
        status=HealthCheckStatus.LOADING,
        response_time_ms=20.0,
        error_message="still loading",
    )

    with TemporaryDirectory() as temp_dir:
        stdout_path = Path(temp_dir) / "stdout.log"
        stderr_path = Path(temp_dir) / "stderr.log"

        class FakeLogHandler:
            def get_file_handles(self):
                return (
                    open(stdout_path, "a", encoding="utf-8"),
                    open(stderr_path, "a", encoding="utf-8"),
                )

        with patch("llama_orchestrator.engine.process.instance_lock", return_value=nullcontext()), \
             patch("llama_orchestrator.engine.process.get_instance_config", return_value=config), \
             patch("llama_orchestrator.engine.process.validate_executable", return_value=(True, "")), \
             patch("llama_orchestrator.engine.process.load_state", return_value=None), \
             patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
             patch("llama_orchestrator.health.ports.validate_port_for_instance", return_value=(True, "ok")), \
             patch("llama_orchestrator.engine.process.build_command", return_value=["llama-server", "--port", "8001"]), \
             patch("llama_orchestrator.engine.process.build_env", return_value={}), \
             patch("llama_orchestrator.engine.process.get_instance_log_handler", return_value=FakeLogHandler()), \
             patch("llama_orchestrator.engine.process.subprocess.Popen", return_value=proc), \
             patch("llama_orchestrator.health.checker.check_instance_health", return_value=loading_result), \
             patch("llama_orchestrator.engine.process.record_health_check") as mock_record_health, \
             patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
             patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
             patch("llama_orchestrator.engine.process.log_event") as mock_log_event, \
             patch("llama_orchestrator.engine.process.time.sleep"):
            try:
                start_instance("test-instance")
            except Exception as exc:
                assert "Process exited with code 17" in str(exc)
            else:
                raise AssertionError("Expected start_instance to fail when process exits during readiness wait")

    assert mock_record_health.call_count == 0
    assert mock_save_state.called
    assert mock_save_runtime.called
    assert any(call.kwargs.get("event_type") == "start_failed" for call in mock_log_event.call_args_list)


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
         patch("llama_orchestrator.health.checker.check_instance_health") as mock_check_health, \
         patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
         patch("llama_orchestrator.engine.process.save_runtime"):
        mock_start_detached.return_value = MagicMock(success=True, pid=2468)

        state = start_instance("test-instance", detach=True)

    assert state.pid == 2468
    assert state.status == InstanceStatus.RUNNING
    mock_start_detached.assert_called_once()
    mock_check_health.assert_not_called()
    assert mock_save_state.called


def test_restart_instance_waits_by_default_and_can_skip_waiting() -> None:
    existing_state = InstanceState(
        name="test-instance",
        pid=1234,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.HEALTHY,
        restart_count=1,
    )
    healthy_state = InstanceState(
        name="test-instance",
        pid=2222,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.HEALTHY,
    )
    loading_state = InstanceState(
        name="test-instance",
        pid=3333,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.LOADING,
    )

    with patch("llama_orchestrator.engine.process.load_state", return_value=existing_state), \
         patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
         patch("llama_orchestrator.engine.process.stop_instance") as mock_stop, \
         patch("llama_orchestrator.engine.process.start_instance", side_effect=[healthy_state, loading_state]) as mock_start, \
         patch("llama_orchestrator.engine.process.save_state") as mock_save_state, \
         patch("llama_orchestrator.engine.process.save_runtime") as mock_save_runtime, \
         patch("llama_orchestrator.engine.process.log_event") as mock_log_event, \
         patch("llama_orchestrator.engine.process.time.sleep"):
        waited = restart_instance("test-instance")
        non_waited = restart_instance("test-instance", wait_for_ready=False)

    assert waited.health == HealthStatus.HEALTHY
    assert non_waited.health == HealthStatus.LOADING
    assert waited.restart_count == 2
    assert non_waited.restart_count == 2
    assert mock_stop.call_count == 2
    assert mock_start.call_args_list[0].args == ("test-instance",)
    assert mock_start.call_args_list[0].kwargs == {"wait_for_ready": True}
    assert mock_start.call_args_list[1].args == ("test-instance",)
    assert mock_start.call_args_list[1].kwargs == {"wait_for_ready": False}
    assert mock_save_state.call_count == 2
    assert mock_save_runtime.call_count == 2
    assert mock_log_event.call_count == 2


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
