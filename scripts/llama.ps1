<#
.SYNOPSIS
    PowerShell wrapper for llama-orchestrator CLI
    
.DESCRIPTION
    Convenience wrapper that calls the Python CLI.
    Automatically detects and uses the virtual environment if available.
    
.EXAMPLE
    .\llama.ps1 up gpt-oss
    
.EXAMPLE
    .\llama.ps1 ps
    
.EXAMPLE
    .\llama.ps1 dashboard
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Verbose "llama.ps1 running from: $ScriptDir"
Write-Verbose "Project root: $ProjectRoot"

# Check for virtual environment
$VenvPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPath) {
    # Use venv Python directly
    Write-Verbose "Using virtual environment Python: $VenvPath"
    & $VenvPath -m llama_orchestrator @Arguments
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    # Use uv run
    Write-Verbose "Using uv run"
    Push-Location $ProjectRoot
    try {
        uv run python -m llama_orchestrator @Arguments
    } finally {
        Pop-Location
    }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    # Fallback to system Python
    Write-Verbose "Using system Python"
    Push-Location $ProjectRoot
    try {
        python -m llama_orchestrator @Arguments
    } finally {
        Pop-Location
    }
} else {
    Write-Error "Python not found. Please install Python 3.11+ or uv."
    exit 1
}