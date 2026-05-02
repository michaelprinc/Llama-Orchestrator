"""
Tkinter GUI for llama-orchestrator model instance management.

The GUI intentionally uses only the Python standard library so the desktop
management surface works on Windows without adding runtime dependencies.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

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
    list_instances,
    restart_instance,
    start_instance,
    stop_instance,
)
from llama_orchestrator.health import check_instance_health

DEFAULT_RUNTIME_ARGS = ["--no-mmproj", "--reasoning", "off", "--flash-attn", "auto"]
MANAGED_VALUE_ARGS = {"--reasoning", "--flash-attn"}
MANAGED_FLAG_ARGS = {"--no-mmproj"}
VULKAN_VARIANT = "win-vulkan-x64"


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
        toolbar.columnconfigure(6, weight=1)

        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(toolbar, text="Add model", command=self._open_add_dialog).grid(row=0, column=1, padx=6)
        ttk.Button(toolbar, text="Apply args", command=self._apply_default_args).grid(row=0, column=2, padx=6)
        ttk.Button(toolbar, text="Install Vulkan", command=self._open_binary_dialog).grid(row=0, column=3, padx=6)
        ttk.Button(toolbar, text="Start", command=lambda: self._run_selected("start")).grid(row=0, column=4, padx=6)
        ttk.Button(toolbar, text="Stop", command=lambda: self._run_selected("stop")).grid(row=0, column=5, padx=6)
        ttk.Button(toolbar, text="Restart", command=lambda: self._run_selected("restart")).grid(row=0, column=6, padx=6)
        ttk.Button(toolbar, text="Health", command=lambda: self._run_selected("health")).grid(row=0, column=7, padx=6)

        self.daemon_var = tk.StringVar(value="Daemon: unknown")
        ttk.Label(toolbar, textvariable=self.daemon_var).grid(row=0, column=8, sticky="e", padx=(10, 6))
        ttk.Button(toolbar, text="Start daemon", command=self._start_daemon).grid(row=0, column=9, padx=6)
        ttk.Button(toolbar, text="Stop daemon", command=self._stop_daemon).grid(row=0, column=10, padx=(6, 0))

        main = ttk.PanedWindow(self, orient=tk.VERTICAL)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        table_frame = ttk.Frame(main)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        main.add(table_frame, weight=4)

        columns = ("name", "status", "health", "pid", "port", "backend", "model", "args", "uptime")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "name": "Name",
            "status": "Status",
            "health": "Health",
            "pid": "PID",
            "port": "Port",
            "backend": "Backend",
            "model": "Model",
            "args": "Runtime args",
            "uptime": "Uptime",
        }
        widths = {
            "name": 150,
            "status": 100,
            "health": 120,
            "pid": 80,
            "port": 80,
            "backend": 100,
            "model": 300,
            "args": 260,
            "uptime": 100,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor=tk.W)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self._open_config())

        detail_bar = ttk.Frame(table_frame, padding=(0, 8, 0, 0))
        detail_bar.grid(row=1, column=0, sticky="ew")
        ttk.Button(detail_bar, text="Open config", command=self._open_config).pack(side=tk.LEFT)
        ttk.Button(detail_bar, text="Open logs", command=self._open_logs).pack(side=tk.LEFT, padx=6)
        ttk.Button(detail_bar, text="Open project", command=self._open_project).pack(side=tk.LEFT, padx=6)

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

        for name in all_names:
            state = states.get(name)
            try:
                config = get_instance_config(name)
                port = str(config.server.port)
                backend = config.gpu.backend
                model = str(config.model.path)
                runtime_args = " ".join(config.args) if config.args else "-"
            except Exception:
                port = "-"
                backend = "-"
                model = "-"
                runtime_args = "-"

            status = state.status.value if state else InstanceStatus.STOPPED.value
            health = state.health.value if state else "unknown"
            pid = str(state.pid) if state and state.pid else "-"
            uptime = state.uptime_str if state else "-"

            self.tree.insert(
                "",
                tk.END,
                iid=name,
                values=(name, status, health, pid, port, backend, model, runtime_args, uptime),
            )

        if selected and selected in all_names:
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

    def _selected_instance(self) -> str | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No model selected", "Select a model instance first.")
            return None
        return selection[0]

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

        ttk.Checkbutton(
            frame,
            text="--no-mmproj",
            variable=self.no_mmproj_var,
        ).grid(row=8, column=1, sticky="w", pady=4)

        reasoning = ttk.Combobox(
            frame,
            textvariable=self.reasoning_var,
            values=("off", "auto"),
            width=18,
        )
        ttk.Label(frame, text="--reasoning").grid(row=9, column=0, sticky="w", pady=4)
        reasoning.grid(row=9, column=1, sticky="w", pady=4)

        flash_attn = ttk.Combobox(
            frame,
            textvariable=self.flash_attn_var,
            values=("auto", "on", "off"),
            width=18,
        )
        ttk.Label(frame, text="--flash-attn").grid(row=10, column=0, sticky="w", pady=4)
        flash_attn.grid(row=10, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=11, column=0, columnspan=3, sticky="e", pady=(12, 0))
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
