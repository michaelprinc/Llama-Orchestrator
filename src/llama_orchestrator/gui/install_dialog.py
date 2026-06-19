"""Install binary dialog.

Extracted from app.py to reduce context fill during independent refactoring.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

VULKAN_VARIANT = "win-vulkan-x64"


class InstallBinaryDialog(tk.Toplevel):
    """Dialog for installing llama.cpp binaries from GitHub releases."""

    def __init__(
        self,
        master: tk.Tk,
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
