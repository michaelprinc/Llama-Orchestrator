"""Focused tests for explicit instance migration and identity persistence."""

import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from llama_orchestrator.benchmark import BenchmarkResult, get_benchmark_db_path, init_benchmark_db, record_benchmark_result
from llama_orchestrator.cli import app
from llama_orchestrator.config import InstanceConfig, ModelConfig, save_config
from llama_orchestrator.config.migration import migrate_instances
from llama_orchestrator.engine.state import HealthStatus, InstanceStatus, RuntimeState, init_db, save_runtime


runner = CliRunner()


def _patch_temp_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("llama_orchestrator.benchmark.get_project_root", lambda: tmp_path)
    monkeypatch.setattr("llama_orchestrator.benchmark.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr("llama_orchestrator.engine.state.get_state_dir", lambda: tmp_path / "state")


def test_migrate_instances_renames_legacy_directories_and_syncs_sqlite(tmp_path: Path, monkeypatch) -> None:
    _patch_temp_root(monkeypatch, tmp_path)
    legacy_dir = tmp_path / "instances" / "legacy-model"
    legacy_dir.mkdir(parents=True)
    legacy_config_path = legacy_dir / "config.json"
    legacy_config_path.write_text(
        json.dumps({"name": "legacy-model", "model": {"path": "models/test.gguf"}}),
        encoding="utf-8",
    )
    init_db()

    config = InstanceConfig(name="legacy-model", model=ModelConfig(path=Path("models/test.gguf")))
    config.set_source_path(legacy_config_path)
    save_runtime(
        RuntimeState(
            name="legacy-model",
            pid=1234,
            status=InstanceStatus.RUNNING,
            health=HealthStatus.HEALTHY,
        )
    )

    benchmark_db = init_benchmark_db(get_benchmark_db_path())
    record_benchmark_result(
        BenchmarkResult(
            instance_name="legacy-model",
            timestamp="2026-05-30T10:00:00Z",
            config_hash="abc",
            prompt_file="default.txt",
            prompt_sha256="sha",
            prompt_chars=10,
            output_tokens=5,
            tokens_per_second=1.0,
            latency_ms=100.0,
            elapsed_ms=500.0,
            vram_mb=256.0,
            status="ok",
        ),
        benchmark_db,
    )

    summary = migrate_instances(apply=True)

    assert summary.changed == 1
    target_dir = tmp_path / "instances" / summary.records[0].target_config_path.parent.name
    assert target_dir.exists()
    assert not legacy_dir.exists()
    assert summary.records[0].backup_path is not None
    assert summary.records[0].backup_path.exists()

    with sqlite3.connect(tmp_path / "state" / "state.sqlite") as conn:
        row = conn.execute(
            "SELECT instance_uid, instance_no, display_name FROM runtime WHERE name = ?",
            ("legacy-model",),
        ).fetchone()
        assert row is not None
        assert row[0] == summary.records[0].instance_uid
        assert row[1] == summary.records[0].instance_no
        assert row[2] == summary.records[0].display_name

    with sqlite3.connect(benchmark_db) as conn:
        row = conn.execute(
            "SELECT instance_uid, instance_no, display_name FROM benchmarks WHERE instance_name = ?",
            ("legacy-model",),
        ).fetchone()
        assert row is not None
        assert row[0] == summary.records[0].instance_uid
        assert row[1] == summary.records[0].instance_no
        assert row[2] == summary.records[0].display_name


def test_config_migrate_instances_cli_previews_without_apply(tmp_path: Path, monkeypatch) -> None:
    _patch_temp_root(monkeypatch, tmp_path)
    legacy_dir = tmp_path / "instances" / "legacy-preview"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.json").write_text(
        json.dumps({"name": "legacy-preview", "model": {"path": "models/test.gguf"}}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "migrate-instances"])

    assert result.exit_code == 0
    assert "Preview instance migration" in result.stdout
    assert "legacy-preview" in result.stdout