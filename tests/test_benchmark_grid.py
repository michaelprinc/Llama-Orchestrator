"""Tests for grid benchmark planning and storage."""

from pathlib import Path

from llama_orchestrator.benchmark import BenchmarkResult, BenchmarkSettings
from llama_orchestrator.benchmark_grid import (
    GridParameterRange,
    GridPlan,
    default_request_grid_plan,
    grid_parameter_catalog,
    latest_grid_runs,
    model_metadata_catalog,
    record_grid_run_finish,
    record_grid_run_start,
    request_parameter_catalog,
    run_request_grid_for_instance,
    runtime_static_parameter_catalog,
    sampling_parameter_catalog,
    settings_for_combination,
    unsupported_execution_parameters,
)
from llama_orchestrator.config import InstanceConfig, ModelConfig


def _settings(tmp_path: Path) -> BenchmarkSettings:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Benchmark prompt", encoding="utf-8")
    return BenchmarkSettings(prompt_file=prompt, max_tokens=32, temperature=0.0)


def _result(name: str, *, status: str = "ok") -> BenchmarkResult:
    return BenchmarkResult(
        instance_name=name,
        timestamp="2026-06-05T10:00:00+0200",
        config_hash="cfg",
        prompt_file="prompt.txt",
        prompt_sha256="sha",
        prompt_chars=16,
        output_tokens=20 if status == "ok" else None,
        tokens_per_second=10.0 if status == "ok" else None,
        latency_ms=100.0 if status == "ok" else None,
        elapsed_ms=2000.0 if status == "ok" else None,
        vram_mb=1024.0 if status == "ok" else None,
        status=status,
        artifact_file="logs/demo/benchmarks/run.md" if status == "ok" else None,
        error=None if status == "ok" else "failed",
    )


def test_grid_plan_enumerates_numeric_enum_and_bool_values() -> None:
    plan = GridPlan(
        parameters=(
            GridParameterRange("temperature", minimum=0.0, maximum=0.2, step=0.1),
            GridParameterRange("endpoint", values=("chat_completions", "completion")),
            GridParameterRange("ignore_eos", values=(False, True)),
        )
    )

    combinations = plan.combinations()

    assert plan.combination_count() == 12
    assert combinations[0].parameters == {
        "temperature": 0.0,
        "endpoint": "chat_completions",
        "ignore_eos": False,
    }
    assert combinations[-1].parameters == {
        "temperature": 0.2,
        "endpoint": "completion",
        "ignore_eos": True,
    }


def test_grid_plan_enforces_hard_limit() -> None:
    plan = GridPlan(
        parameters=(
            GridParameterRange("max_tokens", minimum=1, maximum=20, step=1),
            GridParameterRange("temperature", minimum=0.0, maximum=1.0, step=0.1),
        ),
        hard_limit=100,
    )

    try:
        plan.combinations()
    except ValueError as exc:
        assert "above hard limit" in str(exc)
    else:
        raise AssertionError("Expected hard limit failure")


def test_request_catalog_contains_quick_benchmark_parameters(tmp_path: Path) -> None:
    names = {spec.name: spec for spec in request_parameter_catalog(_settings(tmp_path))}

    assert set(names) == {
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "repeat_penalty",
        "seed",
        "endpoint",
        "ignore_eos",
    }
    assert names["temperature"].category == "request"
    assert names["endpoint"].choices == ("chat_completions", "completion")
    assert names["ignore_eos"].value_type == "bool"


def test_sampling_catalog_is_dynamic_and_execution_supported(tmp_path: Path) -> None:
    specs = {spec.name: spec for spec in sampling_parameter_catalog(_settings(tmp_path))}

    assert specs["temperature"].category == "request"
    assert specs["temperature"].restart_required is False
    assert specs["temperature"].execution_supported is True
    assert specs["max_tokens"].execution_supported is True


def test_runtime_catalog_marks_restart_required_parameters() -> None:
    specs = {spec.name: spec for spec in runtime_static_parameter_catalog()}

    assert specs["model.context_size"].restart_required is True
    assert specs["gpu.layers"].restart_required is True
    assert specs["--cache-type-k"].category == "model_runtime"
    assert "q4_0" in specs["--cache-type-k"].choices
    assert specs["--spec-type"].restart_required is True
    assert specs["--spec-draft-n-max"].value_type == "int"
    assert specs["--spec-draft-n-max"].execution_supported is False


def test_grid_catalog_separates_runtime_sampling_and_metadata(tmp_path: Path) -> None:
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("model.gguf")))
    specs = {spec.name: spec for spec in grid_parameter_catalog(config, _settings(tmp_path))}

    assert specs["temperature"].category == "request"
    assert specs["model.context_size"].restart_required is True
    assert specs["--spec-draft-n-max"].category == "model_runtime"
    assert specs["architecture"].read_only is True


def test_model_metadata_catalog_is_read_only() -> None:
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("model.gguf")))

    rows = model_metadata_catalog(config)

    assert {row.name for row in rows} >= {"architecture", "n_layers", "head_dim_k"}
    assert all(row.read_only for row in rows)
    assert all(row.category == "model_metadata" for row in rows)


def test_settings_for_combination_only_applies_request_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    combination = GridPlan(
        parameters=(
            GridParameterRange("temperature", values=(0.2,)),
            GridParameterRange("model.context_size", values=(8192,)),
        )
    ).combinations()[0]

    updated = settings_for_combination(settings, combination)

    assert updated.temperature == 0.2
    assert not hasattr(updated, "model.context_size")
    assert settings.temperature == 0.0


def test_request_grid_rejects_restart_required_parameters(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("model.gguf")))
    plan = GridPlan(parameters=(GridParameterRange("--spec-draft-n-max", minimum=1, maximum=3, step=1),))

    assert unsupported_execution_parameters(plan) == ("--spec-draft-n-max",)
    try:
        run_request_grid_for_instance(
            config,
            base_settings=settings,
            plan=plan,
            run_benchmark=lambda _config, _settings: _result("demo"),
            db_path=tmp_path / "benchmarks.sqlite",
        )
    except ValueError as exc:
        assert "restart-required" in str(exc)
    else:
        raise AssertionError("Expected request-only runner to reject runtime parameter")


def test_grid_db_helpers_store_failed_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plan = default_request_grid_plan(settings)
    db_path = tmp_path / "benchmarks.sqlite"
    combination = plan.combinations()[0]

    sweep = run_request_grid_for_instance(
        InstanceConfig(name="demo", model=ModelConfig(path=Path("model.gguf"))),
        base_settings=settings,
        plan=GridPlan(parameters=()),
        run_benchmark=lambda _config, _settings: _result("demo"),
        db_path=db_path,
    )
    run = record_grid_run_start(
        sweep_id=sweep.sweep_id,
        instance_name="demo",
        combination=combination,
        db_path=db_path,
    )
    record_grid_run_finish(run, status="failed", error="boom", db_path=db_path)

    rows = latest_grid_runs(sweep.sweep_id, db_path)

    assert rows[-1]["status"] == "failed"
    assert rows[-1]["error"] == "boom"
    assert rows[-1]["parameters_json"] == '{"temperature": 0.0}'


def test_request_grid_runner_records_runs_and_stops_between_combinations(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    config = InstanceConfig(name="demo", model=ModelConfig(path=Path("model.gguf")))
    plan = GridPlan(parameters=(GridParameterRange("temperature", values=(0.0, 0.2, 0.4)),))
    db_path = tmp_path / "benchmarks.sqlite"
    calls: list[float] = []
    messages: list[str] = []

    def run_benchmark(_config: InstanceConfig, active_settings: BenchmarkSettings) -> BenchmarkResult:
        calls.append(active_settings.temperature)
        return _result("demo")

    sweep = run_request_grid_for_instance(
        config,
        base_settings=settings,
        plan=plan,
        should_stop=lambda: len(calls) >= 1,
        post_message=messages.append,
        run_benchmark=run_benchmark,
        db_path=db_path,
    )

    rows = latest_grid_runs(sweep.sweep_id, db_path)

    assert calls == [0.0]
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert '"tokens_per_second": 10.0' in rows[0]["metrics_json"]
    assert messages == [
        "[Grid benchmark] 1/3 running: demo",
        "[Grid benchmark] 1/3 ok: demo",
    ]
