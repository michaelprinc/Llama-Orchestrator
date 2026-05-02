<#
.SYNOPSIS
    Install Windows Task Scheduler autostart for llama-orchestrator.

.DESCRIPTION
    Registers or removes a Windows scheduled task that starts
    llama-orchestrator after local Windows startup. The task invokes
    Start-Autostart.ps1, which starts the daemon and optionally configured
    model instances with audit logging.

.PARAMETER TaskName
    Name of the scheduled task.

.PARAMETER Trigger
    Task trigger. AtStartup runs when Windows starts and usually requires
    Administrator privileges. AtLogOn runs when the current user signs in.

.PARAMETER StartInstances
    Start model instances after the daemon starts.

.PARAMETER InstanceNames
    Specific instances to start when StartInstances is enabled.

.PARAMETER Uninstall
    Remove the scheduled task instead of installing it.

.PARAMETER AuditLogPath
    Path to the audit log file.

.EXAMPLE
    .\Install-AutostartTask.ps1 -Trigger AtStartup

.EXAMPLE
    .\Install-AutostartTask.ps1 -Trigger AtLogOn -StartInstances -InstanceNames gpt-oss

.EXAMPLE
    .\Install-AutostartTask.ps1 -Uninstall

.NOTES
    Autor: MichaelPrinc & Codex
    Datum: 2026-05-02
    Verze: 1.0
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter()]
    [string]$TaskName = "llama-orchestrator-autostart",

    [Parameter()]
    [ValidateSet("AtStartup", "AtLogOn")]
    [string]$Trigger = "AtStartup",

    [Parameter()]
    [switch]$StartInstances,

    [Parameter()]
    [string[]]$InstanceNames = @(),

    [Parameter()]
    [switch]$Uninstall,

    [Parameter()]
    [string]$AuditLogPath
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BootstrapScript = Join-Path $ScriptDir "Start-Autostart.ps1"

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
    if (-not (Test-Path $BootstrapScript)) {
        throw "Autostart bootstrap script not found at $BootstrapScript"
    }

    if ($Uninstall) {
        if ($PSCmdlet.ShouldProcess("Scheduled task '$TaskName'", "Unregister")) {
            $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($existing) {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
                Write-AutostartAuditLog -Action "autostart_task_uninstall" -Result "Success" -Details @{ TaskName = $TaskName }
                Write-Host "Removed scheduled task '$TaskName'."
            } else {
                Write-AutostartAuditLog -Action "autostart_task_uninstall" -Result "SkippedMissing" -Details @{ TaskName = $TaskName }
                Write-Host "Scheduled task '$TaskName' does not exist."
            }
        }
        return
    }

    $bootstrapArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$BootstrapScript`"",
        "-AuditLogPath", "`"$AuditLogPath`""
    )

    if ($StartInstances) {
        $bootstrapArgs += "-StartInstances"
    }

    if ($InstanceNames.Count -gt 0) {
        $bootstrapArgs += "-InstanceNames"
        foreach ($name in $InstanceNames) {
            $bootstrapArgs += "`"$name`""
        }
    }

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($bootstrapArgs -join " ") -WorkingDirectory $ProjectRoot

    if ($Trigger -eq "AtStartup") {
        $taskTrigger = New-ScheduledTaskTrigger -AtStartup
    } else {
        $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
    }

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $description = "Starts llama-orchestrator daemon after Windows startup. Project: $ProjectRoot"

    if ($PSCmdlet.ShouldProcess("Scheduled task '$TaskName'", "Register")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $action `
            -Trigger $taskTrigger `
            -Settings $settings `
            -Description $description `
            -Force | Out-Null

        Write-AutostartAuditLog -Action "autostart_task_install" -Result "Success" -Details @{
            TaskName = $TaskName
            Trigger = $Trigger
            ProjectRoot = $ProjectRoot
            StartInstances = [bool]$StartInstances
            InstanceNames = $InstanceNames
        }

        Write-Host "Registered scheduled task '$TaskName' with trigger '$Trigger'."
        Write-Host "Audit log: $AuditLogPath"
    }
} catch {
    Write-AutostartAuditLog -Action "autostart_task_install" -Result "Failed" -Details @{
        TaskName = $TaskName
        Error = $_.Exception.Message
    }
    throw
}
