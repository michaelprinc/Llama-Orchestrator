"""
Tkinter GUI for llama-orchestrator model instance management.

The GUI intentionally uses only the Python standard library so the desktop
management surface works on Windows without adding runtime dependencies.
"""

from __future__ import annotations

import difflib
import os
import queue
import shlex
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from llama_orchestrator.benchmark import (
    BenchmarkResult,
    BenchmarkSettings,
    latest_benchmark_results,
    load_benchmark_settings,
    quick_benchmark_instance,
    save_benchmark_settings,
)
from llama_orchestrator.config import (
    BinaryConfig,
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
    discover_instances,
    get_instance_config,
    get_project_root,
    save_config,
)
from llama_orchestrator.daemon import get_daemon_status, start_daemon, stop_daemon
from llama_orchestrator.engine import (
    InstanceStatus,
    build_command,
    format_command,
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from llama_orchestrator.health import check_instance_health
from llama_orchestrator.health.ports import suggest_port_for_instance

DEFAULT_RUNTIME_ARGS = ["--no-mmproj", "--reasoning", "off", "--flash-attn", "auto"]
MANAGED_VALUE_ARGS = {"--reasoning", "--flash-attn"}
MANAGED_FLAG_ARGS = {"--no-mmproj"}
VULKAN_VARIANT = "win-vulkan-x64"

ALL_COLUMNS = (
    "name",
    "status",
    "health",
    "pid",
    "port",
    "backend",
    "tags",
    "tps",
    "latency",
    "vram",
    "prompt",
    "model",
    "args",
    "uptime",
)
COLUMN_HEADINGS = {
    "name": "Name",
    "status": "Status",
    "health": "Health",
    "pid": "PID",
    "port": "Port",
    "backend": "Backend",
    "tags": "Tags",
    "tps": "TPS",
    "latency": "Latency ms",
    "vram": "VRAM MB",
    "prompt": "Prompt file",
    "model": "Model",
    "args": "Runtime args",
    "uptime": "Uptime",
}
COLUMN_WIDTHS = {
    "name": 150,
    "status": 90,
    "health": 110,
    "pid": 70,
    "port": 70,
    "backend": 90,
    "tags": 150,
    "tps": 80,
    "latency": 90,
    "vram": 90,
    "prompt": 140,
    "model": 280,
    "args": 260,
    "uptime": 90,
}


def apply_managed_runtime_args(
    args: list[str],
    *,
    no_mmproj: bool = True,
    reasoning: str = "off",
    flash_attn: str = "auto",
) -> list[str]:
    """Apply GUI-managed llama-server runtime args without duplicating flags."""
    cleaned: list[str] = []
    skip_next = False

    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in MANAGED_FLAG_ARGS:
            continue
        if arg in MANAGED_VALUE_ARGS:
            skip_next = True
            continue
        cleaned.append(arg)

    if no_mmproj:
        cleaned.append("--no-mmproj")
    if reasoning:
        cleaned.extend(["--reasoning", reasoning])
    if flash_attn:
        cleaned.extend(["--flash-attn", flash_attn])

    return cleaned


def parse_tag_string(value: str) -> list[str]:
    """Parse a comma/space separated tag string for config storage."""
    tags: list[str] = []
    seen: set[str] = set()
    for tag in value.replace(",", " ").split():
        clean = tag.strip().lower()
        if clean and clean not in seen:
            tags.append(clean)
            seen.add(clean)
    return tags


def format_metric(value: float | None, digits: int = 1) -> str:
    """Format optional numeric benchmark values for the table."""
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def format_benchmark_message(result: BenchmarkResult) -> str:
    """Format a benchmark result for the activity log."""
    if result.status != "ok":
        return (
            f"Benchmark {result.instance_name} failed using {result.prompt_file}: "
            f"{result.error or 'unknown error'}"
        )
    return (
        f"Benchmark {result.instance_name} using {result.prompt_file}: "
        f"{format_metric(result.tokens_per_second)} TPS, "
        f"{format_metric(result.latency_ms, 0)} ms latency, "
        f"{format_metric(result.vram_mb, 0)} MB VRAM."
    )


class LlamaOrchestratorGui(tk.Tk):
    """Desktop GUI for managing llama.cpp model instances."""

    refresh_interval_ms = 5000

    def __init__(self) -> None:
        super().__init__()
        self.title("llama-orchestrator")
        self.geometry("1120x720")
        self.minsize(960, 560)

        self.project_root = get_project_root()
        self._messages: queue.Queue[str] = queue.Queue()
        self._selected_name: str | None = None
        self.benchmark_settings = load_benchmark_settings(self.project_root)
        self.tag_filter_var = tk.StringVar(value="All tags")
        self.prompt_var = tk.StringVar(value=self.benchmark_settings.prompt_file.name)

        self._build_widgets()
        self._schedule_message_pump()
        self.refresh()
        self.after(500, self._check_vulkan_binary)
        self.after(self.refresh_interval_ms, self._auto_refresh)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(10, 10, 10, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(16, weight=1)

        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(toolbar, text="Add model", command=self._open_add_dialog).grid(row=0, column=1, padx=6)
        ttk.Button(toolbar, text="Apply args", command=self._apply_default_args).grid(row=0, column=2, padx=6)
        ttk.Button(toolbar, text="Install Vulkan", command=self._open_binary_dialog).grid(row=0, column=3, padx=6)
        ttk.Button(toolbar, text="Start", command=lambda: self._run_selected("start")).grid(row=0, column=4, padx=6)
        ttk.Button(toolbar, text="Stop", command=lambda: self._run_selected("stop")).grid(row=0, column=5, padx=6)
        ttk.Button(toolbar, text="Restart", command=lambda: self._run_selected("restart")).grid(row=0, column=6, padx=6)
        ttk.Button(toolbar, text="Health", command=lambda: self._run_selected("health")).grid(row=0, column=7, padx=6)

        columns_button = ttk.Menubutton(toolbar, text="Columns")
        columns_menu = tk.Menu(columns_button, tearoff=False)
        columns_button["menu"] = columns_menu
        self._column_vars: dict[str, tk.BooleanVar] = {}
        for column in ALL_COLUMNS:
            var = tk.BooleanVar(value=True)
            self._column_vars[column] = var
            columns_menu.add_checkbutton(
                label=COLUMN_HEADINGS[column],
                variable=var,
                command=self._apply_visible_columns,
            )
        columns_button.grid(row=0, column=8, padx=6)

        batch_button = ttk.Menubutton(toolbar, text="Batch")
        batch_menu = tk.Menu(batch_button, tearoff=False)
        batch_button["menu"] = batch_menu
        batch_menu.add_command(label="Start visible", command=lambda: self._run_batch("start"))
        batch_menu.add_command(label="Stop visible", command=lambda: self._run_batch("stop"))
        batch_menu.add_command(label="Restart visible", command=lambda: self._run_batch("restart"))
        batch_button.grid(row=0, column=9, padx=6)

        ttk.Label(toolbar, text="Tag").grid(row=0, column=10, sticky="e", padx=(12, 4))
        self.tag_filter = ttk.Combobox(
            toolbar,
            textvariable=self.tag_filter_var,
            values=("All tags",),
            state="readonly",
            width=16,
        )
        self.tag_filter.grid(row=0, column=11, sticky="w", padx=(0, 6))
        self.tag_filter.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Button(toolbar, text="Prompt", command=self._select_prompt_file).grid(row=0, column=12, padx=6)
        ttk.Label(toolbar, textvariable=self.prompt_var).grid(row=0, column=13, sticky="w", padx=(0, 6))

        self.daemon_var = tk.StringVar(value="Daemon: unknown")
        ttk.Label(toolbar, textvariable=self.daemon_var).grid(row=0, column=16, sticky="e", padx=(10, 6))
        ttk.Button(toolbar, text="Start daemon", command=self._start_daemon).grid(row=0, column=17, padx=6)
        ttk.Button(toolbar, text="Stop daemon", command=self._stop_daemon).grid(row=0, column=18, padx=(6, 0))

        main = ttk.PanedWindow(self, orient=tk.VERTICAL)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        table_frame = ttk.Frame(main)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        main.add(table_frame, weight=4)

        self.tree = ttk.Treeview(table_frame, columns=ALL_COLUMNS, show="headings", selectmode="extended")
        for column in ALL_COLUMNS:
            self.tree.heading(column, text=COLUMN_HEADINGS[column])
            self.tree.column(column, width=COLUMN_WIDTHS[column], anchor=tk.W)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self._apply_visible_columns()

        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="Quick benchmark", command=self._run_benchmark_selected)
        self.context_menu.add_command(label="Clone row", command=self._clone_selected)
        self.context_menu.add_command(label="Copy as CLI command", command=self._copy_cli_command)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Open config", command=self._open_config)

        detail_bar = ttk.Frame(table_frame, padding=(0, 8, 0, 0))
        detail_bar.grid(row=1, column=0, sticky="ew")
        ttk.Button(detail_bar, text="Quick benchmark", command=self._run_benchmark_selected).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Clone row", command=self._clone_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Diff selected", command=self._diff_selected).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Copy CLI", command=self._copy_cli_command).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open config", command=self._open_config).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Open logs", command=self._open_logs).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open project", command=self._open_project).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open prompt", command=self._open_prompt_file).pack(side=tk.LEFT)

        log_frame = ttk.Frame(main)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        main.add(log_frame, weight=2)

        ttk.Label(log_frame, text="Activity").grid(row=0, column=0, sticky="w", pady=(8, 2))
        self.activity = scrolledtext.ScrolledText(log_frame, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.activity.grid(row=1, column=0, sticky="nsew")

    def _auto_refresh(self) -> None:
        self.refresh()
        self.after(self.refresh_interval_ms, self._auto_refresh)

    def _apply_visible_columns(self) -> None:
        visible = [column for column in ALL_COLUMNS if self._column_vars[column].get()]
        if not visible:
            self._column_vars["name"].set(True)
            visible = ["name"]
        self.tree["displaycolumns"] = visible

    def _schedule_message_pump(self) -> None:
        self._pump_messages()
        self.after(250, self._schedule_message_pump)

    def _pump_messages(self) -> None:
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            if message == "__REFRESH__":
                self.refresh()
                continue
            self._append_activity(message)

    def _append_activity(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.activity.configure(state=tk.NORMAL)
        self.activity.insert(tk.END, f"[{timestamp}] {message}\n")
        self.activity.see(tk.END)
        self.activity.configure(state=tk.DISABLED)

    def _post_message(self, message: str) -> None:
        self._messages.put(message)

    def refresh(self) -> None:
        selected = self._selected_name
        for item in self.tree.get_children():
            self.tree.delete(item)

        states = list_instances()
        config_names = {name for name, _ in discover_instances()}
        all_names = sorted(set(states) | config_names)
        active_tag = self.tag_filter_var.get()
        all_tags: set[str] = set()
        visible_names: set[str] = set()
        latest_results = latest_benchmark_results()

        for name in all_names:
            state = states.get(name)
            try:
                config = get_instance_config(name)
                port = str(config.server.port)
                backend = config.gpu.backend
                tags = ", ".join(config.tags) if config.tags else "-"
                all_tags.update(config.tags)
                model = str(config.model.path)
                runtime_args = " ".join(config.args) if config.args else "-"
            except Exception:
                port = "-"
                backend = "-"
                tags = "-"
                model = "-"
                runtime_args = "-"

            if active_tag != "All tags" and active_tag not in {tag.strip() for tag in tags.split(",")}:
                continue
            visible_names.add(name)

            status = state.status.value if state else InstanceStatus.STOPPED.value
            health = state.health.value if state else "unknown"
            pid = str(state.pid) if state and state.pid else "-"
            uptime = state.uptime_str if state else "-"
            benchmark = latest_results.get(name)
            if benchmark and benchmark.status == "ok":
                tps = format_metric(benchmark.tokens_per_second)
                latency = format_metric(benchmark.latency_ms, 0)
                vram = format_metric(benchmark.vram_mb, 0)
                prompt = benchmark.prompt_file
            elif benchmark:
                tps = "failed"
                latency = "-"
                vram = format_metric(benchmark.vram_mb, 0)
                prompt = benchmark.prompt_file
            else:
                tps = "-"
                latency = "-"
                vram = "-"
                prompt = "-"

            self.tree.insert(
                "",
                tk.END,
                iid=name,
                values=(
                    name,
                    status,
                    health,
                    pid,
                    port,
                    backend,
                    tags,
                    tps,
                    latency,
                    vram,
                    prompt,
                    model,
                    runtime_args,
                    uptime,
                ),
            )

        filter_values = ("All tags", *sorted(all_tags))
        self.tag_filter.configure(values=filter_values)
        if self.tag_filter_var.get() not in filter_values:
            self.tag_filter_var.set("All tags")
        self.prompt_var.set(self.benchmark_settings.prompt_file.name)

        if selected and selected in visible_names:
            self.tree.selection_set(selected)
            self.tree.focus(selected)

        daemon = get_daemon_status()
        if daemon.running:
            self.daemon_var.set(f"Daemon: running (PID {daemon.pid})")
        else:
            self.daemon_var.set("Daemon: stopped")

    def _on_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        self._selected_name = selection[0] if selection else None

    def _tree_column_from_event(self, event: tk.Event) -> str | None:
        column_id = self.tree.identify_column(event.x)
        if not column_id.startswith("#"):
            return None
        try:
            display_index = int(column_id[1:]) - 1
        except ValueError:
            return None
        display_columns = self.tree["displaycolumns"]
        columns = list(ALL_COLUMNS) if display_columns == "#all" else list(display_columns)
        if display_index < 0 or display_index >= len(columns):
            return None
        return columns[display_index]

    def _on_tree_double_click(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        self._selected_name = item
        if self._tree_column_from_event(event) == "args":
            self._edit_args_inline(item, event)
            return
        self._open_config()

    def _show_context_menu(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
            self._selected_name = item
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _selected_instance(self) -> str | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No model selected", "Select a model instance first.")
            return None
        return selection[0]

    def _selected_instances(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.tree.selection())

    def _run_background(self, label: str, action: Callable[[], str | None]) -> None:
        def worker() -> None:
            try:
                result = action()
                self._post_message(result or f"{label} completed.")
            except Exception as exc:
                self._post_message(f"{label} failed: {exc}")
            finally:
                self._post_message("__REFRESH__")

        self._post_message(f"{label} started.")
        threading.Thread(target=worker, daemon=True).start()

    def _run_selected(self, action_name: str) -> None:
        name = self._selected_instance()
        if not name:
            return

        def action() -> str:
            if action_name == "start":
                state = start_instance(name)
                return f"Started {name} (PID {state.pid})."
            if action_name == "stop":
                stop_instance(name)
                return f"Stopped {name}."
            if action_name == "restart":
                state = restart_instance(name)
                return f"Restarted {name} (PID {state.pid})."
            if action_name == "health":
                result = check_instance_health(name)
                detail = result.error_message or f"{result.response_time_ms or 0:.1f} ms"
                return f"Health {name}: {result.status.value} ({detail})."
            raise ValueError(f"Unknown action: {action_name}")

        self._run_background(f"{action_name} {name}", action)

    def _run_batch(self, action_name: str) -> None:
        names = tuple(str(item) for item in self.tree.get_children())
        if not names:
            messagebox.showinfo("No visible models", "No visible model instances match the current filter.")
            return

        def action() -> str:
            completed: list[str] = []
            for name in names:
                if action_name == "start":
                    start_instance(name)
                elif action_name == "stop":
                    stop_instance(name)
                elif action_name == "restart":
                    restart_instance(name)
                else:
                    raise ValueError(f"Unknown batch action: {action_name}")
                completed.append(name)
            return f"{action_name.capitalize()}ed {len(completed)} visible instance(s)."

        label = f"{action_name} {len(names)} visible instance(s)"
        self._run_background(label, action)

    def _run_benchmark_selected(self) -> None:
        name = self._selected_instance()
        if not name:
            return

        def action() -> str:
            config = get_instance_config(name)
            result = quick_benchmark_instance(config, self.benchmark_settings)
            return format_benchmark_message(result)

        self._run_background(f"benchmark {name}", action)

    def _select_prompt_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select benchmark prompt",
            initialdir=str(self.benchmark_settings.prompt_file.parent),
            filetypes=(("Text and Markdown", "*.txt *.md"), ("All files", "*.*")),
        )
        if not path:
            return
        self.benchmark_settings = BenchmarkSettings(
            prompt_file=Path(path),
            max_tokens=self.benchmark_settings.max_tokens,
        )
        save_benchmark_settings(self.benchmark_settings, self.project_root)
        self.prompt_var.set(self.benchmark_settings.prompt_file.name)
        self._post_message(f"Benchmark prompt set to {self.benchmark_settings.prompt_file.name}.")

    def _open_prompt_file(self) -> None:
        self._open_path(self.benchmark_settings.prompt_file)

    def _edit_args_inline(self, name: str, event: tk.Event) -> None:
        column = "args"
        display_columns = self.tree["displaycolumns"]
        columns = list(ALL_COLUMNS) if display_columns == "#all" else list(display_columns)
        if column not in columns:
            return
        column_id = f"#{columns.index(column) + 1}"
        bbox = self.tree.bbox(name, column_id)
        if not bbox:
            return
        x, y, width, height = bbox
        current = self.tree.set(name, column)
        if current == "-":
            current = ""

        editor = ttk.Entry(self.tree)
        editor.insert(0, current)
        editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        closed = {"value": False}

        def close(save: bool) -> None:
            if closed["value"]:
                return
            closed["value"] = True
            value = editor.get()
            editor.destroy()
            if save:
                self._save_runtime_args(name, value)

        editor.bind("<Return>", lambda _event: close(True))
        editor.bind("<Escape>", lambda _event: close(False))
        editor.bind("<FocusOut>", lambda _event: close(True))

    def _save_runtime_args(self, name: str, value: str) -> None:
        try:
            config = get_instance_config(name)
            config.args = shlex.split(value) if value.strip() else []
            save_config(config)
            state = list_instances().get(name)
        except Exception as exc:
            messagebox.showerror("Save runtime args failed", str(exc))
            self.refresh()
            return

        if state and state.status == InstanceStatus.RUNNING:
            self._run_background(
                f"restart {name}",
                lambda: f"Restarted {name} after runtime args edit (PID {restart_instance(name).pid}).",
            )
        else:
            self._post_message(f"Saved runtime args for {name}.")
            self.refresh()

    def _clone_selected(self) -> None:
        source = self._selected_instance()
        if not source:
            return
        try:
            config = get_instance_config(source)
            new_name = self._next_clone_name(source)
        except Exception as exc:
            messagebox.showerror("Clone failed", str(exc))
            return

        requested = simpledialog.askstring(
            "Clone row",
            "New instance name:",
            initialvalue=new_name,
            parent=self,
        )
        if not requested:
            return
        new_name = requested.strip().lower()
        target = self.project_root / "instances" / new_name / "config.json"
        if target.exists():
            messagebox.showerror("Clone failed", f"Instance '{new_name}' already exists.")
            return

        try:
            preferred_port = min(config.server.port + 1, 65535)
            suggested_port = suggest_port_for_instance(
                new_name,
                preferred_port=preferred_port,
                port_range=(preferred_port, 65535),
                host=config.server.host,
            )
            clone = config.model_copy(deep=True, update={"name": new_name})
            clone.server.port = suggested_port or preferred_port
            save_config(clone)
        except Exception as exc:
            messagebox.showerror("Clone failed", str(exc))
            return

        self._post_message(f"Cloned {source} to {new_name} on port {clone.server.port}.")
        self.refresh()

    def _next_clone_name(self, source: str) -> str:
        existing = {name for name, _ in discover_instances()}
        base = f"{source}_clone"
        if base not in existing:
            return base
        index = 2
        while f"{base}{index}" in existing:
            index += 1
        return f"{base}{index}"

    def _diff_selected(self) -> None:
        selected = self._selected_instances()
        if len(selected) != 2:
            messagebox.showinfo("Select two rows", "Select exactly two model rows for config diff.")
            return
        try:
            left = get_instance_config(selected[0])
            right = get_instance_config(selected[1])
        except Exception as exc:
            messagebox.showerror("Diff failed", str(exc))
            return

        left_args = [*left.args]
        right_args = [*right.args]
        diff = "\n".join(
            difflib.unified_diff(
                [arg + "\n" for arg in left_args],
                [arg + "\n" for arg in right_args],
                fromfile=left.name,
                tofile=right.name,
                lineterm="",
            )
        )
        self._open_diff_window(left.name, right.name, left_args, right_args, diff or "Runtime args are identical.")

    def _open_diff_window(
        self,
        left_name: str,
        right_name: str,
        left_args: list[str],
        right_args: list[str],
        diff: str,
    ) -> None:
        window = tk.Toplevel(self)
        window.title(f"Runtime args diff: {left_name} vs {right_name}")
        window.geometry("900x520")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(window, orient=tk.VERTICAL)
        panes.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        side_by_side = ttk.PanedWindow(panes, orient=tk.HORIZONTAL)
        panes.add(side_by_side, weight=2)
        for label, args in ((left_name, left_args), (right_name, right_args)):
            frame = ttk.Frame(side_by_side)
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w")
            text = scrolledtext.ScrolledText(frame, height=10, wrap=tk.NONE)
            text.grid(row=1, column=0, sticky="nsew")
            text.insert(tk.END, "\n".join(args) or "(no runtime args)")
            text.configure(state=tk.DISABLED)
            side_by_side.add(frame, weight=1)

        diff_text = scrolledtext.ScrolledText(panes, height=10, wrap=tk.NONE)
        diff_text.insert(tk.END, diff)
        diff_text.configure(state=tk.DISABLED)
        panes.add(diff_text, weight=1)

    def _copy_cli_command(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        try:
            command = format_command(build_command(get_instance_config(name)))
        except Exception as exc:
            messagebox.showerror("Copy CLI failed", str(exc))
            return
        self.clipboard_clear()
        self.clipboard_append(command)
        self._post_message(f"Copied llama.cpp CLI command for {name}.")

    def _start_daemon(self) -> None:
        def action() -> str:
            status = get_daemon_status()
            if status.running:
                return f"Daemon already running (PID {status.pid})."
            start_daemon(foreground=False)
            return "Daemon started."

        self._run_background("start daemon", action)

    def _stop_daemon(self) -> None:
        def action() -> str:
            status = get_daemon_status()
            if not status.running:
                return "Daemon already stopped."
            if stop_daemon():
                return "Daemon stopped."
            raise RuntimeError("Daemon did not stop cleanly")

        self._run_background("stop daemon", action)

    def _open_config(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        path = self.project_root / "instances" / name / "config.json"
        self._open_path(path)

    def _open_logs(self) -> None:
        name = self._selected_instance()
        if not name:
            return
        self._open_path(self.project_root / "logs" / name)

    def _open_project(self) -> None:
        self._open_path(self.project_root)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showwarning("Path not found", str(path))
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Open failed", str(exc))

    def _open_add_dialog(self) -> None:
        AddModelDialog(self, on_saved=self._on_model_saved)

    def _on_model_saved(self, config: InstanceConfig) -> None:
        self._post_message(f"Configured model instance {config.name}.")
        self.refresh()

    def _open_binary_dialog(self) -> None:
        InstallBinaryDialog(self, on_install=self._install_binary)

    def _check_vulkan_binary(self) -> None:
        try:
            from llama_orchestrator.binaries import BinaryManager

            manager = BinaryManager(self.project_root)
            installed = [
                binary for binary in manager.list_installed()
                if binary.variant == VULKAN_VARIANT
            ]
        except Exception as exc:
            self._post_message(f"Could not inspect installed binaries: {exc}")
            return

        if not installed:
            self._post_message(
                "No Vulkan llama-server binary is installed. Use Install Vulkan "
                "to download win-vulkan-x64."
            )

    def _install_binary(self, version: str, variant: str, set_default: bool) -> None:
        def action() -> str:
            from llama_orchestrator.binaries import BinaryManager

            manager = BinaryManager(self.project_root)
            binary = manager.install(
                version=version,
                variant=variant,  # type: ignore[arg-type]
                set_as_default=set_default,
            )
            return (
                f"Installed llama-server {binary.version} ({binary.variant}) "
                f"as {binary.id}."
            )

        self._run_background(f"install {variant} binary", action)

    def _apply_default_args(self) -> None:
        name = self._selected_instance()
        if not name:
            return

        try:
            config = get_instance_config(name)
            config.args = apply_managed_runtime_args(
                list(config.args),
                no_mmproj=True,
                reasoning="off",
                flash_attn="auto",
            )
            if (
                config.gpu.backend == "vulkan"
                and (config.binary is None or config.binary.variant != VULKAN_VARIANT)
            ):
                config.binary = BinaryConfig(version="latest", variant=VULKAN_VARIANT)
            save_config(config)
        except Exception as exc:
            messagebox.showerror("Apply args failed", str(exc))
            return

        self._post_message(
            f"Applied runtime args to {name}: {' '.join(DEFAULT_RUNTIME_ARGS)}"
        )
        self.refresh()


class AddModelDialog(tk.Toplevel):
    """Dialog for creating a model instance config."""

    def __init__(
        self,
        master: LlamaOrchestratorGui,
        on_saved: Callable[[InstanceConfig], None],
    ) -> None:
        super().__init__(master)
        self.title("Add model")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved

        self.name_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.port_var = tk.StringVar(value="8001")
        self.backend_var = tk.StringVar(value="vulkan")
        self.device_var = tk.StringVar(value="0")
        self.layers_var = tk.StringVar(value="0")
        self.context_var = tk.StringVar(value="4096")
        self.threads_var = tk.StringVar(value="8")
        self.tags_var = tk.StringVar()
        self.no_mmproj_var = tk.BooleanVar(value=True)
        self.reasoning_var = tk.StringVar(value="off")
        self.flash_attn_var = tk.StringVar(value="auto")

        self._build()
        self.name_entry.focus_set()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        self.name_entry = self._entry(frame, "Name", self.name_var, 0)
        model_entry = self._entry(frame, "GGUF model", self.model_var, 1, width=54)
        ttk.Button(frame, text="Browse", command=self._browse_model).grid(row=1, column=2, padx=(6, 0))
        model_entry.focus_set()

        self._entry(frame, "Port", self.port_var, 2)
        backend = ttk.Combobox(
            frame,
            textvariable=self.backend_var,
            values=("cpu", "vulkan", "cuda", "hip", "metal"),
            state="readonly",
            width=18,
        )
        ttk.Label(frame, text="Backend").grid(row=3, column=0, sticky="w", pady=4)
        backend.grid(row=3, column=1, sticky="w", pady=4)

        self._entry(frame, "Device ID", self.device_var, 4)
        self._entry(frame, "GPU layers", self.layers_var, 5)
        self._entry(frame, "Context", self.context_var, 6)
        self._entry(frame, "Threads", self.threads_var, 7)
        self._entry(frame, "Tags", self.tags_var, 8)

        ttk.Checkbutton(
            frame,
            text="--no-mmproj",
            variable=self.no_mmproj_var,
        ).grid(row=9, column=1, sticky="w", pady=4)

        reasoning = ttk.Combobox(
            frame,
            textvariable=self.reasoning_var,
            values=("off", "auto"),
            width=18,
        )
        ttk.Label(frame, text="--reasoning").grid(row=10, column=0, sticky="w", pady=4)
        reasoning.grid(row=10, column=1, sticky="w", pady=4)

        flash_attn = ttk.Combobox(
            frame,
            textvariable=self.flash_attn_var,
            values=("auto", "on", "off"),
            width=18,
        )
        ttk.Label(frame, text="--flash-attn").grid(row=11, column=0, sticky="w", pady=4)
        flash_attn.grid(row=11, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=12, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(0, 8))

    def _entry(
        self,
        frame: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        row: int,
        width: int = 24,
    ) -> ttk.Entry:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(frame, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        return entry

    def _browse_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Select GGUF model",
            filetypes=(("GGUF models", "*.gguf"), ("All files", "*.*")),
        )
        if path:
            self.model_var.set(path)

    def _save(self) -> None:
        try:
            config = InstanceConfig(
                name=self.name_var.get().strip(),
                binary=BinaryConfig(version="latest", variant=VULKAN_VARIANT)
                if self.backend_var.get() == "vulkan"
                else None,
                model=ModelConfig(
                    path=Path(self.model_var.get().strip()),
                    context_size=int(self.context_var.get()),
                    threads=int(self.threads_var.get()),
                ),
                server=ServerConfig(port=int(self.port_var.get())),
                gpu=GpuConfig(
                    backend=self.backend_var.get(),  # type: ignore[arg-type]
                    device_id=int(self.device_var.get()),
                    layers=int(self.layers_var.get()),
                ),
                args=apply_managed_runtime_args(
                    [],
                    no_mmproj=self.no_mmproj_var.get(),
                    reasoning=self.reasoning_var.get().strip(),
                    flash_attn=self.flash_attn_var.get().strip(),
                ),
                tags=parse_tag_string(self.tags_var.get()),
            )
            target = get_project_root() / "instances" / config.name / "config.json"
            if target.exists():
                raise ValueError(f"Instance '{config.name}' already exists")
            save_config(config)
        except Exception as exc:
            messagebox.showerror("Invalid model config", str(exc))
            return

        self.on_saved(config)
        self.destroy()


class InstallBinaryDialog(tk.Toplevel):
    """Dialog for installing llama.cpp binaries from GitHub releases."""

    def __init__(
        self,
        master: LlamaOrchestratorGui,
        on_install: Callable[[str, str, bool], None],
    ) -> None:
        super().__init__(master)
        self.title("Install llama-server")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_install = on_install

        self.version_var = tk.StringVar(value="latest")
        self.variant_var = tk.StringVar(value=VULKAN_VARIANT)
        self.default_var = tk.BooleanVar(value=True)

        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Version").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.version_var, width=24).grid(
            row=0,
            column=1,
            sticky="w",
            pady=4,
        )

        ttk.Label(frame, text="Variant").grid(row=1, column=0, sticky="w", pady=4)
        variant = ttk.Combobox(
            frame,
            textvariable=self.variant_var,
            values=(
                "win-vulkan-x64",
                "win-cpu-x64",
                "win-cuda-12.4-x64",
                "win-cuda-13.1-x64",
                "win-hip-radeon-x64",
                "win-sycl-x64",
            ),
            state="readonly",
            width=24,
        )
        variant.grid(row=1, column=1, sticky="w", pady=4)

        ttk.Checkbutton(
            frame,
            text="Set as default binary",
            variable=self.default_var,
        ).grid(row=2, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Install", command=self._install).pack(side=tk.RIGHT, padx=(0, 8))

    def _install(self) -> None:
        version = self.version_var.get().strip() or "latest"
        variant = self.variant_var.get().strip() or VULKAN_VARIANT
        self.on_install(version, variant, self.default_var.get())
        self.destroy()


def launch_gui() -> None:
    """Launch the desktop GUI."""
    app = LlamaOrchestratorGui()
    app.mainloop()
