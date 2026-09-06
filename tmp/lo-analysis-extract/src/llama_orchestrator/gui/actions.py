"""Instance action handlers.

Extracted from app.py (Phase 5: Module extraction).
Handles clone, diff, copy CLI command, and rename operations.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox
from typing import Any


def clone_instance(
    source_name: str,
    next_clone_name_fn: Callable[[str], str],
    clone_fn: Callable[[str, str, Path, str], None],
    on_clone_complete: Callable[[str], None],
) -> None:
    """Clone the selected instance.

    Args:
        source_name: Name of the source instance to clone.
        next_clone_name_fn: Function to generate next clone name.
        clone_fn: Function to perform the actual clone.
        on_clone_complete: Callback with new instance name.
    """
    new_name = next_clone_name_fn(source_name)
    if not new_name:
        return

    try:
        clone_fn(source_name, new_name, Path(), "")
        on_clone_complete(new_name)
        messagebox.showinfo("Clone", f"Instance '{source_name}' cloned as '{new_name}'")
    except Exception as exc:
        messagebox.showerror("Clone failed", str(exc))


def next_clone_name(
    source: str,
    existing_names: set[str],
) -> str:
    """Generate a unique clone name based on source.

    Args:
        source: The source instance name.
        existing_names: Set of all existing instance names.

    Returns:
        A unique name for the clone.
    """
    base = source
    counter = 1
    candidate = f"{base}-clone-{counter}"
    while candidate in existing_names:
        counter += 1
        candidate = f"{base}-clone-{counter}"
    return candidate


def diff_instances(
    source: str,
    target: str,
    diff_fn: Callable[[str, str], str],
    parent: tk.Misc,
) -> None:
    """Compare two instances and show diff.

    Args:
        source: First instance name.
        target: Second instance name.
        diff_fn: Function to generate diff string.
        parent: Parent widget.
    """
    try:
        diff_text = diff_fn(source, target)
        _show_diff_window(source, target, diff_text, parent)
    except Exception as exc:
        messagebox.showerror("Diff failed", str(exc), parent=parent)


def _show_diff_window(
    name1: str,
    name2: str,
    diff_text: str,
    parent: tk.Misc,
) -> None:
    """Show diff in a new Toplevel window."""
    dialog = tk.Toplevel(parent)
    dialog.title(f"Diff: {name1} vs {name2}")
    dialog.geometry("600x400")
    dialog.transient(parent)

    text = tk.Text(dialog, wrap="none")
    text.pack(fill="both", expand=True, padx=10, pady=10)
    text.insert("1.0", diff_text)
    text.config(state="disabled")


def copy_cli_command(
    instance_name: str,
    get_cli_command_fn: Callable[[str], str],
    root: tk.Misc,
) -> None:
    """Copy CLI command to clipboard using the existing root window.

    Args:
        instance_name: Name of the instance.
        get_cli_command_fn: Function to get the CLI command string.
        root: The active Tkinter root/widget.
    """
    try:
        command = get_cli_command_fn(instance_name)
        root.clipboard_clear()
        root.clipboard_append(command)
        messagebox.showinfo("Copied", "CLI command copied to clipboard", parent=root)
    except Exception as exc:
        messagebox.showerror("Copy failed", str(exc), parent=root)


def rename_instance(
    instance_name: str,
    current_display_name: str,
    confirm_fn: Callable[[str, str], bool],
    parent: tk.Misc,
) -> bool:
    """Rename an instance's display name using a modal Toplevel dialog.

    Args:
        instance_name: The instance identifier.
        current_display_name: Current display name.
        confirm_fn: Confirmation callback (returns True to proceed).
        parent: Parent widget/window.

    Returns:
        True if rename was confirmed, False otherwise.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(f"Rename: {instance_name}")
    dialog.geometry("300x150")
    dialog.transient(parent)
    dialog.grab_set()

    tk.Label(dialog, text=f"Current name: {current_display_name}").pack(pady=10)

    name_var = tk.StringVar(value=current_display_name)
    entry = tk.Entry(dialog, textvariable=name_var, width=30)
    entry.pack(pady=5)

    result: list[bool] = [False]

    def on_ok() -> None:
        new_name = name_var.get().strip()
        if new_name and new_name != current_display_name and confirm_fn(instance_name, new_name):
            result[0] = True
        dialog.destroy()

    tk.Button(dialog, text="OK", command=on_ok).pack(pady=5)
    tk.Button(dialog, text="Cancel", command=dialog.destroy).pack(pady=2)

    parent.wait_window(dialog)
    return result[0]


def copy_endpoint_snippet(
    instance_name: str,
    endpoint_type: str,
    get_config_fn: Callable[[str], Any],
    root: tk.Misc,
) -> None:
    """Generate and copy endpoint configuration snippet to clipboard.

    Args:
        instance_name: Name of the instance.
        endpoint_type: Type of endpoint snippet ("openai_base", "openai_chat", "llama_completion", "python_snippet", "curl_command").
        get_config_fn: Function to get the InstanceConfig object.
        root: The active Tkinter root/widget.
    """
    try:
        config = get_config_fn(instance_name)
        host = getattr(getattr(config, "server", None), "host", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = getattr(getattr(config, "server", None), "port", 8080)
        model_path = getattr(getattr(config, "model", None), "path", "")
        model_name = Path(model_path).name if model_path else "model"

        snippet = ""
        if endpoint_type == "openai_base":
            snippet = f"http://{host}:{port}/v1"
        elif endpoint_type == "openai_chat":
            snippet = f"http://{host}:{port}/v1/chat/completions"
        elif endpoint_type == "llama_completion":
            snippet = f"http://{host}:{port}/completion"
        elif endpoint_type == "python_snippet":
            snippet = (
                "from openai import OpenAI\n\n"
                f"client = OpenAI(\n"
                f"    base_url=\"http://{host}:{port}/v1\",\n"
                f"    api_key=\"no-key-required\"\n"
                ")\n\n"
                "response = client.chat.completions.create(\n"
                f"    model=\"{model_name}\",\n"
                "    messages=[{\"role\": \"user\", \"content\": \"Hello!\"}]\n"
                ")\n"
                "print(response.choices[0].message.content)\n"
            )
        elif endpoint_type == "curl_command":
            snippet = (
                f"curl http://{host}:{port}/v1/chat/completions \\\n"
                "  -H \"Content-Type: application/json\" \\\n"
                "  -d '{\n"
                f"    \"model\": \"{model_name}\",\n"
                "    \"messages\": [\n"
                "      {\n"
                "        \"role\": \"system\",\n"
                "        \"content\": \"You are a helpful assistant.\"\n"
                "      },\n"
                "      {\n"
                "        \"role\": \"user\",\n"
                "        \"content\": \"Hello!\"\n"
                "      }\n"
                "    ]\n"
                "  }'"
            )
        else:
            raise ValueError(f"Unknown endpoint type: {endpoint_type}")

        root.clipboard_clear()
        root.clipboard_append(snippet)
        messagebox.showinfo("Copied", f"Copied {endpoint_type.replace('_', ' ')} snippet to clipboard.", parent=root)
    except Exception as exc:
        messagebox.showerror("Copy failed", str(exc), parent=root)
