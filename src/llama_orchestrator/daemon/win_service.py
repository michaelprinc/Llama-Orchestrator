"""Windows service helpers for llama-orchestrator."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from llama_orchestrator.config import get_project_root


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows service support is only available on Windows")


def _get_nssm_path() -> str:
    nssm_path = shutil.which("nssm")
    if not nssm_path:
        raise FileNotFoundError("NSSM not found in PATH")
    return nssm_path


def _get_service_entry_script() -> Path:
    return Path(__file__).with_name("_service_entry.py")


def install_windows_service(service_name: str = "llama-orchestrator") -> None:
    """Install the daemon as an NSSM-managed Windows service."""
    _require_windows()
    nssm = _get_nssm_path()
    project_root = get_project_root()
    python_executable = Path(sys.executable)
    service_script = _get_service_entry_script()

    daemon_log_dir = project_root / "logs" / "daemon"
    daemon_log_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [nssm, "install", service_name, str(python_executable), str(service_script)],
        check=True,
    )
    subprocess.run([nssm, "set", service_name, "AppDirectory", str(project_root)], check=True)
    subprocess.run([nssm, "set", service_name, "DisplayName", "llama-orchestrator Daemon"], check=True)
    subprocess.run(
        [nssm, "set", service_name, "Description", "Docker-like orchestration for llama.cpp server instances"],
        check=True,
    )
    subprocess.run([nssm, "set", service_name, "Start", "SERVICE_AUTO_START"], check=True)
    subprocess.run([nssm, "set", service_name, "AppStdout", str(daemon_log_dir / "stdout.log")], check=True)
    subprocess.run([nssm, "set", service_name, "AppStderr", str(daemon_log_dir / "stderr.log")], check=True)
    subprocess.run([nssm, "set", service_name, "AppRotateFiles", "1"], check=True)
    subprocess.run([nssm, "set", service_name, "AppRotateBytes", str(10 * 1024 * 1024)], check=True)


def uninstall_windows_service(service_name: str = "llama-orchestrator") -> None:
    """Uninstall the NSSM-managed Windows service."""
    _require_windows()
    nssm = _get_nssm_path()
    subprocess.run([nssm, "stop", service_name], check=False)
    subprocess.run([nssm, "remove", service_name, "confirm"], check=True)