"""Tests for GUI helper behavior."""

from llama_orchestrator.gui import (
    DEFAULT_RUNTIME_ARGS,
    apply_managed_runtime_args,
    parse_tag_string,
)


def test_apply_managed_runtime_args_defaults() -> None:
    """Default GUI args include requested llama-server flags."""
    assert apply_managed_runtime_args([]) == DEFAULT_RUNTIME_ARGS


def test_apply_managed_runtime_args_replaces_existing_values() -> None:
    """Managed flags are replaced instead of duplicated."""
    args = [
        "--threads",
        "8",
        "--reasoning",
        "auto",
        "--flash-attn",
        "off",
        "--no-mmproj",
    ]

    assert apply_managed_runtime_args(args) == [
        "--threads",
        "8",
        "--no-mmproj",
        "--reasoning",
        "off",
        "--flash-attn",
        "auto",
    ]


def test_parse_tag_string_normalizes_unique_tags() -> None:
    """Tags can be typed as comma or space separated values."""
    assert parse_tag_string("Qwen35-family, rx480-test qwen35-family") == [
        "qwen35-family",
        "rx480-test",
    ]
