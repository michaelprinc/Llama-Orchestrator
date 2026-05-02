<#
.SYNOPSIS
    PowerShell wrapper for llama-orchestrator CLI
    
.DESCRIPTION
    Convenience wrapper that calls the Python CLI.
    
.EXAMPLE
    .\llama.ps1 up gpt-oss
    
.EXAMPLE
    .\llama.ps1 ps
    
.EXAMPLE
    .\llama.ps1 dashboard
#>

param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = $ScriptDir

# Check for virtual environment
$VenvPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$UvPath = Join-Path $ProjectRoot ".venv\Scripts\uv.exe"

if (Test-Path $VenvPath) {
    # Use venv Python directly
    & $VenvPath -m llama_orchestrator @Arguments
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    # Use uv run
    Push-Location $ProjectRoot
    try {
        uv run python -m llama_orchestrator @Arguments
    } finally {
        Pop-Location
    }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    # Fallback to system Python
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
