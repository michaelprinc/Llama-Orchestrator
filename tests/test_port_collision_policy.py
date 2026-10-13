"""T2.2 / R11: port-collision policy tests.

The health monitor must detect collisions via the structured
``PortCollisionError`` (never message substrings), mark the instance ERROR,
emit an error-level event that ``describe`` surfaces, and let the configured
restart policy (max_retries/backoff) bound further attempts. Automatic
re-porting is opt-in only (``HealthMonitor(reassign_ports_on_collision=True)``).

The "two instances on one port" simulation binds a real loopback socket as
the owner ("instance 1"); "instance 2" goes through the real start / monitor
code paths against a redirected temp state root.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from llama_orchestrator.config.schema import (
    HealthcheckConfig,
    InstanceConfig,
    ModelConfig,
    RestartPolicy,
    ServerConfig,
)
from llama_orchestrator.engine.process import PortCollisionError, ProcessError, start_instance
from llama_orchestrator.engine.state import (
    HealthStatus,
    InstanceState,
    InstanceStatus,
    get_recent_events,
    init_db,
    load_state,
    save_state,
)
from llama_orchestrator.health.monitor import HealthMonitor, InstanceHealthState

MON_NS = "llama_orchestrator.health.monitor"


def _make_config(name: str, port: int) -> InstanceConfig:
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path("models/test.gguf")),
        server=ServerConfig(host="127.0.0.1", port=port),
        healthcheck=HealthcheckConfig(retries=2, start_period=0),
        restart_policy=RestartPolicy(enabled=True, max_retries=3),
    )


def _seed_error_state(name: str, port: int) -> None:
    """Persist what a failed start already wrote (ERROR state row)."""
    save_state(
        InstanceState(
            name=name,
            status=InstanceStatus.ERROR,
            health=HealthStatus.ERROR,
            error_message=f"Port {port} is in use",
        )
    )


@pytest.fixture
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config root and state dir to tmp; init a clean DB."""
    monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("llama_orchestrator.benchmark.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("llama_orchestrator.benchmark.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr("llama_orchestrator.engine.state.get_state_dir", lambda: tmp_path / "state")
    init_db()
    return tmp_path


@pytest.fixture
def occupied_port() -> int:
    """'Instance 1': a real listening socket owning an ephemeral loopback port.

    ``listen`` mirrors a real running llama-server (LISTEN state), which is
    detected even though ``check_port_available`` uses SO_REUSEADDR (a bound
    non-listening socket can slip past it — noted for T3.1).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def test_start_on_occupied_port_raises_structured_collision(
    temp_root: Path, occupied_port: int
) -> None:
    """Instance 2 on instance 1's port gets a PortCollisionError + ERROR state."""
    config = _make_config("inst-b", occupied_port)

    with patch("llama_orchestrator.engine.process.instance_lock"), \
         patch("llama_orchestrator.engine.process.get_instance_config", return_value=config), \
         patch(
             "llama_orchestrator.engine.process.validate_executable", return_value=(True, "")
         ), \
         patch("llama_orchestrator.engine.process.load_state", return_value=None), \
         patch("llama_orchestrator.engine.process.load_runtime", return_value=None), \
         pytest.raises(PortCollisionError) as excinfo:
        start_instance("inst-b", wait_for_ready=False)

    # Structured signal, still a ProcessError for existing callers.
    assert isinstance(excinfo.value, ProcessError)
    assert str(occupied_port) in str(excinfo.value)

    saved = load_state("inst-b")
    assert saved is not None
    assert saved.status is InstanceStatus.ERROR
    assert saved.health is HealthStatus.ERROR
    assert "in use" in saved.error_message


def test_monitor_default_policy_marks_error_emits_event_counts_attempt(
    temp_root: Path, occupied_port: int
) -> None:
    """Default policy: ERROR state + error event + attempt counted; no re-porting."""
    name = "inst-b"
    _seed_error_state(name, occupied_port)
    config = _make_config(name, occupied_port)
    monitor = HealthMonitor()  # reassign_ports_on_collision defaults to False
    health_state = InstanceHealthState(
        name=name, consecutive_failures=2, in_start_period=False
    )

    with patch(
        f"{MON_NS}.restart_instance",
        side_effect=PortCollisionError(name, f"Port {occupied_port} is in use"),
    ), patch(
        "llama_orchestrator.health.ports.suggest_port_for_instance",
        side_effect=AssertionError("re-porting must not run when opt-in is off"),
    ):
        monitor._trigger_restart(name, config, health_state)

    # Restart policy bookkeeping: the collision counts as an attempt.
    assert health_state.restart_attempts == 1
    assert health_state.last_restart_time is not None

    saved = load_state(name)
    assert saved is not None
    assert saved.status is InstanceStatus.ERROR
    assert str(occupied_port) in saved.error_message

    events = get_recent_events(instance_name=name)
    error_events = [
        e for e in events if e["event_type"] == "port_collision" and e["level"] == "error"
    ]
    assert error_events, f"expected an error-level port_collision event, got: {events}"
    assert str(occupied_port) in error_events[0]["message"]


def test_monitor_opt_in_reassigns_port_via_override(
    temp_root: Path, occupied_port: int
) -> None:
    """Opt-in mode: one-shot restart on a free port via config override."""
    name = "inst-b"
    _seed_error_state(name, occupied_port)
    config = _make_config(name, occupied_port)
    monitor = HealthMonitor(reassign_ports_on_collision=True)
    health_state = InstanceHealthState(
        name=name, consecutive_failures=2, in_start_period=False
    )
    new_port = occupied_port + 1

    calls: list[tuple[str, bool, InstanceConfig | None]] = []

    def fake_restart(
        n: str, wait_for_ready: bool = True, config_override: InstanceConfig | None = None
    ) -> InstanceState:
        calls.append((n, wait_for_ready, config_override))
        if len(calls) == 1:
            raise PortCollisionError(n, f"Port {occupied_port} is in use")
        return InstanceState(name=n, status=InstanceStatus.STARTING)

    with patch(f"{MON_NS}.restart_instance", side_effect=fake_restart), \
         patch(
             "llama_orchestrator.health.ports.suggest_port_for_instance",
             return_value=new_port,
         ):
        monitor._trigger_restart(name, config, health_state)

    assert len(calls) == 2
    # First call: the failed restart that hit the collision.
    assert calls[0][0] == name and calls[0][1] is False and calls[0][2] is None
    # Second call: re-port restart with an override on the free port.
    override = calls[1][2]
    assert override is not None
    assert override.server.port == new_port
    assert override.name == name
    # The original config is never mutated.
    assert config.server.port == occupied_port

    events = get_recent_events(instance_name=name)
    reassigned = [e for e in events if e["event_type"] == "port_reassigned"]
    assert reassigned, f"expected a port_reassigned event, got: {events}"
    assert reassigned[0]["meta"]["from"] == occupied_port
    assert reassigned[0]["meta"]["to"] == new_port


def test_describe_shows_collision_event(temp_root: Path, occupied_port: int) -> None:
    """AC: the collision event is visible in describe output."""
    from llama_orchestrator.cli_describe import build_description, format_description_rich

    name = "inst-b"
    _seed_error_state(name, occupied_port)
    config = _make_config(name, occupied_port)
    monitor = HealthMonitor()
    health_state = InstanceHealthState(
        name=name, consecutive_failures=2, in_start_period=False
    )

    with patch(
        f"{MON_NS}.restart_instance",
        side_effect=PortCollisionError(name, f"Port {occupied_port} is in use"),
    ):
        monitor._trigger_restart(name, config, health_state)

    desc = build_description(name, config=config)
    text = format_description_rich(desc)
    assert "port_collision" in text
    assert str(occupied_port) in text


def test_collision_attempts_count_against_max_retries() -> None:
    """Once collision attempts reach max_retries, no further restarts trigger."""
    config = _make_config("inst-c", 8081)  # max_retries=3
    monitor = HealthMonitor()
    health_state = InstanceHealthState(
        name="inst-c",
        consecutive_failures=5,
        restart_attempts=config.restart_policy.max_retries,
        last_restart_time=time.time(),
        in_start_period=False,
    )
    assert monitor._should_restart("inst-c", config, health_state) is False


def test_no_sniffing_or_manual_intervention_paths_remain() -> None:
    """F11/T2.2: no message-sniffing or dead-end 'not implemented' paths left."""
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "llama_orchestrator"
        / "health"
        / "monitor.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "error_msg" not in text, "message-sniffing must be gone"
    assert "Manual intervention" not in text
    assert "not yet implemented" not in text.lower()
