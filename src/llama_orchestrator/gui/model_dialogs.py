"""Model instance dialog classes.

Extracted from app.py to reduce context fill during independent refactoring.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from llama_orchestrator.config import (
    BinaryConfig,
    GpuConfig,
    InstanceConfig,
    ModelConfig,
    ServerConfig,
    discover_instances,
    get_project_root,
    load_all_instances,
    save_config,
)
from llama_orchestrator.gui_state import GuiSettings, save_gui_settings
from llama_orchestrator.health.ports import find_free_port
from llama_orchestrator.hf_import import (
    ImportedModelSelection,
    build_add_model_prefill,
)
from llama_orchestrator.model_metadata import build_model_metadata

# --- Local utility functions (extracted from app.py) ---

VULKAN_VARIANT = "win-vulkan-x64"


def parse_tag_string(value: str) -> list[str]:
    """Parse a comma/space separated tag string for config storage."""
    tags: list[str] = []
    seen: set[str] = set()
    for tag in value.replace(",", " ").split():
        clean = normalize_config_token(tag)
        if clean and clean not in seen:
            tags.append(clean)
            seen.add(clean)
    return tags


def normalize_config_token(value: str, *, fallback: str = "") -> str:
    """Convert user-facing text into the strict config token format."""
    import re
    import unicodedata

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    clean = ascii_value.strip().lower()
    clean = re.sub(r"[^a-z0-9_-]+", "-", clean)
    clean = re.sub(r"[-_]{2,}", "-", clean).strip("-_")
    return clean or fallback


def unique_instance_name(label: str, existing_names: set[str], *, fallback: str = "model") -> str:
    """Return a unique immutable alias derived from a human model label."""
    base = normalize_config_token(label, fallback=fallback)
    candidate = base
    index = 2
    while candidate in existing_names:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def instance_alias_exists(name: str) -> bool:
    """Check whether an immutable instance alias is already present."""
    return any(existing_name == name for existing_name, _ in discover_instances())


def normalize_model_path_for_config(path: Path) -> Path:
    """Prefer project-relative model paths when the file lives under the repo root."""

    raw_path = path.expanduser()
    project_root = get_project_root().resolve()
    resolved = raw_path if raw_path.is_absolute() else (project_root / raw_path)
    resolved = resolved.resolve()
    try:
        return resolved.relative_to(project_root)
    except ValueError:
        return resolved


def suggest_add_model_port(min_port: int, host: str = "127.0.0.1") -> int:
    """Find the next port suitable for a new GUI-created model."""

    used_ports = {
        config.server.port
        for config in load_all_instances().values()
        if min_port <= config.server.port <= 65535
    }
    port = find_free_port(
        start_port=min_port,
        end_port=65535,
        host=host,
        exclude_ports=used_ports,
    )
    return port or min_port


def suggest_next_add_model_port(
    current_port: int,
    min_port: int,
    host: str = "127.0.0.1",
) -> int:
    """Find the next suitable port after the current Add model port."""

    start_port = max(current_port + 1, min_port, 1024)
    if start_port > 65535:
        start_port = max(min_port, 1024)
    return suggest_add_model_port(start_port, host=host)


def apply_managed_runtime_args(
    args: list[str],
    *,
    no_mmproj: bool = True,
    reasoning: str = "off",
    flash_attn: str = "auto",
) -> list[str]:
    """Apply GUI-managed llama-server runtime args without duplicating flags."""
    managed_flag_args = {"--no-mmproj", "--flash-attn", "--reasoning"}
    managed_value_args = {"--flash-attn", "--reasoning"}
    cleaned: list[str] = []
    skip_next = False

    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in managed_flag_args:
            continue
        if arg in managed_value_args:
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


# --- Dialog classes ---


class AddModelDialog(tk.Toplevel):
    """Dialog for creating a model instance config."""

    def __init__(
        self,
        master: tk.Tk,
        on_saved: Callable[[InstanceConfig], None],
    ) -> None:
        super().__init__(master)
        self.title("Add model")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_saved = on_saved
        self.gui_settings: GuiSettings = master.gui_settings  # type: ignore[attr-defined]

        self.name_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.port_var = tk.StringVar(value=str(suggest_add_model_port(self.gui_settings.add_model_min_port)))
        self.min_port_var = tk.StringVar(value=str(self.gui_settings.add_model_min_port))
        self.backend_var = tk.StringVar(value="vulkan")
        self.device_var = tk.StringVar(value="0")
        self.layers_var = tk.StringVar(value="0")
        self.context_var = tk.StringVar(value="4096")
        self.threads_var = tk.StringVar(value="8")
        self.tags_var = tk.StringVar()
        self.no_mmproj_var = tk.BooleanVar(value=True)
        self.reasoning_var = tk.StringVar(value="off")
        self.flash_attn_var = tk.StringVar(value="auto")
        self._hf_selection: ImportedModelSelection | None = None

        self._build()
        self.name_entry.focus_set()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        self.name_entry = self._entry(frame, "Name", self.name_var, 0)
        model_entry = self._entry(frame, "GGUF model", self.model_var, 1, width=54)
        ttk.Button(frame, text="Browse", command=self._browse_model).grid(row=1, column=2, padx=(6, 0))
        ttk.Button(
            frame,
            text="Import from Hugging Face...",
            command=self._open_hf_import_dialog,
        ).grid(row=2, column=1, sticky="w", pady=(0, 4))
        model_entry.focus_set()

        self._entry(frame, "Port", self.port_var, 3)
        port_buttons = ttk.Frame(frame)
        port_buttons.grid(row=3, column=2, padx=(6, 0), sticky="w")
        ttk.Button(
            port_buttons,
            text="Find free",
            command=self._find_free_port,
        ).pack(side=tk.LEFT)
        ttk.Button(
            port_buttons,
            text="Configure",
            command=self._configure_port_scan,
        ).pack(side=tk.LEFT, padx=(6, 0))
        backend = ttk.Combobox(
            frame,
            textvariable=self.backend_var,
            values=("cpu", "vulkan", "cuda", "hip", "metal"),
            state="readonly",
            width=18,
        )
        ttk.Label(frame, text="Backend").grid(row=4, column=0, sticky="w", pady=4)
        backend.grid(row=4, column=1, sticky="w", pady=4)

        self._entry(frame, "Device ID", self.device_var, 5)
        self._entry(frame, "GPU layers", self.layers_var, 6)
        self._entry(frame, "Context", self.context_var, 7)
        self._entry(frame, "Threads", self.threads_var, 8)
        self._entry(frame, "Tags", self.tags_var, 9)

        ttk.Checkbutton(
            frame,
            text="--no-mmproj",
            variable=self.no_mmproj_var,
        ).grid(row=10, column=1, sticky="w", pady=4)

        reasoning = ttk.Combobox(
            frame,
            textvariable=self.reasoning_var,
            values=("off", "auto"),
            width=18,
        )
        ttk.Label(frame, text="--reasoning").grid(row=11, column=0, sticky="w", pady=4)
        reasoning.grid(row=11, column=1, sticky="w", pady=4)

        flash_attn = ttk.Combobox(
            frame,
            textvariable=self.flash_attn_var,
            values=("auto", "on", "off"),
            width=18,
        )
        ttk.Label(frame, text="--flash-attn").grid(row=12, column=0, sticky="w", pady=4)
        flash_attn.grid(row=12, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=13, column=0, columnspan=3, sticky="e", pady=(12, 0))
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

    def _open_hf_import_dialog(self) -> None:
        from llama_orchestrator.gui.app import HuggingFaceImportDialog

        HuggingFaceImportDialog(self, on_use=self._apply_hf_import_selection)

    def _apply_hf_import_selection(self, selection: ImportedModelSelection) -> None:
        name, model_path, tags = build_add_model_prefill(selection)
        self.name_var.set(name)
        self.model_var.set(model_path)
        self._hf_selection = selection
        merged_tags = parse_tag_string(
            " ".join([self.tags_var.get().strip(), *tags]).strip()
        )
        self.tags_var.set(", ".join(merged_tags))

    def _configure_port_scan(self) -> None:
        try:
            current_min_port = int(self.min_port_var.get())
        except ValueError:
            current_min_port = self.gui_settings.add_model_min_port

        dialog = AddModelPortSettingsDialog(self, current_min_port)
        self.wait_window(dialog)
        if dialog.result is None:
            return

        self.min_port_var.set(str(dialog.result))
        self.gui_settings = replace(self.gui_settings, add_model_min_port=dialog.result)
        save_gui_settings(self.gui_settings)
        self.master.gui_settings = self.gui_settings  # type: ignore[attr-defined]
        self.port_var.set(str(suggest_add_model_port(dialog.result)))

    def _find_free_port(self) -> None:
        try:
            current_port = int(self.port_var.get().strip())
        except ValueError:
            current_port = self.gui_settings.add_model_min_port - 1

        try:
            min_port = int(self.min_port_var.get().strip())
        except ValueError:
            min_port = self.gui_settings.add_model_min_port

        self.port_var.set(str(suggest_next_add_model_port(current_port, min_port)))

    def _save(self) -> None:
        try:
            display_name = self.name_var.get().strip()
            if not display_name:
                raise ValueError("Name cannot be blank")
            name = unique_instance_name(display_name, {existing for existing, _ in discover_instances()})
            model_path = normalize_model_path_for_config(Path(self.model_var.get().strip()))
            config = InstanceConfig(
                name=name,
                display_name=display_name,
                binary=BinaryConfig(version="latest", variant=VULKAN_VARIANT)
                if self.backend_var.get() == "vulkan"
                else None,
                model=ModelConfig(
                    path=model_path,
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
            try:
                config.model_metadata = build_model_metadata(config, imported_selection=self._hf_selection)
            except Exception:
                # Metadata must stay optional and never block profile creation.
                config.model_metadata = None
            if instance_alias_exists(config.name):
                raise ValueError(f"Instance '{config.name}' already exists")
            save_config(config)
        except Exception as exc:
            messagebox.showerror("Invalid model config", str(exc))
            return

        self.on_saved(config)
        self.destroy()


class AddModelPortSettingsDialog(tk.Toplevel):
    """Dialog for configuring the minimum port for model instances."""

    def __init__(self, parent: tk.Misc, current_min_port: int) -> None:
        super().__init__(parent)
        self.title("Port settings")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: int | None = None

        self._build(current_min_port)

    def _build(self, current_min_port: int) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Minimum port:").grid(row=0, column=0, sticky="w", pady=4)
        self.port_var = tk.StringVar(value=str(current_min_port))
        entry = ttk.Entry(frame, textvariable=self.port_var, width=10)
        entry.grid(row=0, column=1, sticky="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK", command=self._ok).pack(side=tk.RIGHT, padx=(0, 8))

    def _ok(self) -> None:
        try:
            value = int(self.port_var.get().strip())
            if value < 1024 or value > 65535:
                raise ValueError("Port must be between 1024 and 65535")
            self.result = value
        except ValueError as exc:
            messagebox.showerror("Invalid port", str(exc))
            return
        self.destroy()
