"""
llama-orchestrator: Docker-like CLI orchestration for llama.cpp server instances

Version 2.1.0 - Clarification and safety update with:
- V2 SQLite state schema with runtime/events tables
- Explicit persisted parameter mutability metadata in config.json
- Warning-level validation for wide network binding on 0.0.0.0
- Current-state documentation that separates shipped features from roadmap ideas
"""

__version__ = "2.1.0"
__author__ = "MichaelPrinc"
