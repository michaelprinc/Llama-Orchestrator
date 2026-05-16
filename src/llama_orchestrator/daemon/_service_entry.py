"""Windows service entry point for NSSM-managed daemon execution."""

from llama_orchestrator.daemon.service import start_daemon


if __name__ == "__main__":
    start_daemon(foreground=True)