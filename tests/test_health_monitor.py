"""Targeted tests for health monitor wiring."""

from pathlib import Path
import time
from unittest.mock import patch

from llama_orchestrator.config.schema import HealthcheckConfig, InstanceConfig, ModelConfig, RestartPolicy
from llama_orchestrator.engine.state import HealthStatus, InstanceState, InstanceStatus
from llama_orchestrator.health.checker import HealthCheckResult, HealthCheckStatus
from llama_orchestrator.health.monitor import HealthMonitor, InstanceHealthState


def test_should_restart_uses_restart_policy() -> None:
    """Health monitor should read the schema's restart_policy field."""
    config = InstanceConfig(
        name="monitor-test",
        model=ModelConfig(path=Path("models/test.gguf")),
        healthcheck=HealthcheckConfig(retries=2, start_period=0),
        restart_policy=RestartPolicy(enabled=True, max_retries=3),
    )
    monitor = HealthMonitor()
    health_state = InstanceHealthState(
        name="monitor-test",
        consecutive_failures=2,
        restart_attempts=0,
        in_start_period=False,
    )

    assert monitor._should_restart("monitor-test", config, health_state) is True


def test_check_instance_updates_runtime_and_history() -> None:
    """Health checks should update both legacy state and V2 runtime metadata."""
    config = InstanceConfig(
        name="monitor-test",
        model=ModelConfig(path=Path("models/test.gguf")),
        healthcheck=HealthcheckConfig(retries=2, start_period=0),
        restart_policy=RestartPolicy(enabled=True, max_retries=3),
    )
    state = InstanceState(
        name="monitor-test",
        pid=12345,
        status=InstanceStatus.RUNNING,
        health=HealthStatus.UNKNOWN,
        start_time=time.time() - 30,
    )
    monitor = HealthMonitor()

    with patch("llama_orchestrator.health.monitor.load_state", return_value=state), \
         patch("llama_orchestrator.health.monitor.get_instance_config", return_value=config), \
         patch("llama_orchestrator.health.monitor.check_instance_health", return_value=HealthCheckResult(status=HealthCheckStatus.OK, response_time_ms=25.0)), \
         patch("llama_orchestrator.health.monitor.save_state") as mock_save_state, \
         patch("llama_orchestrator.health.monitor.load_runtime", return_value=None), \
         patch("llama_orchestrator.health.monitor.save_runtime") as mock_save_runtime, \
         patch("llama_orchestrator.health.monitor.record_health_check") as mock_record_health_check:
        monitor._check_instance("monitor-test")

    saved_runtime = mock_save_runtime.call_args.args[0]

    assert mock_save_state.called
    assert saved_runtime.name == "monitor-test"
    assert saved_runtime.pid == 12345
    assert saved_runtime.port == config.server.port
    assert saved_runtime.health == HealthStatus.HEALTHY
    assert saved_runtime.status == InstanceStatus.RUNNING
    assert saved_runtime.last_seen_at is not None
    assert saved_runtime.last_health_ok_at is not None
    mock_record_health_check.assert_called_once()