# llama.cpp Version Scaling - Implementation Plan

> Generated: 2025-12-29
> Complexity: **Complex**
> Estimated Duration: 8-12 hours

---

## 1. Current State Analysis

### 1.1 Existing Architecture

```
llama-orchestrator/
├── bin/                          # ❌ PROBLEM: Single, unversioned binary folder
│   ├── llama-server.exe          # Only one version can exist
│   ├── *.dll                     # Shared libraries
│   └── LICENSE-*
├── instances/
│   └── <name>/config.json        # ❌ No binary version reference
├── src/llama_orchestrator/
│   ├── config/
│   │   ├── loader.py             # get_llama_server_path() → hardcoded
│   │   └── schema.py             # InstanceConfig without binary info
│   └── engine/
│       └── command.py            # build_command() uses single binary
└── state/
```

### 1.2 Key Limitations

| Issue | Impact | Solution |
|-------|--------|----------|
| Single `bin/` directory | Can't run different llama.cpp versions | UUID-based `bins/` structure |
| No version tracking | Can't upgrade/rollback | Binary registry with metadata |
| No download automation | Manual binary management | GitHub API + automated downloader |
| Hardcoded paths | All instances use same binary | Per-instance binary configuration |

### 1.3 llama.cpp Release Structure Analysis

Based on GitHub releases (https://github.com/ggml-org/llama.cpp/releases):

**Version Pattern:** `b{BUILD_NUMBER}` (e.g., `b7572`, `b7571`)

**Download URL Pattern:**
```
https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/llama-{VERSION}-bin-{VARIANT}.{EXT}
```

**Windows Variants:**
| Variant ID | Description | Extension |
|------------|-------------|-----------|
| `win-cpu-x64` | CPU-only, x64 | `.zip` |
| `win-cpu-arm64` | CPU-only, ARM64 | `.zip` |
| `win-vulkan-x64` | Vulkan GPU acceleration | `.zip` |
| `win-cuda-12.4-x64` | CUDA 12.4 + cudart DLLs | `.zip` |
| `win-cuda-13.1-x64` | CUDA 13.1 + cudart DLLs | `.zip` |
| `win-hip-radeon-x64` | AMD HIP/ROCm | `.zip` |
| `win-sycl-x64` | Intel SYCL | `.zip` |

---

## 2. Goal and Scope

### 2.1 Primary Objectives

1. **Multi-version support**: Run multiple llama.cpp versions simultaneously
2. **Instance isolation**: Each instance can use a different binary version
3. **Automated installation**: Download binaries directly from GitHub
4. **Version tracking**: Registry of installed versions with metadata
5. **Backward compatibility**: Existing configs continue to work

### 2.2 Scope Definition

**In Scope:**
- Binary version management (install, list, remove)
- Per-instance binary configuration
- GitHub release integration
- SHA256 checksum verification
- CLI commands for binary management
- Migration from legacy `bin/` structure

**Out of Scope:**
- Model management (separate concern)
- Linux/macOS support (future)
- Building from source (use pre-built releases)
- Automatic updates (manual trigger required)

---

## 3. Proposed Architecture

### 3.1 New Directory Structure

```
llama-orchestrator/
├── bins/                              # NEW: Versioned binary storage
│   ├── registry.json                  # Binary version registry
│   ├── a1b2c3d4-e5f6-7890-abcd-ef1234567890/
│   │   ├── version.json               # Metadata for this installation
│   │   ├── llama-server.exe
│   │   ├── llama-cli.exe
│   │   ├── *.dll
│   │   └── LICENSE-*
│   └── f9e8d7c6-b5a4-3210-fedc-ba9876543210/
│       └── ...                         # Another version
├── bin/                               # DEPRECATED: Legacy fallback
│   └── llama-server.exe               # Still works for backward compat
├── instances/
│   └── gpt-oss/
│       └── config.json                # NOW includes binary section
└── src/llama_orchestrator/
    ├── binaries/                      # NEW: Binary management module
    │   ├── __init__.py
    │   ├── manager.py                 # BinaryManager class
    │   ├── downloader.py              # Download & extraction
    │   ├── github.py                  # GitHub API client
    │   ├── registry.py                # Binary registry storage
    │   └── schema.py                  # Pydantic models
    └── ...
```

### 3.2 Data Flow Diagram

```
                                ┌─────────────────────┐
                                │   GitHub Releases   │
                                │  ggml-org/llama.cpp │
                                └──────────┬──────────┘
                                           │ API/Download
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                        BINARY MANAGER                            │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐             │
│  │  GitHub    │───▶│ Downloader │───▶│  Registry  │             │
│  │  API       │    │            │    │            │             │
│  └────────────┘    └────────────┘    └─────┬──────┘             │
│                                            │                     │
│        ┌───────────────────────────────────┘                    │
│        ▼                                                         │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    bins/                                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐              │    │
│  │  │ {uuid1}  │  │ {uuid2}  │  │ {uuid3}  │   ...        │    │
│  │  │ b7572    │  │ b7560    │  │ b7500    │              │    │
│  │  │ vulkan   │  │ cuda12   │  │ cpu      │              │    │
│  │  └──────────┘  └──────────┘  └──────────┘              │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                                           ▲
                                           │ resolve
                                           │
┌──────────────────────────────────────────┴───────────────────────┐
│                         INSTANCE CONFIG                           │
│  {                                                                │
│    "name": "gpt-oss",                                             │
│    "binary": {                                                    │
│      "version": "b7572",                        ◄── NEW           │
│      "variant": "win-vulkan-x64",               ◄── NEW           │
│      "source_url": "...",                       ◄── OPTIONAL      │
│      "sha256": "..."                            ◄── OPTIONAL      │
│    },                                                             │
│    "model": { ... },                                              │
│    "server": { ... }                                              │
│  }                                                                │
└──────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                       ENGINE / COMMAND BUILDER                    │
│                                                                   │
│  build_command(config) → [                                        │
│    "bins/{uuid}/llama-server.exe",  ◄── Version-specific path    │
│    "--model", "models/...",                                       │
│    "--port", "8001",                                              │
│    ...                                                            │
│  ]                                                                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Schema Definitions

### 4.1 Binary Configuration (in `config.json`)

```json
{
  "$schema": "../llama-instance-schema.json",
  "name": "gpt-oss",
  
  "binary": {
    "version": "b7572",
    "variant": "win-vulkan-x64",
    "source_url": "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
    "sha256": "optional-checksum-for-verification"
  },
  
  "model": {
    "path": "models/gpt-oss-20b-Q4_K_S.gguf",
    "context_size": 4096,
    "batch_size": 512,
    "threads": 16
  },
  
  "server": {
    "host": "127.0.0.1",
    "port": 8001,
    "timeout": 600,
    "parallel": 4
  },
  
  "gpu": {
    "backend": "vulkan",
    "device_id": 1,
    "layers": 30
  }
}
```

### 4.2 Binary Registry (`bins/registry.json`)

```json
{
  "$schema": "binary-registry-schema.json",
  "version": "1.0.0",
  "binaries": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "version": "b7572",
      "variant": "win-vulkan-x64",
      "download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
      "sha256": "abc123...",
      "installed_at": "2025-12-29T10:30:00Z",
      "path": "bins/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "size_bytes": 52428800,
      "executables": ["llama-server.exe", "llama-cli.exe"]
    }
  ],
  "default_binary_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

### 4.3 Version Metadata (`bins/{uuid}/version.json`)

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "version": "b7572",
  "variant": "win-vulkan-x64",
  "source_url": "https://github.com/ggml-org/llama.cpp/releases/download/b7572/llama-b7572-bin-win-vulkan-x64.zip",
  "sha256": "verified-checksum",
  "installed_at": "2025-12-29T10:30:00Z",
  "github_release_info": {
    "tag_name": "b7572",
    "published_at": "2025-12-29T14:41:50Z",
    "commit_sha": "c1366056f6cb6607b035e438fb1d10bc8b207364"
  }
}
```

---

## 5. Pydantic Models

### 5.1 Binary Schema (`src/llama_orchestrator/binaries/schema.py`)

```python
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl


class BinaryConfig(BaseModel):
    """Binary configuration in instance config.json."""
    
    version: str = Field(
        default="latest",
        description="llama.cpp version tag (e.g., 'b7572', 'latest')"
    )
    variant: Literal[
        "win-cpu-x64",
        "win-cpu-arm64", 
        "win-vulkan-x64",
        "win-cuda-12.4-x64",
        "win-cuda-13.1-x64",
        "win-hip-radeon-x64",
        "win-sycl-x64",
    ] = Field(
        default="win-vulkan-x64",
        description="Platform/GPU variant"
    )
    source_url: Optional[HttpUrl] = Field(
        default=None,
        description="Custom download URL (overrides auto-generated)"
    )
    sha256: Optional[str] = Field(
        default=None,
        description="Expected SHA256 checksum for verification"
    )


class BinaryVersion(BaseModel):
    """Installed binary version metadata."""
    
    id: UUID = Field(default_factory=uuid4)
    version: str
    variant: str
    download_url: str
    sha256: Optional[str] = None
    installed_at: datetime = Field(default_factory=datetime.utcnow)
    path: Path
    size_bytes: Optional[int] = None
    executables: list[str] = Field(default_factory=list)


class BinaryRegistry(BaseModel):
    """Registry of all installed binary versions."""
    
    version: str = "1.0.0"
    binaries: list[BinaryVersion] = Field(default_factory=list)
    default_binary_id: Optional[UUID] = None
    
    def get_by_id(self, binary_id: UUID) -> Optional[BinaryVersion]:
        return next((b for b in self.binaries if b.id == binary_id), None)
    
    def get_by_version(self, version: str, variant: str) -> Optional[BinaryVersion]:
        return next(
            (b for b in self.binaries if b.version == version and b.variant == variant),
            None
        )
```

---

## 6. Implementation Phases

### Phase 1: Schema & Config Updates (2 hours)

1. Create `src/llama_orchestrator/binaries/schema.py` with Pydantic models
2. Update `InstanceConfig` in `config/schema.py` to include `binary: BinaryConfig`
3. Update `config.json` for `gpt-oss` instance with binary section
4. Ensure backward compatibility (binary section is optional)

### Phase 2: Binary Manager Module (3-4 hours)

1. Create `binaries/` module structure
2. Implement `BinaryManager` class:
   - `install()`: Download, extract, register
   - `uninstall()`: Remove binary and registry entry
   - `list_installed()`: Return all installed versions
   - `resolve()`: Get binary path for config
3. Implement `GitHub` API client for release info
4. Implement `Downloader` with progress and checksum

### Phase 3: Path Resolution & Engine Updates (1-2 hours)

1. Update `get_llama_server_path()` to accept config
2. Create `bins/` directory structure
3. Update `build_command()` to use resolved binary path
4. Create migration script for legacy `bin/`

### Phase 4: CLI Commands (2 hours)

1. Add `binary` command group to CLI
2. Implement `install`, `list`, `remove`, `info` commands
3. Update `init` and `up` commands for binary support

### Phase 5: Testing & Documentation (2 hours)

1. Write unit tests for new modules
2. Write integration tests for full workflow
3. Update README and create BINARY_MANAGEMENT.md

---

## 7. CLI Command Design

```bash
# Binary Management
llama-orch binary install b7572 --variant vulkan-x64
llama-orch binary install latest --variant cuda-12.4-x64
llama-orch binary list
llama-orch binary list --verbose
llama-orch binary info b7572
llama-orch binary info a1b2c3d4-...
llama-orch binary remove b7572 --variant vulkan-x64
llama-orch binary prune  # Remove unused versions

# Instance with specific binary
llama-orch init my-instance --model path/to/model.gguf --binary-version b7572
llama-orch init my-instance --model path/to/model.gguf --binary-version latest --variant cuda-12.4-x64

# Upgrade instance binary
llama-orch upgrade gpt-oss --binary-version b7572
llama-orch upgrade gpt-oss --binary-version latest

# Configuration
llama-orch config set-binary gpt-oss b7572
llama-orch config show gpt-oss
```

---

## 8. Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| GitHub rate limiting | Medium | Medium | Cache responses, handle 403 gracefully |
| Download corruption | Low | High | SHA256 verification, retry logic |
| Breaking existing configs | Medium | High | Optional binary field, graceful fallback |
| Disk space exhaustion | Low | High | Check space before download, warn user |
| Concurrent access | Low | Medium | File locking on registry writes |

---

## 9. Testing Strategy

### Unit Tests
- `test_binary_schema.py`: Model validation
- `test_binary_manager.py`: Core logic (mocked downloads)
- `test_github_api.py`: API client (mocked responses)
- `test_downloader.py`: Download/extraction (mocked network)

### Integration Tests
- Install real binary from GitHub (CI-only, optional)
- Full workflow: install → config → start → stop
- Multiple versions simultaneously
- Migration from legacy `bin/`

### Manual Testing
- Fresh install workflow
- Upgrade existing instance
- Error handling (network failures, disk full)

---

## 10. Deliverables

1. **Code**:
   - `src/llama_orchestrator/binaries/` module (5-6 files)
   - Updated `config/schema.py`
   - Updated CLI commands in `cli.py`
   - Migration script `scripts/migrate-bins.py`

2. **Configuration**:
   - Updated `instances/gpt-oss/config.json`
   - New `bins/registry.json` (auto-created)
   - Updated JSON schema (optional)

3. **Documentation**:
   - `docs/BINARY_MANAGEMENT.md`
   - Updated `README.md`
   - Migration guide

4. **Tests**:
   - Unit tests: 10-15 new test files
   - Integration tests: 3-5 scenarios

---

## 11. Recommended Implementation Prompt

Copy this prompt to start implementation:

```
Implement the llama.cpp binary version scaling system for llama-orchestrator based on:

1. Create the binaries module with:
   - schema.py: BinaryConfig, BinaryVersion, BinaryRegistry Pydantic models
   - github.py: GitHub API client for releases
   - downloader.py: Download and extraction utilities
   - registry.py: Registry storage operations
   - manager.py: BinaryManager orchestration class

2. Update config/schema.py:
   - Add BinaryConfig to InstanceConfig (optional field)
   - Ensure backward compatibility

3. Update config/loader.py:
   - Modify get_llama_server_path() to resolve binary from config
   - Create bins/ directory structure

4. Update engine/command.py:
   - Use resolved binary path in build_command()

5. Add CLI commands in cli.py:
   - binary install/list/remove/info subcommands
   - Update init command with --binary-version option

6. Create tests for all new modules

Follow the schemas and patterns defined in LLAMA_VERSION_SCALING_IMPLEMENTATION_PLAN.md
```

---

## 12. Future Enhancements (Out of Scope)

- Linux/macOS support (different variants, tar.gz extraction)
- Automatic update checking and notifications
- Building from source (cmake integration)
- Binary performance profiling per version
- Model-to-binary compatibility matrix
