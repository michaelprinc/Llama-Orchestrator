<#
.SYNOPSIS
    Bootstrap llama-orchestrator after Windows startup.

.DESCRIPTION
    Starts the llama-orchestrator daemon and optionally starts configured model
    instances. This script is intended to be invoked by Windows Task Scheduler.
    It writes an audit log for every significant action.

.PARAMETER StartInstances
    Starts model instances after the daemon is started. Use InstanceNames to
    limit which instances are started.

.PARAMETER InstanceNames
    Specific instance names to start. When omitted with StartInstances, all
    configured instances are started.

.PARAMETER AuditLogPath
    Path to the audit log file.

.EXAMPLE
    .\Start-Autostart.ps1

.EXAMPLE
    .\Start-Autostart.ps1 -StartInstances -InstanceNames gpt-oss

.NOTES
    Autor: MichaelPrinc & Codex
    Datum: 2026-05-02
    Verze: 1.0
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [switch]$StartInstances,

    [Parameter()]
    [string[]]$InstanceNames = @(),

    [Parameter()]
    [string]$AuditLogPath
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LlamaWrapper = Join-Path $ScriptDir "llama.ps1"

if (-not $AuditLogPath) {
    $AuditLogPath = Join-Path $ProjectRoot "logs\autostart-audit.log"
}

function Write-AutostartAuditLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Action,

        [Parameter(Mandatory = $true)]
        [string]$Result,

        [Parameter()]
        [hashtable]$Details = @{}
    )

    if ($WhatIfPreference) {
        Write-Verbose "WhatIf audit skipped: $Action / $Result"
        return
    }

    $logDir = Split-Path -Parent $AuditLogPath
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    $metadata = if ($Details.Count -gt 0) {
        ($Details | ConvertTo-Json -Compress -Depth 5)
    } else {
        "{}"
    }

    $line = "[{0}] [{1}] [{2}] {3}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Action, $Result, $metadata
    Add-Content -Path $AuditLogPath -Value $line -Encoding UTF8
}

try {
    Write-Verbose "Starting llama-orchestrator autostart bootstrap from $ProjectRoot"
    Write-AutostartAuditLog -Action "autostart_bootstrap" -Result "Started" -Details @{
        ProjectRoot = $ProjectRoot
        StartInstances = [bool]$StartInstances
        InstanceNames = $InstanceNames
    }

    if (-not (Test-Path $LlamaWrapper)) {
        throw "CLI wrapper not found at $LlamaWrapper"
    }

    if ($PSCmdlet.ShouldProcess("llama-orchestrator daemon", "Start")) {
        try {
            & $LlamaWrapper daemon start
            Write-AutostartAuditLog -Action "daemon_start" -Result "Success" -Details @{}
        } catch {
            $message = $_.Exception.Message
            if ($message -match "already running") {
                Write-AutostartAuditLog -Action "daemon_start" -Result "SkippedAlreadyRunning" -Details @{ Message = $message }
            } else {
                throw
            }
        }
    }

    if ($StartInstances) {
        $targets = @()
        if ($InstanceNames.Count -gt 0) {
            $targets = $InstanceNames
        } else {
            $instancesDir = Join-Path $ProjectRoot "instances"
            if (Test-Path $instancesDir) {
                $targets = Get-ChildItem -Path $instancesDir -Directory |
                    Where-Object { Test-Path (Join-Path $_.FullName "config.json") } |
                    Select-Object -ExpandProperty Name
            }
        }

        foreach ($name in $targets) {
            if ($PSCmdlet.ShouldProcess("llama-orchestrator instance '$name'", "Start")) {
                try {
                    & $LlamaWrapper up $name
                    Write-AutostartAuditLog -Action "instance_start" -Result "Success" -Details @{ Name = $name }
                } catch {
                    $message = $_.Exception.Message
                    if ($message -match "already running") {
                        Write-AutostartAuditLog -Action "instance_start" -Result "SkippedAlreadyRunning" -Details @{
                            Name = $name
                            Message = $message
                        }
                    } else {
                        Write-AutostartAuditLog -Action "instance_start" -Result "Failed" -Details @{
                            Name = $name
                            Error = $message
                        }
                    }
                }
            }
        }
    }

    Write-AutostartAuditLog -Action "autostart_bootstrap" -Result "Success" -Details @{}
} catch {
    Write-AutostartAuditLog -Action "autostart_bootstrap" -Result "Failed" -Details @{
        Error = $_.Exception.Message
    }
    throw
}
