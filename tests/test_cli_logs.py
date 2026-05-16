"""CLI tests for the V2 logs command surface."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from llama_orchestrator.cli import app


runner = CliRunner()


def test_logs_supports_both_streams(tmp_path: Path) -> None:
    instance_name = "test-instance"
    log_dir = tmp_path / instance_name
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.log").write_text("out-1\nout-2\n", encoding="utf-8")
    (log_dir / "stderr.log").write_text("err-1\nerr-2\n", encoding="utf-8")

    with patch("llama_orchestrator.config.get_instance_config", return_value=MagicMock()), \
         patch("llama_orchestrator.config.get_logs_dir", return_value=tmp_path), \
         patch("llama_orchestrator.engine.detach.get_logs_dir", return_value=tmp_path):
        result = runner.invoke(app, ["logs", instance_name, "--stream", "both", "--tail", "2"])

    assert result.exit_code == 0
    assert "stdout: out-1" in result.stdout
    assert "stderr: err-1" in result.stdout


def test_logs_stderr_flag_maps_to_stderr_stream(tmp_path: Path) -> None:
    instance_name = "test-instance"
    log_dir = tmp_path / instance_name
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.log").write_text("out-only\n", encoding="utf-8")
    (log_dir / "stderr.log").write_text("err-only\n", encoding="utf-8")

    with patch("llama_orchestrator.config.get_instance_config", return_value=MagicMock()), \
         patch("llama_orchestrator.config.get_logs_dir", return_value=tmp_path), \
         patch("llama_orchestrator.engine.detach.get_logs_dir", return_value=tmp_path):
        result = runner.invoke(app, ["logs", instance_name, "--stderr", "--tail", "1"])

    assert result.exit_code == 0
    assert "err-only" in result.stdout
    assert "out-only" not in result.stdout