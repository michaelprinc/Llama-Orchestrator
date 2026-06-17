# llama.ps1 Change Analysis & Best Practices

**Date:** 2026-06-17  
**Author:** Hermes Agent (Software Development Profile)  
**Status:** Analysis Complete

---

## 1. Executive Summary

The `llama.ps1` script was modified externally to handle a critical Windows environment issue where the `.venv` directory (created by `uv`) may not contain a usable Windows Python executable. The external fix introduced:

1. A primary `.venv-windows` directory path
2. A fallback mechanism to check `.venv` if `.venv-windows` doesn't exist
3. Environment variable (`UV_PROJECT_ENVIRONMENT`) management for `uv run`
4. Proper cleanup of environment variables in the `finally` block

This analysis examines what changed, why the previous automated changes may not have applied successfully, and establishes best practices for future `.ps1` and `.py` script modifications.

---

## 2. Changes Made to llama.ps1

### 2.1 Previous Version (Hermes Agent Generated)

```powershell
$VenvPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPath) {
    Write-Verbose "Using virtual environment Python: $VenvPath"
    & $VenvPath -m llama_orchestrator @Arguments
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Verbose "Using uv run"
    Push-Location $ProjectRoot
    try {
        uv run python -m llama_orchestrator @Arguments
    } finally {
        Pop-Location
    }
}
```

### 2.2 Current Version (Externally Modified)

```powershell
$VenvPath = Join-Path $ProjectRoot ".venv-windows\Scripts\python.exe"
$DefaultVenvDir = Join-Path $ProjectRoot ".venv-windows"

if (-not (Test-Path $VenvPath)) {
    $VenvPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $DefaultVenvDir = Join-Path $ProjectRoot ".venv"
}

if (Test-Path $VenvPath) {
    Write-Verbose "Using virtual environment Python: $VenvPath"
    & $VenvPath -m llama_orchestrator @Arguments
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Verbose "Using uv run"
    Push-Location $ProjectRoot
    try {
        $previousUvProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
        $setUvProjectEnvironment = $false
        if ((Test-Path $DefaultVenvDir) -and -not $env:UV_PROJECT_ENVIRONMENT) {
            $env:UV_PROJECT_ENVIRONMENT = ".venv-windows"
            $setUvProjectEnvironment = $true
            Write-Verbose "Default .venv is not a usable Windows venv; using UV_PROJECT_ENVIRONMENT=.venv-windows"
        }
        uv run python -m llama_orchestrator @Arguments
    } finally {
        if ($setUvProjectEnvironment) {
            if ($null -eq $previousUvProjectEnvironment) {
                Remove-Item Env:\UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
            } else {
                $env:UV_PROJECT_ENVIRONMENT = $previousUvProjectEnvironment
            }
        }
        Pop-Location
    }
}
```

### 2.3 Key Changes Identified

| Change | Description | Impact |
|--------|-------------|--------|
| **New variable** | `$DefaultVenvDir` tracks the default venv directory | Enables environment variable logic |
| **Fallback logic** | Checks `.venv-windows` first, falls back to `.venv` | Handles Windows-specific venv issues |
| **Environment variable** | Sets `$env:UV_PROJECT_ENVIRONMENT` conditionally | Allows `uv run` to use correct venv |
| **Cleanup logic** | Restores previous environment variable in `finally` | Prevents environment pollution |
| **Verbose logging** | Added message when UV_PROJECT_ENVIRONMENT is set | Improves debuggability |

---

## 3. Why Previous Changes May Not Have Applied Successfully

### 3.1 Potential Root Causes

Based on the analysis, here are the most likely reasons why the previous changes didn't apply:

#### A. **Environment State Dependency**
The external fix specifically addresses the `UV_PROJECT_ENVIRONMENT` environment variable issue. The previous version:
- Assumed `.venv` would always be usable
- Didn't account for Windows-specific virtual environment creation differences
- Lacked the fallback mechanism for when `.venv` doesn't exist

#### B. **Toolchain Differences**
The changes were likely made using a different tool or editor that:
- Had access to a working Windows environment
- Could test the actual script execution
- Used a different version control system or merge strategy

#### C. **Session Isolation**
The Hermes Agent's changes were made in a WSL (Windows Subsystem for Linux) environment, while the script is designed for Windows PowerShell. This created a mismatch:
- Agent ran in Linux (WSL) environment
- Script runs in Windows PowerShell
- Testing wasn't performed in the actual target environment

#### D. **File Encoding/Line Endings**
PowerShell scripts are sensitive to:
- Line endings (CRLF vs LF)
- File encoding (UTF-8 with/without BOM)
- Special characters in paths

The external fix likely preserved the correct Windows line endings (CRLF), while automated changes may have converted them to Unix-style (LF).

### 3.2 Evidence from Current State

1. **Git repository issues**: The `.git` file points to a non-existent path (`../../.git/modules/infra-local/llama-orchestrator`), suggesting the repository may have been restructured or the module reference was broken during automated changes.

2. **No backup files found**: No `.bak`, `.backup`, or `.orig` files were found, indicating the external changes were applied directly without creating backups.

3. **Current version is functional**: The external fix resolved the actual runtime issue, proving that the previous version had a fundamental environmental assumption that didn't hold in production.

---

## 4. Best Practices for .ps1 Script Changes

### 4.1 Pre-Change Analysis

#### A. **Understand the Target Environment**
```powershell
# ALWAYS verify target environment before making changes
$PSVersionTable.PSEdition  # Desktop vs Core
$PSVersionTable.PSVersion  # Version number
$IsWindows                 # Platform check
$env:UV_PROJECT_ENVIRONMENT # Check relevant env vars
```

#### B. **Preserve Existing Behavior**
```powershell
# GOOD: Maintain backward compatibility
if (-not $Force) {
    Write-Warning "This script modifies environment variables. Use -Force to continue."
    exit 1
}

# BAD: Blindly overwrite without warning
$env:UV_PROJECT_ENVIRONMENT = ".venv-windows"
```

#### C. **Test in Target Environment**
```powershell
# Test with verbose output
.\\llama.ps1 --verbose up test-instance

# Test fallback scenarios
Test-Path ".venv\Scripts\python.exe"  # Check primary
Test-Path ".venv-windows\Scripts\python.exe"  # Check fallback
```

### 4.2 Change Implementation

#### A. **Use Proper PowerShell Conventions**
```powershell
# GOOD: Follow PowerShell best practices
[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
```

#### B. **Preserve Line Endings**
```powershell
# When writing files, preserve existing line endings
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
# Check if CRLF
if ($content -match "`r`n") {
    # Preserve CRLF
} else {
    # Use LF
}
```

#### C. **Handle Environment Variables Properly**
```powershell
# GOOD: Save and restore environment variables
$previousEnv = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = ".venv-windows"
    # Do work
} finally {
    if ($null -eq $previousEnv) {
        Remove-Item Env:\UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    } else {
        $env:UV_PROJECT_ENVIRONMENT = $previousEnv
    }
}
```

### 4.3 Post-Change Verification

#### A. **Syntax Validation**
```powershell
# Validate syntax without executing
$null = [System.Management.Automation.PSParser]::ParseFile($path, [ref]$errors)
if ($errors.Count -gt 0) {
    Write-Error "Syntax errors found:"
    $errors | ForEach-Object { Write-Error $_ }
}
```

#### B. **Functional Testing**
```powershell
# Test basic functionality
& $scriptPath --help  # Should work without side effects
& $scriptPath ps      # List instances (read-only operation)
```

#### C. **Environment Isolation**
```powershell
# Test in a clean environment
$env:Path = "C:\Windows\system32;C:\Windows"
# Run in new process to avoid polluting current session
powershell -File $scriptPath --help
```

---

## 5. Best Practices for .py Script Changes

### 5.1 Pre-Change Analysis

#### A. **Understand Dependencies**
```python
# Always check dependencies before making changes
import sys
print(f"Python version: {sys.version}")
print(f"Python path: {sys.executable}")

# Check for required modules
try:
    import typer
    print(f"typer version: {typer.__version__}")
except ImportError:
    print("ERROR: typer not installed")
```

#### B. **Preserve API Compatibility**
```python
# GOOD: Maintain backward compatibility
def old_function(*args, **kwargs):
    """Deprecated, but still works."""
    warnings.warn("Use new_function() instead", DeprecationWarning)
    return new_function(*args, **kwargs)

def new_function(*args, **kwargs):
    """New implementation."""
    pass
```

#### C. **Test Import Chain**
```python
# Verify import chain works
try:
    from llama_orchestrator.cli import app
    print(f"CLI app type: {type(app)}")
except ImportError as e:
    print(f"Import failed: {e}")
```

### 5.2 Change Implementation

#### A. **Use Proper Python Conventions**
```python
# GOOD: Follow PEP 8 and project conventions
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def process_file(path: Path) -> None:
    """Process a single file.
    
    Args:
        path: Path to the file to process.
        
    Raises:
        FileNotFoundError: If the file doesn't exist.
        PermissionError: If permissions don't allow reading.
    """
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
```

#### B. **Handle Platform Differences**
```python
# GOOD: Handle cross-platform differences
import sys
import os

if sys.platform == 'win32':
    # Windows-specific logic
    venv_path = path / ".venv-windows"
else:
    # Unix/Mac-specific logic
    venv_path = path / ".venv"
```

#### C. **Use Proper Error Handling**
```python
# GOOD: Specific exception handling
try:
    result = subprocess.run(
        ["python", "-m", "llama_orchestrator"] + args,
        check=True,
        capture_output=True,
        text=True
    )
except subprocess.CalledProcessError as e:
    logger.error(f"Command failed: {e.stderr}")
    raise
except FileNotFoundError:
    logger.error("Python executable not found")
    raise
```

### 5.3 Post-Change Verification

#### A. **Linting**
```bash
# Run linter
ruff check src/llama_orchestrator/

# Check for typing errors
mypy src/llama_orchestrator/
```

#### B. **Import Verification**
```bash
# Test imports
python -c "from llama_orchestrator.cli import app; print(app)"
python -c "from llama_orchestrator.gui import launch_gui; print(launch_gui)"
```

#### C. **Functional Testing**
```bash
# Test CLI functionality
uv run python -m llama_orchestrator --help
uv run python -m llama_orchestrator ps
uv run python -m llama_orchestrator health
```

---

## 6. Recommendations for Future Development

### 6.1 For PowerShell Scripts (.ps1)

1. **Always test in the target environment**
   - Don't assume Linux/WSL changes translate directly to Windows
   - Use actual Windows PowerShell to test changes
   - Verify line endings are correct (CRLF for Windows)

2. **Preserve environment state**
   - Save and restore environment variables
   - Use try/finally blocks for cleanup
   - Never pollute the user's session

3. **Handle edge cases**
   - Check for both primary and fallback paths
   - Validate that Python executables exist before using them
   - Provide clear error messages when things go wrong

4. **Use proper PowerShell patterns**
   - [CmdletBinding()] for advanced functions
   - Set-StrictMode -Version Latest for strict checking
   - $ErrorActionPreference = "Stop" for error handling

### 6.2 For Python Scripts (.py)

1. **Verify dependencies first**
   - Check that all required packages are installed
   - Use `uv sync` to install dependencies
   - Test imports before making changes

2. **Maintain backward compatibility**
   - Don't break existing APIs
   - Add deprecation warnings before removing functionality
   - Document all API changes

3. **Handle platform differences**
   - Use `sys.platform` checks for Windows/Unix differences
   - Test on all target platforms
   - Use pathlib for path operations

4. **Use proper error handling**
   - Catch specific exceptions, not just generic `Exception`
   - Provide meaningful error messages
   - Log errors appropriately

### 6.3 For Both .ps1 and .py

1. **Version control best practices**
   - Use meaningful commit messages
   - Test before committing
   - Use branches for experimental changes

2. **Documentation**
   - Update README.md when functionality changes
   - Add comments for complex logic
   - Document breaking changes

3. **Testing**
   - Write tests for critical paths
   - Test in actual target environment
   - Use CI/CD when possible

4. **Change management**
   - Review changes before applying
   - Test in staging environment first
   - Have rollback plans for critical changes

---

## 7. Conclusion

The external fix to `llama.ps1` addressed a critical Windows environment issue that the previous automated changes didn't account for. The key lessons are:

1. **Environment matters**: Changes must be tested in the actual target environment
2. **Edge cases matter**: Always handle fallback scenarios
3. **Preserve state**: Never pollute the user's environment
4. **Test thoroughly**: Verify changes work before deploying

By following the best practices outlined in this document, future changes will be more robust and less likely to fail in production.

---

## 8. Appendix: Quick Reference Checklist

### Pre-Change Checklist
- [ ] Understand target environment (OS, version, tools)
- [ ] Check for existing issues/bugs
- [ ] Verify dependencies are available
- [ ] Create backup of current state
- [ ] Plan rollback strategy

### Implementation Checklist
- [ ] Follow project conventions (style, naming, structure)
- [ ] Handle platform-specific differences
- [ ] Add proper error handling
- [ ] Update documentation/comments
- [ ] Test in target environment

### Post-Change Checklist
- [ ] Run linter/formatter
- [ ] Test imports/functionality
- [ ] Verify no regressions
- [ ] Update documentation
- [ ] Commit with meaningful message
- [ ] Notify team of changes

---

**End of Document**