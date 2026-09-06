"""Hugging Face import dialog.

Extracted from app.py to reduce context fill during independent refactoring.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from llama_orchestrator.config.loader import get_project_root
from llama_orchestrator.gui.dataclasses import ImportDialogEvent
from llama_orchestrator.gui.dialogs import ExistingModelFileDialog
from llama_orchestrator.hf_import import (
    DownloadCancelledError,
    DownloadProgress,
    GGUFVariant,
    HuggingFaceImportError,
    HuggingFaceRepoRef,
    HuggingFaceTokenStore,
    ImportedModelSelection,
    ImportSettings,
    download_gguf_variant,
    list_gguf_variants,
    load_import_settings,
    normalize_hf_model_reference,
    parse_gguf_quantization,
    plan_download_target,
    save_import_settings,
    write_import_metadata_sidecar,
)

# --- Helper functions extracted from app.py ---


def format_model_size_gb(size_bytes: int | float) -> str:
    """Format a model size in bytes to a human-readable GB string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_download_bytes(value: int | None) -> str:
    """Format byte counts with a compact base-1024 GB display."""

    if value is None:
        return "-"
    return f"{value / (1024**3):.2f} GB"


def format_download_progress(progress: DownloadProgress) -> str:
    """Render one GGUF download progress line for the import dialog."""

    if progress.total_bytes:
        return (
            f"Downloading {Path(progress.filename).name}: "
            f"{format_download_bytes(progress.downloaded_bytes)} / "
            f"{format_download_bytes(progress.total_bytes)}"
        )
    return (
        f"Downloading {Path(progress.filename).name}: "
        f"{format_download_bytes(progress.downloaded_bytes)}"
    )


def resolve_models_directory_input(value: str) -> Path:
    """Resolve the local models directory against the project root when needed."""

    raw_value = value.strip()
    if not raw_value:
        return get_project_root() / "models"
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return path
    return get_project_root() / path


# --- Dialog classes ---


class HuggingFaceImportDialog(tk.Toplevel):
    """Import a GGUF model from Hugging Face and prefill Add Model fields."""

    def __init__(
        self,
        master: tk.Tk,
        on_use: Callable[[ImportedModelSelection], None],
    ) -> None:
        super().__init__(master)
        self.title("Import model from Hugging Face")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()
        self.on_use = on_use
        self.token_store = HuggingFaceTokenStore()
        self.repo_ref: HuggingFaceRepoRef | None = None
        self.variants: list[GGUFVariant] = []
        self.selected_model: ImportedModelSelection | None = None
        self._events: queue.Queue[ImportDialogEvent] = queue.Queue()
        self._cancel_event: threading.Event | None = None
        self._event_pump_id: str | None = None
        self._busy = False

        settings = load_import_settings()
        self.model_ref_var = tk.StringVar()
        self.local_models_dir_var = tk.StringVar(value=settings.local_models_directory)
        self.token_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Paste a Hugging Face model URL or owner/repo ID.")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.local_models_dir_var.trace_add("write", self._persist_local_models_dir)

        self._build()
        self._refresh_token_status()
        self.protocol("WM_DELETE_WINDOW", self._cancel_or_close)
        self._schedule_pump()

    def destroy(self) -> None:
        if self._event_pump_id is not None:
            with suppress(tk.TclError):
                self.after_cancel(self._event_pump_id)
            self._event_pump_id = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().destroy()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        ttk.Label(frame, text="Model URL or ID").grid(row=0, column=0, sticky="w", pady=4)
        ref_entry = ttk.Entry(frame, textvariable=self.model_ref_var, width=64)
        ref_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.load_button = ttk.Button(frame, text="Load variants", command=self._load_variants)
        self.load_button.grid(row=0, column=2, padx=(8, 0), pady=4)

        token_row = ttk.Frame(frame)
        token_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(token_row, textvariable=self.token_status_var).pack(side=tk.LEFT)
        self.configure_token_button = ttk.Button(token_row, text="Set token...", command=self._configure_token)
        self.configure_token_button.pack(side=tk.LEFT, padx=(10, 6))
        self.remove_token_button = ttk.Button(token_row, text="Remove token", command=self._remove_token)
        self.remove_token_button.pack(side=tk.LEFT)

        columns = ("filename", "size", "quant", "status", "note")
        self.variant_tree = ttk.Treeview(frame, columns=columns, show="headings", height=9)
        headings = {
            "filename": "Filename",
            "size": "Size",
            "quant": "Quantization",
            "status": "Local status",
            "note": "Note",
        }
        widths = {"filename": 320, "size": 90, "quant": 110, "status": 110, "note": 180}
        for column in columns:
            self.variant_tree.heading(column, text=headings[column])
            self.variant_tree.column(column, width=widths[column], anchor="w")
        self.variant_tree.grid(row=2, column=0, columnspan=3, sticky="nsew")
        self.variant_tree.bind("<<TreeviewSelect>>", lambda _event: self._update_actions())
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.variant_tree.yview)
        scrollbar.grid(row=2, column=3, sticky="ns")
        self.variant_tree.configure(yscrollcommand=scrollbar.set)

        ttk.Label(frame, text="Local models directory").grid(row=3, column=0, sticky="w", pady=4)
        self.models_dir_entry = ttk.Entry(frame, textvariable=self.local_models_dir_var, width=64)
        self.models_dir_entry.grid(row=3, column=1, sticky="ew", pady=4)
        self.browse_button = ttk.Button(frame, text="Browse", command=self._browse_models_dir)
        self.browse_button.grid(row=3, column=2, padx=(8, 0), pady=4)

        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(frame, textvariable=self.status_var, wraplength=720, justify=tk.LEFT).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        self.download_button = ttk.Button(buttons, text="Download selected", command=self._download_selected)
        self.download_button.pack(side=tk.RIGHT, padx=(8, 0))
        self.use_button = ttk.Button(buttons, text="Use downloaded model", command=self._use_downloaded_model)
        self.use_button.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel_or_close).pack(side=tk.RIGHT)

        self._update_actions()
        ref_entry.focus_set()

    def _persist_local_models_dir(self, *_args: object) -> None:
        directory = self.local_models_dir_var.get().strip()
        if not directory:
            return
        save_import_settings(ImportSettings(local_models_directory=directory))

    def _schedule_pump(self) -> None:
        self._event_pump_id = self.after(100, self._pump_events)

    def _resolve_models_dir(self) -> Path:
        directory = self.local_models_dir_var.get().strip()
        if directory:
            return resolve_models_directory_input(directory)
        settings = load_import_settings()
        if settings.local_models_directory:
            return resolve_models_directory_input(settings.local_models_directory)
        return resolve_models_directory_input("")

    def _refresh_token_status(self) -> None:
        configured = self.token_store.is_configured()
        self.token_status_var.set(
            "Hugging Face token: configured" if configured else "Hugging Face token: not configured"
        )
        self.remove_token_button.configure(state=tk.NORMAL if configured else tk.DISABLED)

    def _browse_models_dir(self) -> None:
        initial_dir = self.local_models_dir_var.get().strip() or str(get_project_root() / "models")
        selected = filedialog.askdirectory(title="Select models directory", initialdir=initial_dir)
        if selected:
            self.local_models_dir_var.set(selected)

    def _configure_token(self) -> None:
        token = simpledialog.askstring(
            "Hugging Face token",
            "Enter a Hugging Face read token for gated or private models.",
            parent=self,
            show="*",
        )
        if token is None:
            return
        self.status_var.set("Validating Hugging Face token...")
        self._set_busy(True)

        def worker() -> None:
            try:
                self.token_store.save_token(token)
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(ImportDialogEvent(kind="token_saved", message="Hugging Face token saved."))
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _remove_token(self) -> None:
        self.token_store.remove_token()
        self._refresh_token_status()
        self.status_var.set("Hugging Face token removed.")

    def _load_variants(self) -> None:
        if self._busy:
            return
        model_ref = self.model_ref_var.get().strip()
        try:
            normalize_hf_model_reference(model_ref)
        except HuggingFaceImportError as exc:
            messagebox.showerror("Hugging Face import", str(exc), parent=self)
            return
        models_dir = self._resolve_models_dir()
        token = self.token_store.get_token()

        self.repo_ref = None
        self.variants = []
        self.selected_model = None
        self._render_variants()
        self.progress_var.set(0.0)
        self.status_var.set("Loading GGUF variants from Hugging Face...")
        self._set_busy(True)

        def worker() -> None:
            try:
                repo_ref, variants = list_gguf_variants(
                    model_ref,
                    local_models_dir=models_dir,
                    token=token,
                )
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(
                    ImportDialogEvent(
                        kind="variants_loaded",
                        repo_ref=repo_ref,
                        variants=variants,
                        message=f"Loaded {len(variants)} GGUF variant(s).",
                    )
                )
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _download_selected(self) -> None:
        if self._busy:
            return
        variant = self._selected_variant()
        if variant is None or self.repo_ref is None:
            messagebox.showerror("Hugging Face import", "Select one GGUF variant first.", parent=self)
            return
        models_dir = self._resolve_models_dir()
        token = self.token_store.get_token()

        existing_choice: str | None = None
        try:
            plan_download_target(variant.local_path)
        except HuggingFaceImportError:
            existing_choice = ExistingModelFileDialog.ask(self, variant.local_path)
        if existing_choice == "cancel":
            self.status_var.set("Download cancelled before it started.")
            return
        if existing_choice == "use_existing":
            selection = ImportedModelSelection(
                repo_id=self.repo_ref.repo_id,
                filename=variant.filename,
                local_path=variant.local_path,
                quantization=variant.quantization or parse_gguf_quantization(variant.filename),
                size_bytes=variant.size_bytes,
            )
            try:
                self.selected_model = write_import_metadata_sidecar(
                    selection,
                    token=token,
                    model_card_text="",
                )
            except HuggingFaceImportError as exc:
                messagebox.showerror("Hugging Face import", str(exc), parent=self)
                return
            self._set_variant_status(variant.filename, "downloaded")
            self.status_var.set(f"Using existing local model: {variant.local_path}")
            self._update_actions()
            return

        self.selected_model = None
        self.progress_var.set(0.0)
        self.status_var.set(f"Starting download for {Path(variant.filename).name}...")
        self._set_variant_status(variant.filename, "downloading")
        self._set_busy(True)
        self._cancel_event = threading.Event()

        def worker() -> None:
            try:
                selection = download_gguf_variant(
                    self.repo_ref.repo_id,
                    variant.filename,
                    models_dir,
                    token=token,
                    size_bytes=variant.size_bytes,
                    existing_choice=existing_choice,  # type: ignore[arg-type]
                    progress_callback=lambda progress: self._events.put(
                        ImportDialogEvent(kind="progress", progress=progress)
                    ),
                    cancel_check=lambda: self._cancel_event.is_set() if self._cancel_event else False,
                )
            except DownloadCancelledError as exc:
                self._events.put(ImportDialogEvent(kind="cancelled", message=str(exc)))
            except Exception as exc:
                self._events.put(ImportDialogEvent(kind="error", message=str(exc)))
            else:
                self._events.put(
                    ImportDialogEvent(
                        kind="download_complete",
                        selection=selection,
                        message=(
                            f"Downloaded {Path(selection.filename).name} to {selection.local_path}. "
                            "Click Use downloaded model to fill Add model."
                        ),
                    )
                )
            finally:
                self._events.put(ImportDialogEvent(kind="idle"))

        threading.Thread(target=worker, daemon=True).start()

    def _use_downloaded_model(self) -> None:
        if self.selected_model is None:
            variant = self._selected_variant()
            if variant is None or self.repo_ref is None or variant.local_status != "downloaded":
                messagebox.showerror(
                    "Hugging Face import",
                    "Download or reuse one GGUF variant first.",
                    parent=self,
                )
                return
            selection = ImportedModelSelection(
                repo_id=self.repo_ref.repo_id,
                filename=variant.filename,
                local_path=variant.local_path,
                quantization=variant.quantization or parse_gguf_quantization(variant.filename),
                size_bytes=variant.size_bytes,
            )
            try:
                self.selected_model = write_import_metadata_sidecar(
                    selection,
                    token=self.token_store.get_token(),
                    model_card_text="",
                )
            except HuggingFaceImportError as exc:
                messagebox.showerror("Hugging Face import", str(exc), parent=self)
                return
        self.on_use(self.selected_model)
        self.destroy()

    def _cancel_or_close(self) -> None:
        if self._busy and self._cancel_event is not None:
            self._cancel_event.set()
            self.status_var.set("Cancelling download...")
            return
        self.destroy()

    def _pump_events(self) -> None:
        self._event_pump_id = None
        if not self.winfo_exists():
            return
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if event.kind == "idle":
                self._cancel_event = None
                self._set_busy(False)
            elif event.kind == "variants_loaded":
                self.repo_ref = event.repo_ref
                self.variants = event.variants or []
                self._render_variants(preferred_filename=self.repo_ref.filename if self.repo_ref else None)
                self.status_var.set(event.message)
            elif event.kind == "progress" and event.progress is not None:
                progress = event.progress
                if progress.total_bytes:
                    self.progress_var.set((progress.downloaded_bytes / progress.total_bytes) * 100)
                else:
                    self.progress_var.set(0.0)
                self.status_var.set(format_download_progress(progress))
            elif event.kind == "download_complete" and event.selection is not None:
                self.selected_model = event.selection
                self.progress_var.set(100.0)
                self._set_variant_status(event.selection.filename, "downloaded")
                self.status_var.set(event.message)
            elif event.kind == "token_saved":
                self._refresh_token_status()
                self.status_var.set(event.message)
            elif event.kind == "cancelled":
                self.selected_model = None
                variant = self._selected_variant()
                if variant is not None:
                    self._set_variant_status(variant.filename, "not downloaded")
                self.progress_var.set(0.0)
                self.status_var.set(event.message)
            elif event.kind == "error":
                self.selected_model = None
                variant = self._selected_variant()
                if variant is not None and variant.local_status == "downloading":
                    self._set_variant_status(variant.filename, "not downloaded")
                self.progress_var.set(0.0)
                self.status_var.set(event.message)
                messagebox.showerror("Hugging Face import", event.message, parent=self)
            self._update_actions()
        if self.winfo_exists():
            self._schedule_pump()

    def _render_variants(self, preferred_filename: str | None = None) -> None:
        selected = self.variant_tree.selection()
        selected_name = selected[0] if selected else preferred_filename
        for item in self.variant_tree.get_children():
            self.variant_tree.delete(item)
        for variant in self.variants:
            self.variant_tree.insert(
                "",
                tk.END,
                iid=variant.filename,
                values=(
                    variant.filename,
                    format_model_size_gb(variant.size_gb or 0),
                    variant.quantization or "-",
                    variant.local_status,
                    variant.note or "-",
                ),
            )
        if selected_name and self.variant_tree.exists(selected_name):
            self.variant_tree.selection_set(selected_name)
            self.variant_tree.focus(selected_name)
        self._update_actions()

    def _selected_variant(self) -> GGUFVariant | None:
        selection = self.variant_tree.selection()
        if not selection:
            return None
        filename = selection[0]
        for variant in self.variants:
            if variant.filename == filename:
                return variant
        return None

    def _set_variant_status(self, filename: str, status: str) -> None:
        updated: list[GGUFVariant] = []
        for variant in self.variants:
            if variant.filename == filename:
                updated.append(
                    GGUFVariant(
                        filename=variant.filename,
                        size_bytes=variant.size_bytes,
                        quantization=variant.quantization,
                        local_path=variant.local_path,
                        local_status=status,  # type: ignore[arg-type]
                        note=variant.note,
                    )
                )
            else:
                updated.append(variant)
        self.variants = updated
        self._render_variants(preferred_filename=filename)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.load_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.configure_token_button.configure(state=state)
        self.remove_token_button.configure(
            state=(tk.DISABLED if busy else (tk.NORMAL if self.token_store.is_configured() else tk.DISABLED))
        )
        self._update_actions()

    def _update_actions(self) -> None:
        if self._busy:
            self.download_button.configure(state=tk.DISABLED)
            self.use_button.configure(state=tk.DISABLED)
            return
        variant = self._selected_variant()
        self.download_button.configure(state=tk.NORMAL if variant is not None else tk.DISABLED)
        can_use = self.selected_model is not None or (
            variant is not None and variant.local_status == "downloaded"
        )
        self.use_button.configure(state=tk.NORMAL if can_use else tk.DISABLED)
