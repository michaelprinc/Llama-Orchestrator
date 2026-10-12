"""Platform-neutral executable naming for bundled llama.cpp binaries.

Windows packages ship their executables with a Windows file-name suffix;
POSIX builds ship the same files without any extension.  Every runtime
code path must resolve executable file names through this module instead
of hardcoding a platform-specific name, so the orchestrator starts,
validates, and displays binaries identically on both platforms (R17).
"""

from __future__ import annotations

import os
from pathlib import Path

# Single Windows-only literal in the tree: the file-name suffix that only
# Windows executables carry.  All other modules reference this constant.
WINDOWS_EXECUTABLE_SUFFIX = ".exe"

# Base names of the llama.cpp executables we resolve.  The platform suffix
# is applied (or not) by :func:`executable_name`.
SERVER_BASE = "llama-server"
CLI_BASE = "llama-cli"
DIFFUSION_RUNNER_BASE = "llama-diffusion-gemma-visual-server"


def executable_name(base: str) -> str:
    """Return the platform-native file name for an executable base name."""
    if os.name == "nt":  # Windows-only branch: only nt names carry a suffix
        return f"{base}{WINDOWS_EXECUTABLE_SUFFIX}"
    return base


def executable_candidates(base: str) -> tuple[str, ...]:
    """Candidate file names for a binary base, platform-native first.

    Packages created on the other platform (for example a Windows bundle
    imported onto a POSIX host) remain locatable through the cross-platform
    candidate.
    """
    native = executable_name(base)
    if os.name == "nt":
        return (native,)
    return (native, f"{base}{WINDOWS_EXECUTABLE_SUFFIX}")


def locate_executable(directory: Path, base: str) -> Path | None:
    """Return the first existing candidate executable inside ``directory``."""
    for name in executable_candidates(base):
        path = directory / name
        if path.is_file():
            return path
    return None
