"""
llama-orchestrator: Docker-like CLI orchestration for llama.cpp server instances

Version 2.0.0 - Major update with:
- V2 SQLite state schema with runtime/events tables
- Process validation and orphan detection
- Instance locking with stale detection
- Detached mode with file-based logging (no deadlocks)
- Port collision detection and suggestions
- Pluggable health probes (HTTP, TCP, Custom)
- Exponential backoff with jitter
- Event-based daemon loop (graceful shutdown)
- State reconciler for consistency
- Standardized CLI exit codes
- Enhanced describe command with V2 info
"""

__version__ = "2.0.0"
__author__ = "MichaelPrinc"
