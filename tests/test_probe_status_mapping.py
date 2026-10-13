"""R12/F6: table-driven health status mapping per probe type.

Acceptance criteria (LO_IMPLEMENTATION_PLAN_2026-09-05, R12):

* Probe-failure classification uses structured fields (status code, error
  kind) instead of message substrings.
* Table-driven tests cover (503, timeout, refused, bad-body-200,
  expected_body mismatch) per probe type.
* Legacy 200-without-JSON is classified LOADING, never OK.

The grep gate ("no ``in message_lower`` branches left in checker.py") is
verified separately by the T2.1 verification command.
"""

from __future__ import annotations

import asyncio
import socket as socket_module
import stat
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from llama_orchestrator.config.schema import (
    HealthcheckConfig,
    InstanceConfig,
    ModelConfig,
)
from llama_orchestrator.health import probes as probes_module
from llama_orchestrator.health.checker import (
    HealthCheckResult,
    HealthCheckStatus,
    _probe_result_to_health_check_result,
    check_health,
    classify_probe_outcome,
)
from llama_orchestrator.health.client_pool import check_one_async
from llama_orchestrator.health.probes import (
    ERROR_KIND_BODY_MISMATCH,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNREACHABLE,
    CustomProbe,
    HTTPProbe,
    ProbeExecutionMode,
    ProbeResult,
    TCPProbe,
)

# ---------------------------------------------------------------------------
# Pure classifier table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("success", "status_code", "error_kind", "expected"),
    [
        # success always wins
        (True, None, None, HealthCheckStatus.OK),
        (True, 200, ERROR_KIND_TIMEOUT, HealthCheckStatus.OK),
        # 503 means "up but still loading" regardless of kind
        (False, 503, None, HealthCheckStatus.LOADING),
        (False, 503, ERROR_KIND_TIMEOUT, HealthCheckStatus.LOADING),
        # structured kinds
        (False, None, ERROR_KIND_TIMEOUT, HealthCheckStatus.TIMEOUT),
        (False, None, ERROR_KIND_UNREACHABLE, HealthCheckStatus.UNREACHABLE),
        (False, 200, ERROR_KIND_BODY_MISMATCH, HealthCheckStatus.ERROR),
        # non-loading status codes with no kind fail closed
        (False, 404, None, HealthCheckStatus.ERROR),
        (False, 500, None, HealthCheckStatus.ERROR),
        # custom-probe exit codes are not HTTP loading codes
        (False, 3, None, HealthCheckStatus.ERROR),
        # no structured info at all (legacy result) fails closed to ERROR
        (False, None, None, HealthCheckStatus.ERROR),
    ],
)
def test_classify_probe_outcome_table(
    success: bool,
    status_code: int | None,
    error_kind: str | None,
    expected: HealthCheckStatus,
) -> None:
    """The classifier is a pure function of (success, status_code, error_kind)."""
    assert classify_probe_outcome(success, status_code, error_kind) is expected


def test_mapper_uses_structured_fields_not_message() -> None:
    """A message that *looks* like one failure kind cannot override the kind."""
    # Legacy-shaped result: no kind recorded → ERROR even though the message
    # contains every sniffed token.
    legacy = ProbeResult(
        success=False,
        response_time_ms=1.0,
        message="timeout connection failed refused unreachable",
    )
    assert _probe_result_to_health_check_result(legacy).status is HealthCheckStatus.ERROR

    # Same message with a structured kind → the kind decides.
    typed = ProbeResult(
        success=False,
        response_time_ms=1.0,
        message="timeout connection failed refused unreachable",
        error_kind=ERROR_KIND_TIMEOUT,
    )
    assert _probe_result_to_health_check_result(typed).status is HealthCheckStatus.TIMEOUT


# ---------------------------------------------------------------------------
# Loopback HTTP server fixture (no external network)
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Serves a fixed status/body; class attributes are set per test."""

    response_status = 200
    response_body = b'{"status": "ok"}'
    content_type = "application/json"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self.send_response(self.response_status)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args: object) -> None:  # silence request logging
        pass


@pytest.fixture()
def http_server():
    """Start a loopback HTTP server on an ephemeral port; yield (host, port)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _configure_server(status: int, body: bytes, content_type: str = "application/json") -> None:
    _Handler.response_status = status
    _Handler.response_body = body
    _Handler.content_type = content_type


# ---------------------------------------------------------------------------
# HTTPProbe (real loopback server + mocked transport exceptions)
# ---------------------------------------------------------------------------


class _RaisingClient:
    """Stands in for httpx.Client; its get() raises the given exception."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def __enter__(self) -> _RaisingClient:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def get(self, url: str, **kwargs: object) -> None:
        raise self._exc


def _http_probe_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> HTTPProbe:
    monkeypatch.setattr(probes_module.httpx, "Client", lambda *a, **k: _RaisingClient(exc))
    return HTTPProbe(path="/health", expected_status=[200])


def test_http_probe_503_is_loading(http_server: tuple[str, int]) -> None:
    """503 (llama.cpp still loading) → structured status_code decides LOADING."""
    host, port = http_server
    _configure_server(503, b'{"status": "loading model"}')

    result = HTTPProbe(path="/health", expected_status=[200]).check(host, port)

    assert result.success is False
    assert result.status_code == 503
    assert result.error_kind is None
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.LOADING
    legacy = _probe_result_to_health_check_result(result)
    assert legacy.status is HealthCheckStatus.LOADING


def test_http_probe_ok_is_ok(http_server: tuple[str, int]) -> None:
    host, port = http_server
    _configure_server(200, b'{"status": "ok"}')

    result = HTTPProbe(path="/health", expected_status=[200]).check(host, port)

    assert result.success is True
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.OK


def test_http_probe_expected_body_mismatch_is_error(http_server: tuple[str, int]) -> None:
    """expected_body mismatch → structured kind BODY_MISMATCH → ERROR."""
    host, port = http_server
    _configure_server(200, b'{"status": "ok"}')

    result = HTTPProbe(path="/health", expected_status=[200], expected_body="ready").check(host, port)

    assert result.success is False
    assert result.status_code == 200
    assert result.error_kind is ERROR_KIND_BODY_MISMATCH
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.ERROR


def test_http_probe_timeout_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _http_probe_raising(monkeypatch, httpx.ReadTimeout("read timed out"))

    result = probe.check("127.0.0.1", 9)

    assert result.success is False
    assert result.status_code is None
    assert result.error_kind is ERROR_KIND_TIMEOUT
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.TIMEOUT


def test_http_probe_refused_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = _http_probe_raising(monkeypatch, httpx.ConnectError("connection refused"))

    result = probe.check("127.0.0.1", 9)

    assert result.success is False
    assert result.error_kind is ERROR_KIND_UNREACHABLE
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.UNREACHABLE


# ---------------------------------------------------------------------------
# Legacy check_health flow (default llama.cpp /health endpoint)
# ---------------------------------------------------------------------------


def test_legacy_200_without_json_is_loading_not_ok(http_server: tuple[str, int]) -> None:
    """F6 fix: HTTP 200 with a non-JSON body is LOADING, never OK."""
    host, port = http_server
    _configure_server(200, b"loading model weights... please wait", "text/plain")

    result = check_health(host, port, "/health", timeout=5.0)

    assert result.status is HealthCheckStatus.LOADING
    assert result.is_healthy is False
    assert result.error_message is not None and "HTTP 200 without valid JSON" in result.error_message


def test_legacy_503_is_loading(http_server: tuple[str, int]) -> None:
    host, port = http_server
    _configure_server(503, b'{"status": "loading model"}')

    result = check_health(host, port, "/health", timeout=5.0)

    assert result.status is HealthCheckStatus.LOADING


def test_legacy_ok_still_ok(http_server: tuple[str, int]) -> None:
    host, port = http_server
    _configure_server(200, b'{"status": "ok"}')

    result = check_health(host, port, "/health", timeout=5.0)

    assert result.status is HealthCheckStatus.OK
    assert result.is_healthy is True


def test_legacy_timeout_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probes_module.httpx, "Client", lambda *a, **k: _RaisingClient(httpx.ReadTimeout("timed out"))
    )

    result = check_health("127.0.0.1", 9, "/health", timeout=5.0)

    assert result.status is HealthCheckStatus.TIMEOUT


def test_legacy_refused_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probes_module.httpx, "Client", lambda *a, **k: _RaisingClient(httpx.ConnectError("refused"))
    )

    result = check_health("127.0.0.1", 9, "/health", timeout=5.0)

    assert result.status is HealthCheckStatus.UNREACHABLE


# ---------------------------------------------------------------------------
# TCPProbe (loopback; 503 / bad-body-200 / expected_body mismatch are N/A —
# TCP has no HTTP semantics — and are covered by the pure classifier table)
# ---------------------------------------------------------------------------


def test_tcp_probe_success_is_ok() -> None:
    listener = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        result = TCPProbe(timeout=2.0).check("127.0.0.1", listener.getsockname()[1])
    finally:
        listener.close()

    assert result.success is True
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.OK


def test_tcp_probe_refused_is_unreachable() -> None:
    """Connecting to a closed loopback port → ECONNREFUSED → UNREACHABLE."""
    probe_port = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    probe_port.bind(("127.0.0.1", 0))
    port = probe_port.getsockname()[1]
    probe_port.close()  # port is now free → connect_ex returns a non-zero errno

    result = TCPProbe(timeout=2.0).check("127.0.0.1", port)

    assert result.success is False
    assert result.status_code is None
    assert result.error_kind is ERROR_KIND_UNREACHABLE
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.UNREACHABLE


def test_tcp_probe_timeout_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RaisingSocket:
        def __enter__(self) -> _RaisingSocket:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def settimeout(self, value: float) -> None:
            pass

        def connect_ex(self, address: object) -> int:
            raise TimeoutError("timed out")

    fake_socket_module = types.SimpleNamespace(
        AF_INET=socket_module.AF_INET,
        SOCK_STREAM=socket_module.SOCK_STREAM,
        socket=lambda *a, **k: _RaisingSocket(),
    )
    monkeypatch.setattr(probes_module, "socket", fake_socket_module)

    result = TCPProbe(timeout=1.0).check("127.0.0.1", 9)

    assert result.success is False
    assert result.error_kind is ERROR_KIND_TIMEOUT
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.TIMEOUT


# ---------------------------------------------------------------------------
# CustomProbe (real subprocesses; 503 / bad-body-200 / expected_body
# mismatch are N/A for scripts — exit code 0 vs non-zero only)
# ---------------------------------------------------------------------------


def test_custom_probe_success_is_ok() -> None:
    probe = CustomProbe(script="echo ok", execution_mode=ProbeExecutionMode.RESTRICTED, timeout=5.0)

    result = probe.check("127.0.0.1", 9)

    assert result.success is True
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.OK


def test_custom_probe_nonzero_exit_is_error(tmp_path: Path) -> None:
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    probe = CustomProbe(script=str(script), execution_mode=ProbeExecutionMode.RESTRICTED, timeout=5.0)
    result = probe.check("127.0.0.1", 9)

    assert result.success is False
    assert result.status_code == 3
    assert result.error_kind is None
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.ERROR


def test_custom_probe_timeout_is_timeout() -> None:
    probe = CustomProbe(script="sleep 2", execution_mode=ProbeExecutionMode.RESTRICTED, timeout=0.3)

    start = time.monotonic()
    result = probe.check("127.0.0.1", 9)
    elapsed = time.monotonic() - start

    assert result.success is False
    assert result.error_kind is ERROR_KIND_TIMEOUT
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.TIMEOUT
    assert elapsed < 1.5  # timeout actually fired, did not wait for sleep


def test_custom_probe_script_error_is_error() -> None:
    probe = CustomProbe(script="/nonexistent/no-such-script", execution_mode=ProbeExecutionMode.RESTRICTED, timeout=5.0)

    result = probe.check("127.0.0.1", 9)

    assert result.success is False
    assert result.error_kind is None
    assert classify_probe_outcome(result.success, result.status_code, result.error_kind) is HealthCheckStatus.ERROR


# ---------------------------------------------------------------------------
# Async bridge (check_one_async): same structured classification per mode
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self._status = status_code
        self._text = text

    @property
    def status_code(self) -> int:
        return self._status

    @property
    def text(self) -> str:
        return self._text

    @property
    def is_server_error(self) -> bool:  # noqa: N802 - httpx response API
        return self._status >= 500

    def json(self) -> dict[str, object]:
        return {}


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc

    async def get(self, url: str, timeout: float | None = None) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _async_config(expected_body: str | None = None) -> InstanceConfig:
    return InstanceConfig(
        name="async-mapping-test",
        model=ModelConfig(path=Path("models/test.gguf")),
        healthcheck=HealthcheckConfig(
            type="http",
            path="/health",
            expected_status=[200],
            expected_body=expected_body,
        ),
    )


def _run(coro: object) -> HealthCheckResult:
    """Drive a coroutine on a fresh event loop (no pytest-asyncio dependency)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_async_503_is_loading() -> None:
    client = _FakeAsyncClient(response=_FakeResponse(503, '{"status": "loading model"}'))
    result = _run(check_one_async(client, _async_config()))  # type: ignore[arg-type]

    assert result.status is HealthCheckStatus.LOADING


def test_async_timeout_is_timeout() -> None:
    client = _FakeAsyncClient(exc=httpx.ReadTimeout("timed out"))
    result = _run(check_one_async(client, _async_config()))  # type: ignore[arg-type]

    assert result.status is HealthCheckStatus.TIMEOUT


def test_async_refused_is_unreachable() -> None:
    client = _FakeAsyncClient(exc=httpx.ConnectError("refused"))
    result = _run(check_one_async(client, _async_config()))  # type: ignore[arg-type]

    assert result.status is HealthCheckStatus.UNREACHABLE


def test_async_bad_body_200_is_ok() -> None:
    """Without expected_body a 200 answer satisfies the probe contract (OK)."""
    client = _FakeAsyncClient(response=_FakeResponse(200, "not json at all"))
    result = _run(check_one_async(client, _async_config()))  # type: ignore[arg-type]

    assert result.status is HealthCheckStatus.OK


def test_async_expected_body_mismatch_is_error() -> None:
    client = _FakeAsyncClient(response=_FakeResponse(200, "server says nothing useful"))
    result = _run(check_one_async(client, _async_config(expected_body="ready")))  # type: ignore[arg-type]

    assert result.status is HealthCheckStatus.ERROR
