"""Registry browser and local package importer for llama-server bundles."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from llama_orchestrator.binaries.manager import (
    BinaryManagerError,
    resolve_local_package_directory,
)
from llama_orchestrator.binaries.schema import BinaryVersion


@dataclass(frozen=True)
class LocalPackageImport:
    """User-supplied provenance for a complete local server package."""

    package_dir: Path
    version: str
    variant: str
    source_url: str | None = None


def validate_local_package_import(
    request: LocalPackageImport,
    managed_bins_dir: Path,
) -> LocalPackageImport:
    """Validate and normalize a local package before starting a background copy."""
    package_dir = request.package_dir.expanduser().resolve()
    bins_dir = managed_bins_dir.expanduser().resolve()
    try:
        package_dir.relative_to(bins_dir)
    except ValueError:
        pass
    else:
        raise ValueError("Select the original package, not a folder already under managed bins/.")
    try:
        package_dir = resolve_local_package_directory(package_dir)
    except BinaryManagerError as exc:
        raise ValueError(str(exc)) from exc
    if not request.version.strip() or not request.variant.strip():
        raise ValueError("Build/version ID and variant are required.")
    return LocalPackageImport(
        package_dir=package_dir,
        version=request.version.strip(),
        variant=request.variant.strip(),
        source_url=request.source_url.strip() if request.source_url else None,
    )


def format_size(size_bytes: int | None) -> str:
    """Format an optional package size for the compact browser table."""
    if size_bytes is None:
        return "-"
    return f"{size_bytes / (1024 * 1024):.1f} MiB"


def build_version_rows(
    binaries: Sequence[BinaryVersion],
    package_path: Callable[[BinaryVersion], Path],
) -> list[tuple[str, ...]]:
    """Build display rows and expose missing package executables explicitly."""
    rows: list[tuple[str, ...]] = []
    for binary in sorted(binaries, key=lambda item: item.installed_at, reverse=True):
        server_path = package_path(binary) / "llama-server.exe"
        status = "Ready" if server_path.is_file() else "Missing llama-server.exe"
        rows.append(
            (
                str(binary.id),
                binary.version,
                binary.variant,
                format_size(binary.size_bytes),
                binary.sha256 or "-",
                status,
                str(server_path.parent),
            )
        )
    return rows


class LocalPackageImportDialog(tk.Toplevel):
    """Collect explicit identity before a package is copied into ``bins/``."""

    def __init__(
        self,
        master: tk.Misc,
        on_import: Callable[[LocalPackageImport], None],
        managed_bins_dir: Path,
    ) -> None:
        super().__init__(master)
        self.title("Import local llama-server package")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self._on_import = on_import
        self._managed_bins_dir = managed_bins_dir
        self.package_var = tk.StringVar()
        self.version_var = tk.StringVar()
        self.variant_var = tk.StringVar(value="win-hip-radeon-x64")
        self.source_url_var = tk.StringVar()
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Package folder").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.package_var, width=60).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        ttk.Button(frame, text="Browse", command=self._browse).grid(
            row=0, column=2, padx=(6, 0), pady=4
        )
        ttk.Label(frame, text="Build/version ID").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.version_var, width=60).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(frame, text="Variant").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.variant_var, width=60).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(frame, text="Provenance URL (optional)").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Entry(frame, textvariable=self.source_url_var, width=60).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(
            frame,
            text=(
                "Select a complete package, or a build folder with bin/llama-server.exe. "
                "For ROCm builds, the matching artifacts/package bundle is copied to bins/<UUID>."
            ),
            wraplength=580,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Import", command=self._import).pack(side=tk.RIGHT, padx=(0, 8))

    def _browse(self) -> None:
        directory = filedialog.askdirectory(title="Select complete llama-server package folder")
        if directory:
            self.package_var.set(directory)

    def _import(self) -> None:
        package_dir = Path(self.package_var.get().strip()).expanduser()
        version = self.version_var.get().strip()
        variant = self.variant_var.get().strip()
        source_url = self.source_url_var.get().strip() or None
        try:
            request = validate_local_package_import(
                LocalPackageImport(
                    package_dir=package_dir,
                    version=version,
                    variant=variant,
                    source_url=source_url,
                ),
                self._managed_bins_dir,
            )
        except ValueError as exc:
            messagebox.showerror("Invalid package", str(exc), parent=self)
            return
        self._on_import(request)
        self.destroy()


class SwitchServerDialog(tk.Toplevel):
    """Scrollable, selectable picker for a registered llama-server package."""

    COLUMNS = ("uuid", "version", "variant", "status", "path")

    def __init__(
        self,
        master: tk.Misc,
        *,
        binaries: Sequence[BinaryVersion],
        package_path: Callable[[BinaryVersion], Path],
        selected_binary_id: str | None,
        on_confirm: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.title("Switch llama-server version")
        self.geometry("1080x450")
        self.minsize(820, 300)
        self.transient(master)
        self.grab_set()
        self._on_confirm = on_confirm
        self._rows = build_version_rows(binaries, package_path)
        self._build(selected_binary_id)

    def _build(self, selected_binary_id: str | None) -> None:
        frame = ttk.Frame(self, padding=14)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(
            frame,
            text="Select a registered package. Only a Ready package can be applied.",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=self.COLUMNS, show="headings", height=15)
        headings = {
            "uuid": "UUID",
            "version": "Version",
            "variant": "Variant",
            "status": "Status",
            "path": "Managed package folder",
        }
        widths = {"uuid": 280, "version": 220, "variant": 220, "status": 150, "path": 360}
        for column in self.COLUMNS:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=80, stretch=column == "path")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self._confirm())

        selected_item = None
        for row in self._rows:
            item = self.tree.insert("", tk.END, values=(row[0], row[1], row[2], row[5], row[6]))
            if row[0] == selected_binary_id:
                selected_item = item
        if selected_item is not None:
            self.tree.selection_set(selected_item)
            self.tree.focus(selected_item)
            self.tree.see(selected_item)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Confirm selected server", command=self._confirm).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _confirm(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select a server", "Select a llama-server package first.", parent=self)
            return
        values = self.tree.item(selected[0], "values")
        if not values or values[3] != "Ready":
            messagebox.showerror(
                "Package unavailable",
                "The selected package is incomplete and cannot be applied.",
                parent=self,
            )
            return
        self.destroy()
        self._on_confirm(str(values[0]))


class VersionBrowserDialog(tk.Toplevel):
    """Show registered packages, their UUIDs, and local package health."""

    COLUMNS = ("uuid", "version", "variant", "size", "sha256", "status", "path")

    def __init__(
        self,
        master: tk.Misc,
        *,
        list_binaries: Callable[[], Sequence[BinaryVersion]],
        package_path: Callable[[BinaryVersion], Path],
        on_import: Callable[[LocalPackageImport], None],
        managed_bins_dir: Path,
    ) -> None:
        super().__init__(master)
        self.title("llama-server version browser")
        self.geometry("1180x430")
        self.minsize(900, 300)
        self.transient(master)
        self._list_binaries = list_binaries
        self._package_path = package_path
        self._on_import = on_import
        self._managed_bins_dir = managed_bins_dir
        self._build()
        self.refresh()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text="Registered llama-server packages. UUID is the immutable instance pin.",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        tree_frame = ttk.Frame(frame)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=self.COLUMNS, show="headings", height=14)
        headings = {
            "uuid": "UUID",
            "version": "Version",
            "variant": "Variant",
            "size": "Package size",
            "sha256": "Server SHA-256",
            "status": "Status",
            "path": "Managed package folder",
        }
        widths = {"uuid": 280, "version": 220, "variant": 220, "size": 90, "sha256": 160, "status": 160, "path": 360}
        for column in self.COLUMNS:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=80, stretch=column == "path")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(buttons, text="Import server...", command=self.open_importer).pack(
            side=tk.LEFT
        )

    def refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            rows = build_version_rows(self._list_binaries(), self._package_path)
        except Exception as exc:
            messagebox.showerror("Version browser", f"Could not load binary registry: {exc}", parent=self)
            return
        for row in rows:
            self.tree.insert("", tk.END, values=row)

    def open_importer(self) -> None:
        """Open the local-package importer from the browser or the main toolbar."""
        LocalPackageImportDialog(
            self,
            on_import=self._on_import,
            managed_bins_dir=self._managed_bins_dir,
        )
