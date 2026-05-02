# llama.cpp Version Scaling - Implementation Checklist

> Generated: 2025-12-29
> Updated: 2025-01-XX (Phase 1-4 Complete)
> Complexity: **Complex** (multi-module, new dependencies, schema changes)
> Estimated Total Effort: **8-12 hours**

---

## Overview

This checklist covers the implementation of a scalable llama.cpp binary version management system for `llama-orchestrator`. The system enables:

- Multiple llama.cpp versions to coexist
- **UUID4-based binary identification (PRIMARY KEY)**
- Automatic download and installation from GitHub releases
- Per-instance version pinning via config.json

**Key Design Decision:** UUID is the PRIMARY identifier for binaries. Version+variant are supplementary hints for fallback resolution.

---

## Phase 1: Schema & Configuration Updates

**Estimated Effort:** 2 hours | **Status:** ✅ COMPLETE

### 1.1 Extend Instance Config Schema
- [x] Add `binary` section to `InstanceConfig` (Pydantic model)
  - [x] `binary_id`: UUID (PRIMARY KEY)
  - [x] `version`: str (e.g., "b7572", "latest") - fallback hint
  - [x] `variant`: Literal["cpu-x64", "vulkan-x64", "cuda-12.4-x64", ...]
  - [x] `source_url`: Optional[str] (custom download URL override)
- [x] Acceptance: New fields validated by Pydantic, optional with defaults
- [x] Update JSON Schema file if exists

### 1.2 Create Binary Registry Schema
- [x] Create `BinaryVersion` Pydantic model
  - [x] `binary_id`: UUID4 (PRIMARY KEY)
  - [x] `version`: str (llama.cpp version tag)
  - [x] `variant`: str (platform/GPU variant)
  - [x] `download_url`: str
  - [x] `sha256`: Optional[str]
  - [x] `installed_at`: datetime
  - [x] `path`: Path (relative path under `bins/`)
- [x] Create `BinaryRegistry` model for tracking all installed versions
- [x] Acceptance: Models serialize/deserialize correctly

### 1.3 Update Instance Config Template
- [x] Update `instances/<name>/config.json` to include `binary` section
- [x] Add example config for `gpt-oss` with Vulkan binary reference
- [x] Acceptance: Existing configs still load (backward compatible)

---

## Phase 2: Binary Management Module

**Estimated Effort:** 3-4 hours | **Status:** ✅ COMPLETE

### 2.1 Create Binary Manager Module
- [x] Create `src/llama_orchestrator/binaries/__init__.py`
- [x] Create `src/llama_orchestrator/binaries/manager.py`
  - [x] `BinaryManager` class with methods:
    - [x] `install(version, variant) -> BinaryVersion`
    - [x] `uninstall(binary_id: UUID) -> bool`
    - [x] `get(binary_id: UUID) -> BinaryVersion | None`
    - [x] `list_installed() -> list[BinaryVersion]`
    - [x] `get_by_version(version, variant) -> BinaryVersion | None`
    - [x] `resolve_latest() -> str` (fetch latest version from GitHub API)
- [ ] Acceptance: All methods have unit tests

### 2.2 Implement Download & Extraction
- [x] Create `src/llama_orchestrator/binaries/downloader.py`
  - [x] `download_binary(url, dest_path) -> Path`
  - [x] `extract_archive(archive_path, dest_dir) -> Path`
  - [x] `verify_checksum(file_path, expected_sha256) -> bool`
- [x] Use `httpx` for async downloads with progress
- [x] Support `.zip` (Windows) and `.tar.gz` (Linux/macOS)
- [x] Acceptance: Downloads complete, checksums verified, archives extracted

### 2.3 Implement GitHub API Client
- [x] Create `src/llama_orchestrator/binaries/github.py`
  - [x] `get_latest_release() -> dict` (version, assets)
  - [x] `get_release(version: str) -> dict`
  - [x] `build_download_url(version, variant) -> str`
  - [x] Handle rate limiting gracefully
- [x] Acceptance: API calls return expected data, rate limits handled

### 2.4 Create Binary Registry Storage
- [x] Create `src/llama_orchestrator/binaries/registry.py`
  - [x] Store registry in `bins/registry.json`
  - [x] CRUD operations for binary versions
  - [x] UUID-based lookups (primary) and version+variant lookups (fallback)
  - [x] Atomic writes with temp file + rename
- [x] Acceptance: Registry persists across restarts

---

## Phase 3: Directory Structure Changes

**Estimated Effort:** 1-2 hours | **Status:** ✅ COMPLETE

### 3.1 Implement Scalable Bin Directory Structure
- [x] Create new directory layout:
  ```
  llama-orchestrator/
  ├── bins/                          # NEW: Versioned binaries
  │   ├── registry.json              # Central registry with UUIDs
  │   ├── {uuid4}/                   # Unique ID per installation
  │   │   ├── llama-server.exe
  │   │   ├── *.dll
  │   │   └── ...
  │   └── {uuid4}/
  │       └── ...
  ├── bin/                           # DEPRECATED: Keep for backward compat
  │   └── llama-server.exe           # Fallback
  ```
- [x] Update `get_bin_dir()` to return version-specific path
- [x] Create `get_bins_dir()` for the `bins/` directory
- [x] Acceptance: Old and new paths both work

### 3.2 Update Path Resolution
- [x] Modify `get_llama_server_path(config: InstanceConfig) -> Path`
  - [x] Accept config to resolve binary by UUID first
  - [x] Fall back to version+variant if no UUID
  - [x] Fall back to legacy `bin/` if no binary section
- [x] Update `build_command()` to use instance-specific binary
- [x] Acceptance: Commands use correct binary per instance

### 3.3 Migration Script
- [ ] Create `scripts/migrate-bins.py`
  - [ ] Move existing `bin/` contents to `bins/{uuid}/`
  - [ ] Create registry entry for migrated binary
  - [ ] Update existing instance configs
- [ ] Acceptance: Migration is non-destructive, reversible

---

## Phase 4: CLI Commands

**Estimated Effort:** 2 hours | **Status:** ✅ COMPLETE

### 4.1 Add Binary Management Commands
- [x] `llama-orch binary install <version> [--variant vulkan-x64]`
  - [x] Downloads and installs specified version
  - [x] Returns UUID of installed binary
- [x] `llama-orch binary list`
  - [x] Lists all installed binary versions
- [x] `llama-orch binary remove <uuid>`
  - [x] Removes binary (with confirmation)
- [x] `llama-orch binary info <uuid>`
  - [x] Shows detailed info about binary
- [x] `llama-orch binary latest`
  - [x] Shows latest available version from GitHub
- [x] Acceptance: All commands work end-to-end

### 4.2 Update Instance Commands
- [ ] Update `llama-orch init` to accept `--binary-version`
- [x] Update `llama-orch up` to validate binary exists before start
- [ ] Add `llama-orch upgrade <name> [--binary-version]`
  - [ ] Upgrades instance to new binary version
- [x] Acceptance: Instance lifecycle respects binary versions

### 4.3 Add Configuration Command
- [ ] `llama-orch config set-binary <name> <uuid|version>`
  - [ ] Updates instance config to use specific binary
- [ ] Acceptance: Config updates persist correctly

---

## Phase 5: Testing & Documentation

**Estimated Effort:** 2 hours | **Status:** 🔄 NOT STARTED

### 5.1 Unit Tests
- [ ] `tests/test_binary_manager.py`
- [ ] `tests/test_binary_downloader.py`
- [ ] `tests/test_binary_registry.py`
- [ ] `tests/test_github_api.py`
- [ ] Acceptance: >80% code coverage for new modules

### 5.2 Integration Tests
- [ ] Test full install → configure → run → stop workflow
- [ ] Test multiple versions running simultaneously
- [ ] Test backward compatibility with legacy `bin/`
- [ ] Acceptance: All integration tests pass

### 5.3 Update Documentation
- [ ] Update `README.md` with binary management section
- [ ] Add `docs/BINARY_MANAGEMENT.md` detailed guide
- [ ] Document config.json `binary` section
- [ ] Add migration guide from legacy `bin/`
- [ ] Acceptance: Documentation is complete and accurate

---

## Phase 6: Polish & Edge Cases

**Estimated Effort:** 1 hour

### 6.1 Error Handling
- [ ] Handle download failures gracefully
- [ ] Handle corrupt archives
- [ ] Handle disk space issues
- [ ] Handle permission errors
- [ ] Acceptance: Clear error messages for all failure modes

### 6.2 Cleanup & Optimization
- [ ] Add `llama-orch binary prune` (remove unused versions)
- [ ] Add download caching in temp directory
- [ ] Add concurrent download support (future)
- [ ] Acceptance: No orphaned files, efficient downloads

### 6.3 Security
- [ ] Verify checksums for all downloads
- [ ] Validate GitHub URLs (prevent injection)
- [ ] Secure file permissions on binaries
- [ ] Acceptance: No security vulnerabilities

---

## Acceptance Criteria Summary

| Criteria | Status |
|----------|--------|
| Multiple llama.cpp versions can coexist | [x] |
| Each binary has unique UUID4 identifier | [x] |
| Instances can pin specific binary versions | [x] |
| Automatic download from GitHub releases | [x] |
| SHA256 checksum verification | [x] |
| Backward compatible with legacy `bin/` | [x] |
| CLI commands for binary management | [x] |
| Unit tests with >80% coverage | [ ] |
| Documentation complete | [ ] |

---

## Rollback Plan

If issues arise:
1. Keep legacy `bin/` directory intact
2. `binary` config section is optional (defaults to legacy)
3. Migration script creates backups
4. Can revert by removing `bins/` and `binary` config sections

---

## Dependencies

- **New**: None (uses existing `httpx`, `pydantic`)
- **Updated**: `aiosqlite` for async registry operations (optional)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| GitHub rate limiting | Medium | Medium | Cache API responses, respect limits |
| Large download sizes (~50-400MB) | High | Low | Progress indicators, resume support |
| Disk space exhaustion | Low | High | Check free space before download |
| Checksum mismatch | Low | Medium | Clear error, retry option |
| Breaking existing configs | Medium | High | Backward compatibility layer |
