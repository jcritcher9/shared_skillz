#Requires -Version 5.1
<#
.SYNOPSIS
    Generic local launcher for EasyImports API + Django (two-process stack).

.DESCRIPTION
    Single command to:
      - optionally load a .env if present (not required)
      - apply local host/port defaults
      - start API + Django in separate PowerShell windows
      - wait for HTTP readiness
      - open the Django home page in the browser

    This is the day-to-day local path. It does NOT require Path C / HubSpot live
    configuration. The API default target remains fake-preview-v1 unless your
    environment opts into something else.

    For the live HubSpot company pilot, use scripts\start_path_c.ps1 instead.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1 -SkipBrowser

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1 `
      -EnvFile C:\Users\you\Documents\easyimports_local\.env
#>
[CmdletBinding()]
param(
    # Optional absolute path to .env. When omitted, discovers common locations
    # or runs with process defaults if none exist.
    [string]$EnvFile = "",

    # Repository root. Defaults to parent of scripts/.
    [string]$RepoRoot = "",

    # Skip opening the browser after startup.
    [switch]$SkipBrowser,

    # Skip Django migrate in the Django window.
    [switch]$SkipMigrate,

    # Do not wait for HTTP readiness before opening the browser.
    [switch]$NoWait,

    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [string]$DjangoHost = "127.0.0.1",
    [int]$DjangoPort = 8001,

    # Seconds to wait for each service HTTP probe.
    [int]$ReadyTimeoutSeconds = 45,

    # Optional absolute Python executable. When omitted, prefers repo .venv when
    # healthy, else ambient PATH python only if it passes the full local probe.
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$libDir = Join-Path $PSScriptRoot "lib"
$lib = Join-Path $libDir "EasyImportsDev.ps1"
if (-not (Test-Path -LiteralPath $lib)) {
    Write-Host "[ERROR] Missing shared library: $lib" -ForegroundColor Red
    exit 1
}
. $lib

try {
    if (-not $RepoRoot -or -not $RepoRoot.Trim()) {
        $RepoRoot = Get-EasyImportsRepoRoot -StartPath $PSScriptRoot
    } else {
        $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
    }

    Write-Host ""
    Write-Host "EasyImports local launcher" -ForegroundColor White
    Write-Host "=========================="
    Write-Host "Repo: $RepoRoot"
    Write-Host ""

    # One resolved absolute interpreter for probes, install check, API, and Django.
    $resolvedPython = Resolve-EasyImportsPythonExecutable `
        -RepoRoot $RepoRoot `
        -PythonExe $PythonExe
    $pythonVersion = Get-EasyImportsPythonVersion -PythonExe $resolvedPython
    Show-EasyImportsPythonSummary -PythonExe $resolvedPython -Version $pythonVersion
    Assert-EasyImportsPythonLocalDependencies -PythonExe $resolvedPython -RepoRoot $RepoRoot

    $resolvedEnv = Resolve-EasyImportsEnvFileOptional -EnvFile $EnvFile -RepoRoot $RepoRoot
    if ($resolvedEnv) {
        Import-EasyImportsEnvFile -Path $resolvedEnv | Out-Null
    } else {
        Write-EiInfo "No .env found; using process defaults (fake-preview / SQLite)"
    }

    # Apply launcher host/port defaults when env omits them.
    if (-not $env:EASYIMPORTS_API_BASE_URL -or -not $env:EASYIMPORTS_API_BASE_URL.Trim()) {
        $env:EASYIMPORTS_API_BASE_URL = "http://${ApiHost}:${ApiPort}"
    }
    if (-not $env:EASYIMPORTS_API_HOST -or -not $env:EASYIMPORTS_API_HOST.Trim()) {
        $env:EASYIMPORTS_API_HOST = $ApiHost
    }
    if (-not $env:EASYIMPORTS_API_PORT -or -not $env:EASYIMPORTS_API_PORT.Trim()) {
        $env:EASYIMPORTS_API_PORT = "$ApiPort"
    }
    if (-not $env:EASYIMPORTS_DEBUG -or -not $env:EASYIMPORTS_DEBUG.Trim()) {
        $env:EASYIMPORTS_DEBUG = "true"
    }
    if (-not $env:EASYIMPORTS_ALLOWED_HOSTS -or -not $env:EASYIMPORTS_ALLOWED_HOSTS.Trim()) {
        $env:EASYIMPORTS_ALLOWED_HOSTS = "localhost,127.0.0.1"
    }

    # Storage truth + refuse poisoned / missing non-default bootstrap targets.
    $installReport = Get-EasyImportsOperatorInstallReport `
        -RepoRoot $RepoRoot `
        -PythonExe $resolvedPython
    Show-EasyImportsOperatorInstallSummary -Report $installReport
    Assert-EasyImportsOperatorInstallSafe -Report $installReport

    Write-Host ""
    Write-Host "Local configuration summary" -ForegroundColor White
    Write-Host "---------------------------"
    Write-Host ("PYTHON={0}" -f $resolvedPython)
    Write-Host ("PYTHON_VERSION={0}" -f $pythonVersion)
    Write-Host ("API_BASE={0}" -f $env:EASYIMPORTS_API_BASE_URL)
    Write-Host ("API_HOST={0}" -f $env:EASYIMPORTS_API_HOST)
    Write-Host ("API_PORT={0}" -f $env:EASYIMPORTS_API_PORT)
    Write-Host ("DJANGO={0}:{1}" -f $DjangoHost, $DjangoPort)
    Write-Host ("DEBUG={0}" -f $env:EASYIMPORTS_DEBUG)
    $hasApi = Test-EasyImportsApiPackagePresent -RepoRoot $RepoRoot
    Write-Host ("API_PACKAGE={0}" -f $(if ($hasApi) { "mappings_2.api.app" } else { "<absent - Django-only>" }))
    Write-Host ("STATE={0}" -f $(if ($installReport.data_root) { $installReport.data_root } else { "<n/a>" }))
    Write-Host ("STATE_SOURCE={0}" -f $installReport.source)
    Write-Host ("BOOTSTRAP={0}" -f $(if ($installReport.bootstrap_path) { $installReport.bootstrap_path } else { "<n/a>" }))
    Write-Host ("DB={0}" -f $(if ($env:EASYIMPORTS_DB_PATH) { $env:EASYIMPORTS_DB_PATH } else { "<default web/db.sqlite3>" }))
    Write-Host ("MEDIA={0}" -f $(if ($env:EASYIMPORTS_MEDIA_ROOT) { $env:EASYIMPORTS_MEDIA_ROOT } else { "<default web/media/>" }))
    Write-Host ("ENV_FILE={0}" -f $(if ($resolvedEnv) { $resolvedEnv } else { "<none>" }))
    Write-Host ""

    if ($hasApi) {
        Assert-EasyImportsPortsFree `
            -ApiHost $ApiHost `
            -ApiPort $ApiPort `
            -DjangoHost $DjangoHost `
            -DjangoPort $DjangoPort
    } else {
        Assert-EasyImportsPortsFree `
            -ApiHost $DjangoHost `
            -ApiPort $DjangoPort `
            -DjangoHost $DjangoHost `
            -DjangoPort $DjangoPort
    }

    $startArgs = @{
        RepoRoot   = $RepoRoot
        ApiHost    = $ApiHost
        ApiPort    = $ApiPort
        DjangoHost = $DjangoHost
        DjangoPort = $DjangoPort
        PythonExe  = $resolvedPython
    }
    if ($resolvedEnv) {
        $startArgs["EnvFile"] = $resolvedEnv
    }

    $diagDir = New-EasyImportsServiceDiagnosticDir
    $startArgs["DiagnosticDir"] = $diagDir
    $apiSvc = $null
    if ($hasApi) {
        $apiSvc = Start-EasyImportsServiceWindow @startArgs -Title "API" -Service api
    } else {
        Write-EiWarn "Skipping API window: mappings_2/api/app.py is not in this repository."
    }
    $djangoSvc = Start-EasyImportsServiceWindow @startArgs -Title "Django" -Service django -SkipMigrate:$SkipMigrate

    $apiUrl = "http://${ApiHost}:${ApiPort}/health"
    $djangoUrl = "http://${DjangoHost}:${DjangoPort}/"

    if (-not $NoWait) {
        if ($hasApi) {
            [void](Wait-EasyImportsHttpReady `
                -Url $apiUrl `
                -TimeoutSeconds $ReadyTimeoutSeconds `
                -Label "API" `
                -Process $apiSvc.Process `
                -PythonExe $resolvedPython `
                -DiagnosticLog $apiSvc.DiagnosticLog)
        }
        [void](Wait-EasyImportsHttpReady `
            -Url $djangoUrl `
            -TimeoutSeconds $ReadyTimeoutSeconds `
            -Label "Django" `
            -Process $djangoSvc.Process `
            -PythonExe $resolvedPython `
            -DiagnosticLog $djangoSvc.DiagnosticLog)
    } else {
        Write-EiInfo -Message "NoWait set; launch requested without readiness checks"
        Start-Sleep -Seconds 3
    }

    if (-not $SkipBrowser) {
        Open-EasyImportsBrowser -Url $djangoUrl
    } else {
        Write-EiInfo -Message ("SkipBrowser set; open manually: {0}" -f $djangoUrl)
    }

    Write-Host ""
    if (-not $NoWait) {
        Write-EiOk "Local stack is ready"
    } else {
        Write-EiOk "Local stack launch requested (readiness not verified; -NoWait)"
    }
    Write-Host "  Python:        $resolvedPython ($pythonVersion)"
    if ($hasApi) {
        Write-Host "  API window:    -m mappings_2.api.app  ($ApiHost`:$ApiPort)"
        Write-Host "  API health:    $apiUrl"
        Write-Host "  API docs:      http://${ApiHost}:${ApiPort}/docs"
    } else {
        Write-Host "  API window:    skipped (no mappings_2.api.app in this tree)"
    }
    Write-Host "  Django window: runserver $DjangoHost`:$DjangoPort"
    Write-Host "  Diagnostics:   $diagDir"
    Write-Host "  Django:        $djangoUrl"
    Write-Host ""
    Write-Host "Leave the service window(s) open. Close them (or Ctrl+C) to stop."
    Write-Host "This launcher window can be closed safely."
    Write-Host ""
    exit 0
} catch {
    Write-EiErr $_.Exception.Message
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    }
    exit 1
}
