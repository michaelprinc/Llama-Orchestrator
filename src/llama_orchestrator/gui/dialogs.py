"""GUI dialog classes extracted from the legacy gui.py.

This module retains ``ExistingModelFileDialog``, which is used by the
Hugging Face import dialog (``hf_import_dialog``).  The other dialogs that
were staged here during the WS-5 extraction (KV cache profile selection,
grid benchmark configuration, binary installation) live in their dedicated
modules: ``kv_cache_dialogs``, ``grid_benchmark_dialog``, and
``install_dialog``.  The dead duplicate copies previously kept in this file
were removed as part of the T4 strict-type workstream.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk


class ExistingModelFileDialog(tk.Toplevel):
    """Dialog for handling existing model files."""

    # Tk or Toplevel (typeshed has no common nominal base; both satisfy
    # Toplevel(master) [Misc] and transient(master) [Wm]).
    def __init__(self, master: tk.Tk | tk.Toplevel, final_path: Path) -> None:
        super().__init__(master)
        self.title("Model file exists")
        self.transient(master)
        self._final_path = final_path

        body = ttk.Frame(self, padding=10)
        body.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            body, text=f"Model file already exists: {final_path}"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        ttk.Button(body, text="Use existing", command=self._use_existing).grid(
            row=1, column=0, sticky="e", padx=4
        )
        ttk.Button(body, text="Redownload", command=self._redownload).grid(
            row=1, column=1, sticky="w", padx=4
        )
        ttk.Button(body, text="Cancel", command=self._cancel).grid(
            row=2, column=0, columnspan=2, pady=(10, 0)
        )

    @staticmethod
    def ask(master: tk.Tk | tk.Toplevel, final_path: Path) -> str:
        """Class method to show the dialog and return the user's choice."""
        dialog = ExistingModelFileDialog(master, final_path)
        dialog.grab_set()
        return dialog._choice

    def _use_existing(self) -> None:
        self._choice = "use_existing"
        self.destroy()

    def _redownload(self) -> None:
        self._choice = "redownload"
        self.destroy()

    def _cancel(self) -> None:
        self._choice = "cancel"
        self.destroy()
