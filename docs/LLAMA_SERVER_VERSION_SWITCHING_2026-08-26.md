# Llama Server Version Switching: Implementation Plan

**Date:** 2026-08-26  
**Status:** implemented and focused-test validated

## Problem

The orchestrator can store multiple UUID-addressed llama-server packages, but
testing locally built ROCm packages still requires manual registry/config edits.
An explicit `binary_id` can also fall through to an unrelated default package
when the pin has been removed or is incomplete. That is unsafe for controlled
server-version comparisons.

## Goals

1. Import a complete local llama-server package into `bins/<uuid>` without
   mixing its executable and DLLs with another package.
2. Switch one existing instance to a selected, immutable binary UUID without
   changing its model, GPU, server, or sampling settings.
3. Make the switch visible through the CLI and GUI, and restart only when the
   affected instance is already running.
4. Fail closed when an explicit UUID pin cannot be resolved.

## Non-goals

- Download/build/package a ROCm server.
- Infer provenance from the package folder name.
- Change the global default binary during an instance switch.
- Benchmark or claim performance results.

## Design

| Surface | Change |
|---|---|
| Local import | Add `llama-orch binary register <package-dir> --version <id> --variant <variant>`. It copies the whole package into a new UUID directory, requires `llama-server.exe`, records the executable SHA-256 and package size, and does not modify the source package. |
| Instance switch | Add `llama-orch config set-binary <instance> <binary-uuid> [--restart]`. It writes a complete UUID pin plus version/variant provenance. `--restart` is required if the instance is running. |
| Resolution | An explicit `binary_id` is authoritative: a missing registry entry or `llama-server.exe` is an error, never a fallback to the default or legacy binary. Legacy/default fallback remains only for configs without `binary_id`. |
| GUI | Add a `Switch llama-server version...` action for the selected instance. The dialog lists registered packages, shows version/variant/UUID, confirms a running-instance restart, persists the pin, and refreshes the table. |
| Version browser | Add a persistent `Server versions` window that exposes UUID, version, variant, server SHA-256, package size, destination, and whether the managed package contains `llama-server.exe`. Its importer copies a complete local package into `bins/<UUID>`. |

## Acceptance Criteria

1. A local package containing `llama-server.exe` can be registered without
   changing its source directory.
2. Switching changes only `binary.*` in the selected instance configuration.
3. A bad explicit UUID prevents command construction/startup instead of using a
   different binary.
4. CLI and GUI operations surface package identity before the change.
5. Focused unit tests cover registration, strict resolution, non-running
   switching, and the restart guard.

## Implementation Sequence

1. Add package registration and strict resolution in the binary manager/loader.
2. Add a focused configuration mutation service and CLI commands.
3. Add the GUI selector action using the same service.
4. Add tests, run focused validation, then document results below.

## Implementation Report

**Status:** implemented and focused-test validated

### Delivered

- Added `llama-orch binary register <package-dir> --version <id> --variant <id>`.
  It copies a full local package to a new UUID directory, requires
  `llama-server.exe`, records its SHA-256, size, executable inventory, and
  local provenance. It rejects a source already managed under `bins/`.
- Added `llama-orch config set-binary <instance> <binary-uuid> [--restart]`.
  The command accepts an unambiguous UUID prefix, writes only the selected
  instance's binary pin and package identity, and requires `--restart` for a
  currently running instance.
- Added the GUI `Switch server` button and `Switch llama-server version...`
  context action. Both list registered packages, accept a UUID/prefix, and
  request confirmation before restarting a running server.
- Added the GUI `Server versions` browser. It is available from the toolbar,
  gives each package's UUID and identity in a table, identifies
  incomplete managed packages, and has an `Import local package...` flow.
  The importer requires explicit build/version and variant labels, validates
  the selected folder contains `llama-server.exe`, then copies the complete
  package into the UUID-managed `bins/` structure in the background.
- Made an explicit `binary_id` fail closed: a missing registry record or
  `llama-server.exe` now fails resolution rather than launching the registry
  default or legacy package.
- Allowed locally descriptive ROCm variants (for example,
  `win-hip-gfx1030-rocm10-r3`) in an instance pin. Official download variants
  remain available for the release installer.

### Operator Workflow

```powershell
# Register a complete, already packaged ROCm build. This preserves the DLL set.
llama-orch binary register 'K:\Data_science_projects\ROCm llama-server\artifacts\package\R3' `
  --version llama.cpp-r3-gfx1030-20260826 `
  --variant win-hip-gfx1030-rocm10-r3

# Inspect identity, then pin the same instance to the returned UUID.
llama-orch binary list
llama-orch config set-binary 00000093 <uuid>

# Only when the instance is running and the restart is intended.
llama-orch config set-binary 00000093 <uuid> --restart
```

The switch does not make a speed or correctness claim. After selecting a real
candidate, validate its package-local integrity, `--list-devices`, real model
load, health endpoint, and deterministic completion before benchmarking.

### Validation Evidence

| Check | Result |
|---|---|
| `python -m compileall -q src` | passed |
| `pytest tests/test_binaries.py tests/test_binary_switching.py tests/test_config.py -q --no-cov` | 63 passed |
| `pytest tests/test_gui.py tests/test_gui_modules.py tests/test_loader.py -q --no-cov` | 111 passed |
| CLI help for `binary register` and `config set-binary` | passed |
| `git diff --check` | passed (existing CRLF advisory warnings only) |
| Ruff | not run: it is not installed in the active virtual environment or PATH |

The focused tests cover whole-package import, missing executable rejection,
strict UUID resolution, custom ROCm variant pins, preservation of non-binary
instance settings, and the running-instance restart guard. No live server was
restarted and no existing instance config or registry entry was changed during
this implementation.

### Follow-up: Version Browser and GUI Importer

The browser/importer extension was implemented without changing existing
registry entries or instance pins. Open **Server versions** in the GUI, select
**Import local package...**, choose the package directory, enter the immutable
build/version ID and variant, then import. The package is copied—not moved—so
the source build remains untouched. The browser refreshes after the background
copy completes and the returned UUID can be used through **Switch server**.

Validation for this extension: `python -m compileall -q src` passed and
`pytest tests/test_binary_switching.py tests/test_binaries.py tests/test_gui.py
tests/test_gui_modules.py tests/test_loader.py -q --no-cov` passed with 142
tests. The new GUI-module coverage verifies that the browser exposes UUIDs,
orders recent packages first, formats package size, and marks a missing
`llama-server.exe` rather than reporting the package as ready.

### Follow-up: GUI Startup with Missing Package Pins

An explicit binary UUID remains strict for launch: a missing package still
blocks `up`/`restart`. The GUI had incorrectly used that launch-time resolution
to compute display-only GPU labels, which meant one stale package pin could
prevent the whole window from opening. GPU inventory and table rendering now
fall back to configuration-derived labels only when binary resolution fails;
they do not make the server runnable or hide the package problem.

Validation: 128 focused tests passed, including a missing-UUID-pin regression,
and the exact inventory path completed against the current 84 instance configs
without starting or changing any server.

### Follow-up: Scrollable Server Switch Picker and Import Validation

**Status:** implemented and focused-test validated

`Switch server` now opens a modal, scrollable package picker rather than a
free-text UUID prompt. It shows each registered package's UUID, version,
variant, managed folder, and readiness. The current UUID pin is preselected.
Choose a row (or double-click it), then select **Confirm selected server**.
Only a package that contains `llama-server.exe` can be confirmed. If the
instance is running, the next confirmation explicitly asks whether to restart
it; otherwise the new pin applies to its next start.

The GUI importer now validates the selected source before it closes or starts
the background copy: the folder must exist, contain `llama-server.exe`, be
outside the orchestrator-managed `bins/` directory, and have non-empty build
and variant labels. This gives an immediate actionable error for the common
invalid selections and preserves the whole-package copy behavior for valid
sources.

Validation: `python -m compileall -q src` passed and the focused test suite
(`test_binary_switching`, `test_binaries`, `test_detection`, `test_gui`,
`test_gui_modules`, and `test_loader`) passed with **155 tests**. The added
tests cover normalizing a valid local source plus early rejection of a missing
executable and a source already under managed `bins/`. No package was imported,
server restarted, registry entry changed, or instance pin changed during this
validation.

### Follow-up: Dedicated Import Server Toolbar Action

The main window now has an explicit **Import server** button beside **Server
versions**. It opens the version browser and immediately opens the same local
package importer, so importing no longer depends on discovering a secondary
button in the browser window. The browser retains **Import server...** as the
equivalent action when it is already open.

Validation: `python -m compileall -q src` passed and `pytest tests/test_gui.py
tests/test_gui_modules.py -q --no-cov` passed with **96 tests**. The added
regression test verifies that the dedicated action opens the browser importer.

### Follow-up: Whole ROCm Build Import

The importer now accepts either `artifacts/build/<build-id>` or its `bin`
subdirectory. For this established ROCm layout it resolves the matching,
self-contained `artifacts/package/<build-id>` bundle and copies that bundle to
`bins/<UUID>`. This prevents a raw CMake `bin` directory—without ROCm runtime
DLLs—from being registered as a runnable server. If the matching package does
not exist, the importer stops with an instruction to run
`Package-RocmRuntime.ps1` first.

Validation: `python -m compileall -q src` passed and the focused binary/GUI
suite passed with **148 tests**. A read-only resolution check against
`llama.cpp-b10199-rocm10.1-nightly-gfx1030-batch-c-c3-vmm-20260823-r3`
resolved its build root to the matching package root and verified both
`llama-server.exe` and `manifest.json` there. No binary was imported, switched,
or started during validation.
