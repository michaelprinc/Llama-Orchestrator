<#
.SYNOPSIS
    Install llama-orchestrator as a Windows service using NSSM
    
.DESCRIPTION
    Installs the orchestrator daemon as a Windows service that starts
    automatically on system boot.
    
.PARAMETER ServiceName
    Name for the Windows service (default: llama-orchestrator)
    
.PARAMETER Uninstall
    Remove the service instead of installing
    
.EXAMPLE
    .\install-service.ps1
    
.EXAMPLE
    .\install-service.ps1 -Uninstall
    
.NOTES
    Requires NSSM (Non-Sucking Service Manager) to be installed.
    Download from: https://nssm.cc/download
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ServiceName = "llama-orchestrator",
    
    [Parameter()]
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

function Write-Header {
    param([string]$Text)
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host " $Text" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-Step {
    param([string]$Text)
    Write-Host "[*] $Text" -ForegroundColor Yellow
}

function Write-Success {
    param([string]$Text)
    Write-Host "[OK] $Text" -ForegroundColor Green
}

function Write-ErrorCustom {
    param([string]$Text)
    Write-Host "[ERROR] $Text" -ForegroundColor Red
}

# Check for admin rights
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-ErrorCustom "This script requires Administrator privileges."
    Write-Host "Please run PowerShell as Administrator and try again."
    exit 1
}

# Check for NSSM
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssmPath) {
    Write-ErrorCustom "NSSM not found in PATH."
    Write-Host ""
    Write-Host "Please install NSSM:" -ForegroundColor Yellow
    Write-Host "  1. Download from: https://nssm.cc/download" -ForegroundColor Gray
    Write-Host "  2. Extract to a folder in your PATH (e.g., C:\Windows)" -ForegroundColor Gray
    Write-Host "  3. Or use: winget install nssm" -ForegroundColor Gray
    exit 1
}

if ($Uninstall) {
    Write-Header "Uninstalling $ServiceName Service"
    
    Write-Step "Stopping service..."
    & nssm stop $ServiceName 2>$null
    
    Write-Step "Removing service..."
    & nssm remove $ServiceName confirm
    
    Write-Success "Service removed successfully"
    exit 0
}

Write-Header "Installing $ServiceName Service"

# Find Python executable
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-ErrorCustom "Virtual environment not found at: $VenvPython"
    Write-Host "Please run 'uv sync' first to create the virtual environment."
    exit 1
}

Write-Step "Installing service: $ServiceName"

# Install the service
& nssm install $ServiceName $VenvPython "-m" "llama_orchestrator" "daemon" "start" "--foreground"

# Configure service
Write-Step "Configuring service..."

& nssm set $ServiceName AppDirectory $ProjectRoot
& nssm set $ServiceName DisplayName "llama-orchestrator Daemon"
& nssm set $ServiceName Description "Docker-like orchestration for llama.cpp server instances"
& nssm set $ServiceName Start SERVICE_AUTO_START

# Log files
$LogDir = Join-Path $ProjectRoot "logs\daemon"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
& nssm set $ServiceName AppStdout (Join-Path $LogDir "stdout.log")
& nssm set $ServiceName AppStderr (Join-Path $LogDir "stderr.log")
& nssm set $ServiceName AppRotateFiles 1
& nssm set $ServiceName AppRotateBytes 10485760  # 10 MB

Write-Success "Service installed successfully"

Write-Host ""
Write-Host "Service management commands:" -ForegroundColor Cyan
Write-Host "  Start:   nssm start $ServiceName" -ForegroundColor Gray
Write-Host "  Stop:    nssm stop $ServiceName" -ForegroundColor Gray
Write-Host "  Status:  nssm status $ServiceName" -ForegroundColor Gray
Write-Host "  Edit:    nssm edit $ServiceName" -ForegroundColor Gray
Write-Host "  Remove:  .\install-service.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""

# Ask to start now
$response = Read-Host "Start service now? (y/n)"
if ($response -eq "y" -or $response -eq "Y") {
    Write-Step "Starting service..."
    & nssm start $ServiceName
    Write-Success "Service started"
}
