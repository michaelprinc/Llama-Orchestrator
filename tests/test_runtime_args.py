"""Dedicated tests for the runtime argument parser (R9/F15).

Covers the AC list: --parallel 2, --device Vulkan0, --jinja, --flag=value,
duplicates, trailing flag — plus unknown-flag queryability, no value-swallowing,
catalog completeness for every real config, and the proven repro that the
alignment validator (lint_config / find_conflicts) actually fires.
"""

import json
from pathlib import Path

import pytest

from llama_orchestrator.config.schema import (
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
)
from llama_orchestrator.config.validator import lint_config, validate_runtime_arg_alignment
from llama_orchestrator.runtime_args import (
    KNOWN_ARGS,
    find_conflicts,
    find_duplicates,
    parse_args_list,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_config(**overrides) -> InstanceConfig:
    config = InstanceConfig(
        name="demo",
        model=ModelConfig(
            path=Path("models/demo.gguf"),
            context_size=4096,
            batch_size=512,
            threads=16,
        ),
        server=ServerConfig(port=39001, host="127.0.0.1"),
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=30),
        args=[],
        env={},
    )
    return config.model_copy(update=overrides)


# --- core classification (the F15 bug cases) ---------------------------------


def test_parallel_value_is_named_not_boolean() -> None:
    """Proven F15 case: --parallel 2 must not be classified as a boolean."""
    parsed = parse_args_list(["--parallel", "2"])
    assert parsed.get("--parallel") == "2"
    assert "--parallel" not in parsed.flags
    assert parsed.positional == []


def test_device_vulkan0_queryable() -> None:
    parsed = parse_args_list(["--device", "Vulkan0"])
    assert parsed.get("--device") == "Vulkan0"
    assert parsed.has("--device") is True


def test_jinja_is_boolean_flag() -> None:
    parsed = parse_args_list(["--jinja"])
    assert "--jinja" in parsed.flags
    assert parsed.get("--jinja") is None
    assert parsed.has("--jinja") is True


def test_assignment_syntax_parsed() -> None:
    parsed = parse_args_list(["--parallel=2"])
    assert parsed.get("--parallel") == "2"
    assert parsed.assignments["--parallel"] == "--parallel=2"
    assert parsed.positional == []


def test_assignment_with_equals_in_value() -> None:
    parsed = parse_args_list(["--chat-template-kwargs=x=y"])
    assert parsed.get("--chat-template-kwargs") == "x=y"


def test_unknown_named_flag_preserved_and_queryable() -> None:
    parsed = parse_args_list(["--brand-new-flag", "xyz"])
    assert parsed.get("--brand-new-flag") == "xyz"
    assert "--brand-new-flag" in parsed.unknown
    assert parsed.extra == []  # never demoted to extra anymore


def test_unknown_assignment_flag_queryable() -> None:
    parsed = parse_args_list(["--frobnicate=3"])
    assert parsed.get("--frobnicate") == "3"
    assert "--frobnicate" in parsed.unknown


def test_trailing_value_flag_without_value_is_flag_presence() -> None:
    """A value flag at the end of the list (no value) must not crash and stays queryable."""
    parsed = parse_args_list(["--device"])
    assert "--device" in parsed.flags
    assert parsed.get("--device") is None
    assert parsed.has("--device") is True


def test_boolean_flag_does_not_swallow_next_value() -> None:
    """The old heuristic swallowed values after dash-less flags; booleans must not."""
    parsed = parse_args_list(["--jinja", "--parallel", "2"])
    assert "--jinja" in parsed.flags
    assert parsed.get("--parallel") == "2"
    assert parsed.positional == []


def test_flash_attn_value_form_consumed() -> None:
    """Real configs pass --flash-attn on|auto; the value must be consumed, not positionalized."""
    parsed = parse_args_list(["--flash-attn", "on", "--temp", "0.8"])
    assert parsed.get("--flash-attn") == "on"
    assert parsed.get("--temp") == "0.8"
    assert parsed.positional == []


def test_flash_attn_bare_is_flag_presence() -> None:
    """A bare/trailing --flash-attn (no value) stays queryable as a flag presence."""
    parsed = parse_args_list(["--metrics", "--flash-attn"])
    assert "--flash-attn" in parsed.flags
    assert "--metrics" in parsed.flags
    assert parsed.get("--temp") is None


def test_reasoning_value_forms_consumed() -> None:
    """Real configs pass --reasoning on|off|auto; all three must be consumed as values."""
    for value in ("on", "off", "auto"):
        parsed = parse_args_list(["--reasoning", value])
        assert parsed.get("--reasoning") == value, value
        assert parsed.positional == []


def test_duplicate_named_last_value_wins() -> None:
    parsed = parse_args_list(["--temp", "0.7", "--temp", "1.1"])
    assert parsed.get("--temp") == "1.1"


def test_find_duplicates_reports_both_indices() -> None:
    dupes = find_duplicates(["--parallel", "2", "--parallel", "4"])
    assert dupes == [("--parallel", 0, 2)]


def test_positional_arguments_preserved() -> None:
    parsed = parse_args_list(["model.gguf", "--port", "8080"])
    assert parsed.positional == ["model.gguf"]
    assert parsed.get("--port") == "8080"


def test_bare_dash_is_positional() -> None:
    parsed = parse_args_list(["-", "--port", "8080"])
    assert parsed.positional == ["-"]
    assert parsed.get("--port") == "8080"


def test_single_dash_fit_flag_parsed() -> None:
    """build_command emits '-fit off'; the parser must classify it, not positionalize it."""
    parsed = parse_args_list(["-fit", "off"])
    assert parsed.get("-fit") == "off"
    assert parsed.positional == []


def test_empty_input_yields_empty_structure() -> None:
    parsed = parse_args_list([])
    assert parsed.positional == []
    assert parsed.named == {}
    assert parsed.flags == frozenset()
    assert parsed.unknown == frozenset()


# --- catalog completeness ------------------------------------------------------


def test_build_command_output_fully_classified() -> None:
    """Every token build_command emits must be a known flag (no unknowns, no positionals)."""
    from llama_orchestrator.engine.command import build_command

    config = _make_config(
        server=ServerConfig(port=8080, host="127.0.0.1", parallel=2, disable_fit=True),
        gpu=GpuConfig(backend="vulkan", device_id=1, layers=30),
    )
    cmd = build_command(config)
    parsed = parse_args_list(cmd[1:])  # drop executable
    assert parsed.positional == []
    assert parsed.unknown == frozenset()
    assert parsed.get("--model") is not None
    assert parsed.get("--port") == "8080"
    assert parsed.get("--parallel") == "2"
    assert parsed.get("-fit") == "off"
    assert parsed.get("--n-gpu-layers") == "30"


def test_every_flag_in_real_instance_configs_is_known() -> None:
    """R9 AC: the catalog must include every flag used anywhere in instances/*.json."""
    config_files = sorted((PROJECT_ROOT / "instances").glob("*/config.json"))
    assert len(config_files) > 0, "no instance configs found"
    unknown_seen: set[str] = set()
    for cfg_file in config_files:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        for token in data.get("args") or []:
            if not isinstance(token, str):
                continue
            if token.startswith("--") and token in KNOWN_ARGS:
                continue  # known double-dash flag
            if token.startswith("-") and len(token) > 1 and token not in {"-fit"}:
                unknown_seen.add(f"{cfg_file.parent.name}: {token}")
    assert not unknown_seen, f"catalog missing real config flags: {sorted(unknown_seen)}"


def test_catalog_has_no_pathological_variants() -> None:
    """The 265 recursive --sorta-kv-head-id-slot-id-... entries must be gone."""
    pathological = [f for f in KNOWN_ARGS if "slot-id" in f or len(f) > 80]
    assert not pathological, f"pathological variants remain: {pathological[:5]}"
    # Sanity: the catalog is a curated list, not a generated dump.
    assert len(KNOWN_ARGS) < 300


# --- mutation API --------------------------------------------------------------


def test_set_and_remove_roundtrip() -> None:
    parsed = parse_args_list(["--temp", "0.7"])
    updated = parsed.set("--top-k", "40")
    assert updated.get("--top-k") == "40"
    assert updated.get("--temp") == "0.7"
    removed = updated.remove("--temp")
    assert removed.get("--temp") is None
    # original untouched (immutable)
    assert parsed.get("--top-k") is None


def test_set_true_converts_to_flag() -> None:
    parsed = parse_args_list(["--device", "Vulkan0"])
    updated = parsed.set("--jinja", True)
    assert "--jinja" in updated.flags


def test_to_list_contains_named_and_flags() -> None:
    parsed = parse_args_list(["--temp", "0.7", "--jinja"])
    flat = parsed.to_list()
    assert "--temp" in flat and "0.7" in flat
    assert "--jinja" in flat


def test_merge_other_wins_on_overlap() -> None:
    a = parse_args_list(["--temp", "0.5"])
    b = parse_args_list(["--temp", "1.5", "--top-k", "8"])
    merged = a.merge(b)
    assert merged.get("--temp") == "1.5"
    assert merged.get("--top-k") == "8"


def test_runtime_args_find_conflicts_named_vs_named() -> None:
    a = parse_args_list(["--device", "Vulkan0"])
    b = parse_args_list(["--device", "CUDA1"])
    assert a.find_conflicts(b) == ["--device"]
    assert a.has_conflicts_with(b) is True


# --- validator integration (the proven repro) ----------------------------------


def test_lint_config_repro_device_mismatch_fires() -> None:
    """AC: vulkan backend + --device CUDA1 must yield a non-empty error issue."""
    config = _make_config(
        args=["--device", "CUDA1"],
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=30),
    )
    result = lint_config(config)
    issues = [i for i in result.issues if i.field == "args" and i.severity == "error"]
    assert issues, f"expected a --device alignment error issue, got: {result.issues}"
    assert any("CUDA1" in i.message for i in issues)


def test_validate_alignment_matching_device_no_issue() -> None:
    config = _make_config(
        args=["--device", "Vulkan0"],
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=30),
    )
    result = validate_runtime_arg_alignment(config)
    assert result.issues == []


def test_validate_alignment_device_id_mismatch_is_warning() -> None:
    config = _make_config(
        args=["--device", "Vulkan2"],
        gpu=GpuConfig(backend="vulkan", device_id=0, layers=30),
    )
    result = validate_runtime_arg_alignment(config)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "warning"


def test_find_conflicts_returns_device_and_parallel_conflicts() -> None:
    """AC: the repro input yields BOTH the device and the parallel conflict."""
    config = {
        "gpu": {"device_id": 0, "backend": "vulkan"},
        "server": {"parallel": 2},
    }
    conflicts = find_conflicts(config, ["--device", "CUDA1", "--parallel", "4"])
    codes = {c["code"] for c in conflicts}
    assert "GPU_DEVICE_CONFLICT" in codes
    assert "PARALLEL_CONFLICT" in codes


def test_find_conflicts_backend_aware_labels() -> None:
    cuda_config = {"gpu": {"device_id": 1, "backend": "cuda"}, "server": {}}
    assert find_conflicts(cuda_config, ["--device", "CUDA1"]) == []
    bad = find_conflicts(cuda_config, ["--device", "ROCm1"])
    assert [c["code"] for c in bad] == ["GPU_DEVICE_CONFLICT"]


def test_find_conflicts_cpu_backend_skips_device_check() -> None:
    cpu_config = {"gpu": {"device_id": 0, "backend": "cpu"}, "server": {}}
    assert find_conflicts(cpu_config, ["--device", "Vulkan0"]) == []


if __name__ == "__main__":  # pragma: no cover - manual runner convenience
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
