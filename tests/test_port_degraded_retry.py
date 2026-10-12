"""T3.1 / R13: port utilities degrade loudly + TOCTOU-aware spawn path.

Covered:
  * owner lookups that hit AccessDenied (or an unattributable LISTEN
    connection) report ``degraded=True`` with a one-time warning, and the
    flag propagates into ``PortInfo``;
  * ``check_port_available`` behavior on free / listening / bound-not-
    listening owners (the Linux kernel blind spot is documented, not hidden);
  * ``acquire_port_for_spawn`` retry loop: just-freed ports are waited for,
    still-held ports time out to False;
  * start path wiring: a restart whose previous owner still holds the socket
    waits for release instead of racing it; an unbindable port at spawn time
    raises ``PortCollisionError``; a child that dies immediately while the
    port is now held by someone else is classified as a TOCTOU collision.
"""

from __future__ import annotations

import logging
import os
import socket
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

from llama_orchestrator.config.schema import (
    HealthcheckConfig,
    InstanceConfig,
    ModelConfig,
    RestartPolicy,
    ServerConfig,
)
from llama_orchestrator.engine.process import PortCollisionError, ProcessError, start_instance
from llama_orchestrator.engine.state import init_db
from llama_orchestrator.health import ports as ports_mod


@pytest.fixture(autouse=True)
def reset_degraded_warning():
    """R13: the one-time degraded warning is per-process; keep tests isolated."""
    ports_mod._degraded_warned = False
    yield
    ports_mod._degraded_warned = False


@pytest.fixture
def temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config root and state dir to tmp; init a clean DB."""
    monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("llama_orchestrator.benchmark.get_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        "llama_orchestrator.benchmark.get_state_dir", lambda: tmp_path / "state"
    )
    monkeypatch.setattr("llama_orchestrator.engine.state.get_state_dir", lambda: tmp_path / "state")
    init_db()
    return tmp_path


def _free_port() -> int:
    """An ephemeral loopback port that is free after the probe socket closes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _listening_socket() -> tuple[socket.socket, int]:
    """A loopback listener (this process is its owner)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _denied_net_connections(*_args, **_kwargs):
    raise psutil.AccessDenied(0, "denied")


class TestOwnerLookupDegradation:
    def test_access_denied_reports_none_and_degraded(self):
        with patch.object(ports_mod.psutil, "net_connections", side_effect=_denied_net_connections):
            owner, degraded = ports_mod.get_port_owner(_free_port())
        assert owner is None
        assert degraded is True

    def test_access_denied_warns_exactly_once(self, caplog: pytest.LogCaptureFixture):
        with (
            patch.object(ports_mod.psutil, "net_connections", side_effect=_denied_net_connections),
            caplog.at_level(logging.WARNING, logger="llama_orchestrator.health.ports"),
        ):
            ports_mod.get_port_owner(_free_port())
            ports_mod.get_port_owner(_free_port())
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "degraded" in warnings[0].message

    def test_unattributable_listen_connection_degraded(self):
        port = _free_port()
        fake_conn = SimpleNamespace(
            laddr=SimpleNamespace(family=2, address="127.0.0.1", port=port),
            status="LISTEN",
            pid=None,
        )
        with patch.object(ports_mod.psutil, "net_connections", return_value=[fake_conn]):
            owner, degraded = ports_mod.get_port_owner(port)
        assert degraded is True
        assert owner is not None
        assert owner["pid"] is None

    def test_live_listener_not_degraded(self):
        sock, port = _listening_socket()
        try:
            owner, degraded = ports_mod.get_port_owner(port)
        finally:
            sock.close()
        assert degraded is False
        assert owner is not None
        assert owner["pid"] == os.getpid()

    def test_free_port_not_degraded(self):
        owner, degraded = ports_mod.get_port_owner(_free_port())
        assert owner is None
        assert degraded is False


class TestDegradedPropagation:
    """AC: the degraded flag propagates into PortInfo."""

    def test_degraded_flag_propagates_into_port_info(
        self, monkeypatch: pytest.MonkeyPatch, temp_root: Path
    ):
        port = _free_port()
        monkeypatch.setattr(ports_mod, "check_port_available", lambda p, h="127.0.0.1": False)
        with patch.object(
            ports_mod.psutil, "net_connections", side_effect=_denied_net_connections
        ):
            info = ports_mod.get_port_info(port)
        assert info.is_available is False
        assert info.degraded is True
        assert info.owner_pid is None

    def test_validate_message_says_degraded(
        self, monkeypatch: pytest.MonkeyPatch, temp_root: Path
    ):
        port = _free_port()
        monkeypatch.setattr(ports_mod, "check_port_available", lambda p, h="127.0.0.1": False)
        with patch.object(
            ports_mod.psutil, "net_connections", side_effect=_denied_net_connections
        ):
            valid, msg = ports_mod.validate_port_for_instance(port, "inst-deg")
        assert valid is False
        assert "degraded" in msg


class TestCheckPortAvailable:
    def test_free_port_available(self):
        assert ports_mod.check_port_available(_free_port()) is True

    def test_listening_owner_unavailable(self):
        sock, port = _listening_socket()
        try:
            assert ports_mod.check_port_available(port) is False
        finally:
            sock.close()

    def test_bound_not_listening_is_documented_blind_spot(self):
        """R13: a bound-but-not-listening socket is invisible to /proc/net/tcp,
        so the SO_REUSEADDR probe reports the port available. Empirically
        verified on this platform: a second bind+listen on top of it succeeds
        and reliably receives all connections, which matches what llama-server
        does — the spawn is not harmed. The limitation is documented in
        ``check_port_available`` rather than detected (impossible without ptrace)."""
        owner = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        owner.bind(("127.0.0.1", 0))
        try:
            port = owner.getsockname()[1]
            assert ports_mod.check_port_available(port) is True
        finally:
            owner.close()


class TestAcquirePortForSpawn:
    """AC: the retry loop for just-freed ports is covered by test."""

    def test_free_port_returns_immediately(self):
        port = _free_port()
        t0 = time.monotonic()
        assert ports_mod.acquire_port_for_spawn(port, timeout=2.0) is True
        assert time.monotonic() - t0 < 0.5

    def test_just_freed_port_retry_waits_then_succeeds(self):
        sock, port = _listening_socket()
        release_at = time.monotonic() + 0.4

        def releaser():
            while time.monotonic() < release_at:
                time.sleep(0.05)
            sock.close()

        t = threading.Thread(target=releaser, daemon=True)
        t.start()
        try:
            t0 = time.monotonic()
            assert (
                ports_mod.acquire_port_for_spawn(port, timeout=3.0, check_interval=0.05) is True
            )
            elapsed = time.monotonic() - t0
        finally:
            t.join(timeout=2.0)

        # It waited for the release instead of racing it or failing early.
        assert elapsed >= 0.3

    def test_still_held_times_out_false(self):
        sock, port = _listening_socket()
        try:
            assert (
                ports_mod.acquire_port_for_spawn(port, timeout=0.4, check_interval=0.05) is False
            )
        finally:
            sock.close()


def _make_config(name: str, port: int) -> InstanceConfig:
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path("models/test.gguf")),
        server=ServerConfig(host="127.0.0.1", port=port),
        healthcheck=HealthcheckConfig(retries=2, start_period=0),
        restart_policy=RestartPolicy(enabled=True, max_retries=3),
        args=[],
    )


def _patch_start_prereqs(monkeypatch: pytest.MonkeyPatch, config: InstanceConfig) -> None:
    """Standard hermetic start_instance wiring (see T2.3 test file)."""

    class _NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("llama_orchestrator.engine.process.instance_lock", lambda *a, **k: _NoLock())
    monkeypatch.setattr(
        "llama_orchestrator.engine.process.validate_executable", lambda config: (True, "")
    )
    monkeypatch.setattr("llama_orchestrator.engine.process.load_state", lambda name: None)
    monkeypatch.setattr("llama_orchestrator.engine.process.load_runtime", lambda name: None)


class TestStartPathRetryWiring:
    def test_restart_waits_for_previous_owner_release(
        self, monkeypatch: pytest.MonkeyPatch, temp_root: Path
    ):
        """Restart race: the runtime table says this instance owns the port and
        its previous owner (here: the test process) still holds the socket.
        start_instance must wait for release (real acquire loop), not fail at
        pre-check and not spawn into a held port."""
        sock, port = _listening_socket()
        config = _make_config("inst-retry", port)
        _patch_start_prereqs(monkeypatch, config)

        # Runtime row makes validation pass via "already owned by this instance".
        monkeypatch.setattr(
            ports_mod,
            "load_all_runtime",
            lambda: {"inst-retry": SimpleNamespace(port=port, pid=os.getpid())},
        )

        def releaser():
            time.sleep(0.4)
            sock.close()

        t = threading.Thread(target=releaser, daemon=True)
        t.start()
        try:
            t0 = time.monotonic()
            with pytest.raises(ProcessError) as excinfo:
                start_instance("inst-retry", wait_for_ready=False, config_override=config)
            elapsed = time.monotonic() - t0
        finally:
            t.join(timeout=2.0)

        # The failure is the generic fake-binary spawn error, NOT a collision:
        # the retry loop waited for the port to become bindable first.
        assert not isinstance(excinfo.value, PortCollisionError)
        assert elapsed >= 0.3

    def test_unbindable_at_spawn_raises_collision(
        self, monkeypatch: pytest.MonkeyPatch, temp_root: Path
    ):
        """Pre-check passes (TOCTOU: someone binds in the window), but the port
        is still not bindable at spawn time -> fail closed as a collision."""
        sock, port = _listening_socket()
        config = _make_config("inst-unbind", port)
        _patch_start_prereqs(monkeypatch, config)

        monkeypatch.setattr(
            "llama_orchestrator.health.ports.validate_port_for_instance",
            lambda *a, **k: (True, "ok"),
        )
        monkeypatch.setattr(
            "llama_orchestrator.health.ports.acquire_port_for_spawn",
            lambda *a, **k: False,
        )

        try:
            with pytest.raises(PortCollisionError) as excinfo:
                start_instance("inst-unbind", wait_for_ready=False, config_override=config)
            assert "not bindable" in str(excinfo.value)
        finally:
            sock.close()

    def test_immediate_exit_on_held_port_classified_as_collision(
        self, monkeypatch: pytest.MonkeyPatch, temp_root: Path, tmp_path: Path
    ):
        """TOCTOU loser: pre-check said free, the child spawned and died
        immediately, and the port is now held by someone else -> collision,
        not a raw exit code."""
        sock, port = _listening_socket()
        config = _make_config("inst-toc", port)
        _patch_start_prereqs(monkeypatch, config)

        monkeypatch.setattr(
            "llama_orchestrator.health.ports.validate_port_for_instance",
            lambda *a, **k: (True, "ok"),
        )
        monkeypatch.setattr(
            "llama_orchestrator.health.ports.acquire_port_for_spawn",
            lambda *a, **k: True,
        )

        # A real executable that exits immediately with a non-zero code.
        script = tmp_path / "fake-llama-server"
        script.write_text("#!/bin/sh\nexit 7\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(
            "llama_orchestrator.engine.command.get_llama_server_path",
            lambda config=None: script,
        )

        try:
            with pytest.raises(PortCollisionError) as excinfo:
                start_instance("inst-toc", wait_for_ready=False, config_override=config)
            message = str(excinfo.value)
            assert "TOCTOU collision" in message
            assert "held by PID" in message
        finally:
            sock.close()
