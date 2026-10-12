"""T2.3 / R18: explicit override policy tests.

When raw ``config.args`` re-specify a built-in flag, the behavior must not be
silent: ``start_instance`` logs the effective command at INFO with both values,
and ``describe`` surfaces the override (config value + raw value). llama.cpp
applies the last occurrence of a repeated flag, so the raw value wins at
runtime.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from llama_orchestrator.cli_describe import build_description, format_description_rich
from llama_orchestrator.config.schema import (
    GpuConfig,
    HealthcheckConfig,
    InstanceConfig,
    ModelConfig,
    RestartPolicy,
    ServerConfig,
)
from llama_orchestrator.engine.command import find_raw_arg_overrides
from llama_orchestrator.engine.process import ProcessError, start_instance
from llama_orchestrator.engine.state import init_db


def _make_config(name: str, port: int, args: list[str] | None = None) -> InstanceConfig:
    return InstanceConfig(
        name=name,
        model=ModelConfig(path=Path("models/test.gguf")),
        server=ServerConfig(host="127.0.0.1", port=port),
        healthcheck=HealthcheckConfig(retries=2, start_period=0),
        restart_policy=RestartPolicy(enabled=True, max_retries=3),
        args=args or [],
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


def _free_port() -> int:
    """An ephemeral loopback port that is free after the probe socket closes."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestFindRawArgOverrides:
    def test_port_shadowing_detected_with_both_values(self):
        config = _make_config("inst", 8001, args=["--port", "9999"])
        assert find_raw_arg_overrides(config) == [("--port", "8001", "9999")]

    def test_non_builtin_flags_not_reported(self):
        config = _make_config("inst", 8001, args=["--temp", "0.5", "--flash-attn"])
        assert find_raw_arg_overrides(config) == []

    def test_empty_args_no_overrides(self):
        assert find_raw_arg_overrides(_make_config("inst", 8001)) == []

    def test_equals_form_and_last_occurrence_wins(self):
        config = _make_config(
            "inst", 8001, args=["--port=9998", "--port", "9999"]
        )
        assert find_raw_arg_overrides(config) == [("--port", "8001", "9999")]

    def test_conditional_builtins_are_covered(self):
        config = _make_config(
            "inst",
            8001,
            args=["--parallel", "4", "--n-gpu-layers", "99", "-fit", "on"],
        )
        config.server.parallel = 2
        config.gpu = GpuConfig(backend="cuda", layers=33)
        config.server.disable_fit = True
        assert find_raw_arg_overrides(config) == [
            ("--parallel", "2", "4"),
            ("--n-gpu-layers", "33", "99"),
            ("-fit", "off", "on"),
        ]


class TestStartInstanceOverrideLog:
    def test_info_log_line_emitted_with_both_values(self, temp_root, caplog):
        port = _free_port()
        config = _make_config("inst-ovr", port, args=["--port", "9999"])

        with (
            patch("llama_orchestrator.engine.process.instance_lock"),
            patch(
                "llama_orchestrator.engine.process.get_instance_config",
                return_value=config,
            ),
            patch(
                "llama_orchestrator.engine.process.validate_executable",
                return_value=(True, ""),
            ),
            patch("llama_orchestrator.engine.process.load_state", return_value=None),
            patch("llama_orchestrator.engine.process.load_runtime", return_value=None),
            caplog.at_level(logging.INFO, logger="llama_orchestrator.engine.process"),
            pytest.raises(ProcessError),  # fake binary path → Popen fails after logging
        ):
            start_instance("inst-ovr", wait_for_ready=False)

        info_records = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO
            and r.name == "llama_orchestrator.engine.process"
        ]
        matched = [
            r
            for r in info_records
            if "Raw args override" in r.getMessage() and "--port" in r.getMessage()
        ]
        assert matched, f"expected an INFO override log line; got: {[r.getMessage() for r in info_records]}"
        message = matched[0].getMessage()
        assert f"config={port}" in message  # config value shown
        assert "raw=9999" in message  # raw value shown
        assert "Effective command:" in message


class TestDescribeOverrideSurfacing:
    def test_describe_shows_config_and_raw_values(self, temp_root):
        port = _free_port()
        config = _make_config("inst-ovr", port, args=["--port", "9999"])
        desc = build_description("inst-ovr", config=config)
        text = format_description_rich(desc)
        assert "Raw arg overrides" in text
        assert f"--port: config={port} raw=9999" in text

    def test_describe_omits_section_without_overrides(self, temp_root):
        port = _free_port()
        config = _make_config("inst-clean", port)  # no raw args
        desc = build_description("inst-clean", config=config)
        assert desc.arg_overrides == []
        text = format_description_rich(desc)
        assert "Raw arg overrides" not in text
        assert desc.to_dict()["configuration"]["arg_overrides"] is None
