#!/usr/bin/env python3
"""Analyze gui/app.py and produce a section-based division plan.

This script:
1. Reads gui/app.py
2. Identifies all class/method boundaries
3. Groups them into logical sections
4. Outputs a JSON plan suitable for spec-driven refactoring

Usage:
    python .hermes/scripts/analyze_app_sections.py
"""

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Method:
    name: str
    line_start: int
    line_end: int
    class_name: str | None = None
    is_async: bool = False
    is_private: bool = False
    docstring: str = ""


@dataclass
class Class:
    name: str
    line_start: int
    line_end: int
    methods: list[Method] = field(default_factory=list)
    docstring: str = ""


@dataclass
class Section:
    """A logical section of app.py for independent refactoring."""
    name: str
    description: str
    line_start: int
    line_end: int
    target_module: str
    classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    estimated_lines: int = 0
    dependencies: list[str] = field(default_factory=list)
    extraction_order: int = 0


def read_file(path: str) -> list[str]:
    with open(path, "r") as f:
        return f.readlines()


def extract_classes_and_methods(lines: list[str]) -> list[Class]:
    """Extract all class definitions with their methods."""
    classes: list[Class] = []
    current_class: Class | None = None

    for i, line in enumerate(lines, 1):
        # Class definition
        m = re.match(r'^class\s+(\w+)(?:\(([^)]+)\))?\s*:', line)
        if m:
            class_name = m.group(1)
            bases = m.group(2) or ""
            current_class = Class(
                name=class_name,
                line_start=i,
                line_end=0,  # set later
                docstring=_get_docstring(lines, i),
            )
            classes.append(current_class)
            continue

        # Method definition (4-space indent = inside a class)
        m2 = re.match(r'^    (async\s+)?def\s+(\w+)\(', line)
        if m2 and current_class is not None:
            is_async = bool(m2.group(1))
            method_name = m2.group(2)
            method = Method(
                name=method_name,
                line_start=i,
                line_end=0,  # set later
                class_name=current_class.name,
                is_async=is_async,
                is_private=method_name.startswith("_") and not method_name.startswith("__"),
                docstring=_get_docstring(lines, i),
            )
            current_class.methods.append(method)

    # Set line_end for each class
    for idx, cls in enumerate(classes):
        if idx + 1 < len(classes):
            cls.line_end = classes[idx + 1].line_start - 1
        else:
            cls.line_end = len(lines)

    # Set line_end for each method
    for cls in classes:
        for idx, method in enumerate(cls.methods):
            if idx + 1 < len(cls.methods):
                method.line_end = cls.methods[idx + 1].line_start - 1
            else:
                method.line_end = cls.line_end

    return classes


def _get_docstring(lines: list[str], start_line: int) -> str:
    """Get the docstring of a class or method definition."""
    # Find the next non-empty line after the colon
    for i in range(start_line, min(start_line + 3, len(lines))):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#"):
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = stripped[:3]
                if stripped.count(quote) >= 2:
                    return stripped.strip(quote).strip()
                # Multi-line docstring
                doc = stripped[3:]
                for j in range(i + 1, len(lines)):
                    if quote in lines[j]:
                        doc += lines[j].strip().strip(quote)
                        break
                return doc
            break
    return ""


def identify_sections(classes: list[Class], lines: list[str]) -> list[Section]:
    """Group classes into logical sections for independent refactoring."""
    sections: list[Section] = []

    # Section 1: Module-level dataclasses (TableRow, ImportDialogEvent, GuiRefreshSnapshot)
    dataclass_classes = [c for c in classes if c.line_start < 350 and c.name in (
        "TableRow", "ImportDialogEvent", "GuiRefreshSnapshot"
    )]
    if dataclass_classes:
        sections.append(Section(
            name="dataclasses",
            description="Immutable data classes: TableRow, ImportDialogEvent, GuiRefreshSnapshot",
            line_start=dataclass_classes[0].line_start,
            line_end=dataclass_classes[-1].line_end,
            classes=[c.name for c in dataclass_classes],
            target_module="dataclasses.py",
            estimated_lines=sum(
                (c.line_end or 0) - c.line_start + 1 for c in dataclass_classes
            ),
            dependencies=[],
            extraction_order=1,
        ))

    # Section 2: Grid dialog support (GridDialogParameterVars, helper functions)
    grid_support = [c for c in classes if c.name == "GridDialogParameterVars"]
    if grid_support:
        sections.append(Section(
            name="grid_dialog_support",
            description="Grid benchmark dialog support: GridDialogParameterVars, _default_step_or_values",
            line_start=grid_support[0].line_start,
            line_end=grid_support[0].line_end,
            classes=[c.name for c in grid_support],
            target_module="grid_dialogs.py",
            estimated_lines=grid_support[0].line_end - grid_support[0].line_start + 1,
            dependencies=["dataclasses"],
            extraction_order=2,
        ))

    # Section 3: KV Cache Profile Dialog
    kv_cache = [c for c in classes if c.name == "KvCacheProfileDialog"]
    if kv_cache:
        sections.append(Section(
            name="kv_cache_profile_dialog",
            description="KV Cache profile selection dialog",
            line_start=kv_cache[0].line_start,
            line_end=kv_cache[0].line_end,
            classes=[c.name for c in kv_cache],
            target_module="kv_cache_dialogs.py",
            estimated_lines=kv_cache[0].line_end - kv_cache[0].line_start + 1,
            dependencies=["grid_dialog_support"],
            extraction_order=3,
        ))

    # Section 4: Grid Benchmark Dialog
    grid_bench = [c for c in classes if c.name == "GridBenchmarkDialog"]
    if grid_bench:
        sections.append(Section(
            name="grid_benchmark_dialog",
            description="Grid benchmark parameter configuration dialog",
            line_start=grid_bench[0].line_start,
            line_end=grid_bench[0].line_end,
            classes=[c.name for c in grid_bench],
            target_module="grid_dialogs.py",
            estimated_lines=grid_bench[0].line_end - grid_bench[0].line_start + 1,
            dependencies=["grid_dialog_support", "kv_cache_profile_dialog"],
            extraction_order=4,
        ))

    # Section 5: Main GUI class (LlamaOrchestratorGui)
    main_gui = [c for c in classes if c.name == "LlamaOrchestratorGui"]
    if main_gui:
        # Split the main GUI class into sub-sections
        methods = main_gui[0].methods
        # Group methods by concern
        widget_methods = [m for m in methods if m.name in (
            "__init__", "_build_widgets", "_auto_refresh", "_apply_visible_columns",
            "_refresh_tree_headings", "_toggle_sort", "_reset_gui_state",
            "_toggle_gpu_inventory", "_render_gpu_inventory", "_edit_gpu_alias",
        )]
        message_methods = [m for m in methods if m.name in (
            "_schedule_message_pump", "_pump_messages", "_append_activity", "_post_message",
        )]
        refresh_methods = [m for m in methods if m.name in (
            "refresh", "_capture_tree_position", "_collect_refresh_snapshot",
            "_build_table_rows", "_visible_rows", "_render_full_rows",
            "_render_refresh_metadata", "_render_daemon_status",
        )]
        selection_methods = [m for m in methods if m.name in (
            "_on_select", "_tree_column_from_event", "_on_tree_click",
            "_on_tree_double_click", "_show_context_menu",
        )]
        instance_methods = [m for m in methods if m.name in (
            "_selected_instance", "_selected_instances", "_visible_instance_names",
            "_ordered_queued_instance_names", "_update_queue_cells",
            "_toggle_queue_name",
        )]
        benchmark_methods = [m for m in methods if m.name in (
            "_set_active_benchmark_name", "_toggle_selected_queue_rows",
            "_benchmark_job_running", "_begin_benchmark_job", "_finish_benchmark_job",
            "_update_benchmark_controls", "_run_background", "_run_selected",
            "_benchmark_instance", "_start_serial_benchmark_instance",
            "_handle_benchmark_exception", "_run_batch", "_run_benchmark_selected",
            "_run_serial_benchmark", "_stop_serial_benchmark",
            "_grid_benchmark_targets", "_run_grid_benchmark", "_stop_grid_benchmark",
        )]
        benchmark_settings_methods = [m for m in methods if m.name in (
            "_set_benchmark_settings", "_reload_benchmark_settings",
            "_refresh_benchmark_params_menu", "_toggle_benchmark_endpoint",
            "_toggle_benchmark_ignore_eos", "_edit_benchmark_max_tokens",
            "_edit_benchmark_temperature", "_edit_optional_int_setting",
            "_edit_optional_float_setting", "_reset_benchmark_params",
            "_open_benchmark_settings_file",
        )]
        prompt_methods = [m for m in methods if m.name in (
            "_select_prompt_file", "_open_prompt_file", "_edit_args_inline",
            "_save_runtime_args",
        )]
        action_methods = [m for m in methods if m.name in (
            "_clone_selected", "_next_clone_name", "_diff_selected",
            "_open_diff_window", "_copy_cli_command", "_rename_display_name",
        )]
        daemon_methods = [m for m in methods if m.name in (
            "_start_daemon", "_stop_daemon", "_export_config_to_vscode",
        )]
        file_methods = [m for m in methods if m.name in (
            "_open_config", "_open_config_folder", "_open_logs",
            "_open_project", "_open_path",
        )]
        dialog_methods = [m for m in methods if m.name in (
            "_open_add_dialog", "_on_model_saved", "_open_binary_dialog",
            "_check_vulkan_binary", "_install_binary", "_apply_default_args",
        )]

        # Create sub-sections for the main GUI
        sections.append(Section(
            name="gui_widgets",
            description="GUI widget construction: __init__, _build_widgets, refresh, GPU inventory",
            line_start=main_gui[0].line_start,
            line_end=widget_methods[-1].line_end if widget_methods else main_gui[0].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in widget_methods],
            target_module="gui_widgets.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in widget_methods
            ),
            dependencies=["dataclasses", "grid_dialog_support"],
            extraction_order=5,
        ))

        sections.append(Section(
            name="gui_messages",
            description="Message pump and activity log",
            line_start=message_methods[0].line_start,
            line_end=message_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in message_methods],
            target_module="gui_messages.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in message_methods
            ),
            dependencies=["gui_widgets"],
            extraction_order=6,
        ))

        sections.append(Section(
            name="gui_refresh",
            description="Refresh cycle: snapshot collection, row rendering, metadata",
            line_start=refresh_methods[0].line_start,
            line_end=refresh_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in refresh_methods],
            target_module="gui_refresh.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in refresh_methods
            ),
            dependencies=["gui_widgets", "gui_messages"],
            extraction_order=7,
        ))

        sections.append(Section(
            name="gui_selection",
            description="Tree selection, context menu, event handling",
            line_start=selection_methods[0].line_start,
            line_end=selection_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in selection_methods],
            target_module="gui_selection.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in selection_methods
            ),
            dependencies=["gui_widgets"],
            extraction_order=8,
        ))

        sections.append(Section(
            name="gui_instances",
            description="Instance selection, queue management, visible names",
            line_start=instance_methods[0].line_start,
            line_end=instance_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in instance_methods],
            target_module="gui_instances.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in instance_methods
            ),
            dependencies=["gui_widgets", "gui_selection"],
            extraction_order=9,
        ))

        sections.append(Section(
            name="gui_benchmark",
            description="Benchmark execution: single, serial, batch, grid",
            line_start=benchmark_methods[0].line_start,
            line_end=benchmark_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in benchmark_methods],
            target_module="gui_benchmark.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in benchmark_methods
            ),
            dependencies=["gui_widgets", "gui_instances", "grid_dialog_support"],
            extraction_order=10,
        ))

        sections.append(Section(
            name="gui_benchmark_settings",
            description="Benchmark parameter editing and settings",
            line_start=benchmark_settings_methods[0].line_start,
            line_end=benchmark_settings_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in benchmark_settings_methods],
            target_module="gui_benchmark_settings.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in benchmark_settings_methods
            ),
            dependencies=["gui_widgets", "gui_benchmark"],
            extraction_order=11,
        ))

        sections.append(Section(
            name="gui_prompts",
            description="Prompt file handling and runtime args editing",
            line_start=prompt_methods[0].line_start,
            line_end=prompt_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in prompt_methods],
            target_module="gui_prompts.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in prompt_methods
            ),
            dependencies=["gui_widgets"],
            extraction_order=12,
        ))

        sections.append(Section(
            name="gui_actions",
            description="Instance actions: clone, diff, copy CLI, rename",
            line_start=action_methods[0].line_start,
            line_end=action_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in action_methods],
            target_module="gui_actions.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in action_methods
            ),
            dependencies=["gui_widgets", "gui_instances"],
            extraction_order=13,
        ))

        sections.append(Section(
            name="gui_daemon",
            description="Daemon management: start, stop, export config",
            line_start=daemon_methods[0].line_start,
            line_end=daemon_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in daemon_methods],
            target_module="gui_daemon.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in daemon_methods
            ),
            dependencies=["gui_widgets"],
            extraction_order=14,
        ))

        sections.append(Section(
            name="gui_files",
            description="File operations: open config, logs, project, paths",
            line_start=file_methods[0].line_start,
            line_end=file_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in file_methods],
            target_module="gui_files.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in file_methods
            ),
            dependencies=["gui_widgets"],
            extraction_order=15,
        ))

        sections.append(Section(
            name="gui_dialogs",
            description="Dialog triggers: add model, binary install, default args",
            line_start=dialog_methods[0].line_start,
            line_end=dialog_methods[-1].line_end,
            classes=["LlamaOrchestratorGui"],
            methods=[m.name for m in dialog_methods],
            target_module="gui_dialogs.py",
            estimated_lines=sum(
                (m.line_end or 0) - m.line_start + 1 for m in dialog_methods
            ),
            dependencies=["gui_widgets", "grid_dialog_support"],
            extraction_order=16,
        ))

    # Section 6: Model addition dialogs
    model_dialogs = [c for c in classes if c.name in (
        "AddModelDialog", "AddModelPortSettingsDialog", "ExistingModelFileDialog"
    )]
    if model_dialogs:
        sections.append(Section(
            name="model_dialogs",
            description="Model addition dialogs: AddModelDialog, AddModelPortSettingsDialog, ExistingModelFileDialog",
            line_start=model_dialogs[0].line_start,
            line_end=model_dialogs[-1].line_end,
            classes=[c.name for c in model_dialogs],
            target_module="model_dialogs.py",
            estimated_lines=sum(
                (c.line_end or 0) - c.line_start + 1 for c in model_dialogs
            ),
            dependencies=["dataclasses"],
            extraction_order=17,
        ))

    # Section 7: HuggingFace Import Dialog
    hf_dialog = [c for c in classes if c.name == "HuggingFaceImportDialog"]
    if hf_dialog:
        sections.append(Section(
            name="hf_import_dialog",
            description="HuggingFace model import dialog",
            line_start=hf_dialog[0].line_start,
            line_end=hf_dialog[0].line_end,
            classes=[c.name for c in hf_dialog],
            target_module="hf_import_dialog.py",
            estimated_lines=hf_dialog[0].line_end - hf_dialog[0].line_start + 1,
            dependencies=["dataclasses"],
            extraction_order=18,
        ))

    # Section 8: Install Binary Dialog
    install_dialog = [c for c in classes if c.name == "InstallBinaryDialog"]
    if install_dialog:
        sections.append(Section(
            name="install_binary_dialog",
            description="LLama binary installation dialog",
            line_start=install_dialog[0].line_start,
            line_end=install_dialog[0].line_end,
            classes=[c.name for c in install_dialog],
            target_module="install_dialog.py",
            estimated_lines=install_dialog[0].line_end - install_dialog[0].line_start + 1,
            dependencies=[],
            extraction_order=19,
        ))

    return sections


def main():
    app_path = Path(__file__).parent.parent.parent / "src" / "llama_orchestrator" / "gui" / "app.py"
    if not app_path.exists():
        print(f"ERROR: {app_path} not found", file=sys.stderr)
        sys.exit(1)

    lines = read_file(str(app_path))
    print(f"Read {len(lines)} lines from {app_path}", file=sys.stderr)

    classes = extract_classes_and_methods(lines)
    print(f"Found {len(classes)} classes", file=sys.stderr)

    sections = identify_sections(classes, lines)
    print(f"Identified {len(sections)} sections", file=sys.stderr)

    # Output JSON
    result = {
        "total_lines": len(lines),
        "total_classes": len(classes),
        "total_methods": sum(len(c.methods) for c in classes),
        "sections": [
            {
                "name": s.name,
                "description": s.description,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "classes": s.classes,
                "methods": s.methods,
                "target_module": s.target_module,
                "estimated_lines": s.estimated_lines,
                "dependencies": s.dependencies,
                "extraction_order": s.extraction_order,
            }
            for s in sections
        ],
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
