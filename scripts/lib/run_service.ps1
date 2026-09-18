#Requires -Version 5.1
<#
.SYNOPSIS
    Child-window service runner for EasyImports local development.

.DESCRIPTION
    Loads .env via EasyImportsDev.ps1, then starts API or Django.
    Invoked by start_local.ps1 / start_local_https.ps1 / start_path_c.ps1 in a
    new window. Always uses the parent-resolved -PythonExe (never bare python).

    Phase 1B: captures service stdout/stderr to -DiagnosticLog, never blocks on
    ReadLine after failure, and exits so the parent readiness wait observes the
    host process exit code.
#>
param(
    # Optional. When omitted, services run with process defaults / already-set env vars.
    [string]$EnvFile = "",
    [Parameter(Mandatory)][string]$RepoRoot,
    [Parameter(Mandatory)][ValidateSet("api", "django")][string]$Service,
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [string]$DjangoHost = "127.0.0.1",
    [int]$DjangoPort = 8001,
    [switch]$SkipMigrate,
    # HTTPS-2: Django TLS on 127.0.0.1:8001 via manage.py runserver_https.
    [switch]$HttpsLoopback,
    # Absolute Python executable selected by the parent launcher (required).
    [Parameter(Mandatory)][string]$PythonExe,
    # Non-secret local diagnostic log path (parent readiness surfaces a tail).
    [string]$DiagnosticLog = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$lib = Join-Path $PSScriptRoot "EasyImportsDev.ps1"
if (-not (Test-Path -LiteralPath $lib)) {
    throw "Missing shared library: $lib"
}
. $lib

try {
    $host.UI.RawUI.WindowTitle = "EasyImports $Service"
} catch {
    # Non-interactive hosts may not support window title.
}

Write-Host ""
Write-Host "EasyImports service: $Service" -ForegroundColor White
Write-Host "Repo: $RepoRoot"
Write-Host "Python: $PythonExe"
if ($DiagnosticLog -and $DiagnosticLog.Trim()) {
    Write-Host "Diagnostic: $DiagnosticLog"
}
if ($EnvFile -and $EnvFile.Trim()) {
    Write-Host "Env:  $EnvFile"
} else {
    Write-Host "Env:  <none - using process defaults>"
}
Write-Host ""

if (-not $PythonExe -or -not $PythonExe.Trim()) {
    throw "PythonExe is required (parent launcher must pass a resolved absolute interpreter)."
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "PythonExe does not exist: $PythonExe"
}

if ($EnvFile -and $EnvFile.Trim()) {
    Import-EasyImportsEnvFile -Path $EnvFile.Trim() | Out-Null
}

# Ensure child inherits sensible defaults even if .env omitted them.
if (-not $env:EASYIMPORTS_API_BASE_URL) {
    $env:EASYIMPORTS_API_BASE_URL = "http://${ApiHost}:${ApiPort}"
}
if (-not $env:EASYIMPORTS_API_HOST) {
    $env:EASYIMPORTS_API_HOST = $ApiHost
}
if (-not $env:EASYIMPORTS_API_PORT) {
    $env:EASYIMPORTS_API_PORT = "$ApiPort"
}

Set-Location -LiteralPath $RepoRoot

function Write-EasyImportsServiceFailureAndExit {
    param(
        [Parameter(Mandatory)][string]$Message,
        [int]$Code = 1
    )
    $safeMessage = Protect-EasyImportsDiagnosticText -Text $Message
    Write-EiErr $safeMessage
    if ($DiagnosticLog -and $DiagnosticLog.Trim()) {
        try {
            $block = @(
                ("service={0}" -f $Service)
                ("python_exe={0}" -f $PythonExe)
                ("failure={0}" -f $safeMessage)
                ("exit_code={0}" -f $Code)
            ) -join [Environment]::NewLine
            [void](Write-EasyImportsDiagnosticContent -Path $DiagnosticLog -Text $block)
            $tail = Get-EasyImportsDiagnosticTail -Path $DiagnosticLog
            if ($tail) {
                Write-Host "diagnostic_tail:"
                Write-Host $tail
            }
        } catch {
        }
    }
    # Phase 1B: never ReadLine — parent must observe process exit immediately.
    exit $Code
}

# Test-only forced failures (network-free Phase 1B acceptance). Not used in production.
$forceExit = ""
if ($env:EASYIMPORTS_TEST_FORCE_SERVICE_EXIT -and $env:EASYIMPORTS_TEST_FORCE_SERVICE_EXIT.Trim()) {
    $forceExit = $env:EASYIMPORTS_TEST_FORCE_SERVICE_EXIT.Trim().ToLowerInvariant()
}

if ($Service -eq "api") {
    if ($forceExit -eq "api") {
        Write-EiInfo "Launching API on ${ApiHost}:${ApiPort} ..."
        $code = Invoke-EasyImportsServicePython `
            -PythonExe $PythonExe `
            -ArgumentList @(
                "-c",
                "raise ModuleNotFoundError(`"No module named 'pandas'`")"
            ) `
            -WorkingDirectory $RepoRoot `
            -DiagnosticLog $DiagnosticLog `
            -ServiceLabel "api" `
            -InvocationRole "api"
        Write-EasyImportsServiceFailureAndExit -Message "API exited with code $code" -Code $code
    }
    if (-not (Test-EasyImportsApiPackagePresent -RepoRoot $RepoRoot)) {
        Write-EasyImportsServiceFailureAndExit -Message @"
FastAPI application not found: mappings_2/api/app.py

This share package ships Django under web/ and FastAPI *thoughts* under api/.
The live module is python -m mappings_2.api.app from the mill. Copy that
package into this tree, or start the mill API on ${ApiHost}:${ApiPort} yourself.
"@
    }
    Write-EiInfo "Launching API on ${ApiHost}:${ApiPort} ..."
    Write-Host "Command: & '$PythonExe' -m mappings_2.api.app"
    Write-Host "Stop with Ctrl+C in this window."
    Write-Host ""
    $code = Invoke-EasyImportsServicePython `
        -PythonExe $PythonExe `
        -ArgumentList @("-m", "mappings_2.api.app") `
        -WorkingDirectory $RepoRoot `
        -DiagnosticLog $DiagnosticLog `
        -ServiceLabel "api" `
        -InvocationRole "api"
    if ($code -ne 0) {
        Write-EasyImportsServiceFailureAndExit -Message "API exited with code $code" -Code $code
    }
} else {
    $webRoot = Join-Path $RepoRoot "web"
    if (-not (Test-Path -LiteralPath $webRoot)) {
        throw "Django web root not found: $webRoot"
    }
    Set-Location -LiteralPath $webRoot
    if (-not $SkipMigrate) {
        if ($forceExit -eq "django") {
            Write-EiInfo "Running Django migrations..."
            $code = Invoke-EasyImportsServicePython `
                -PythonExe $PythonExe `
                -ArgumentList @(
                    "-c",
                    "raise SystemExit(`"django.db.utils.OperationalError: forced migration failure (Phase 1B test)`")"
                ) `
                -WorkingDirectory $webRoot `
                -DiagnosticLog $DiagnosticLog `
                -ServiceLabel "django" `
                -InvocationRole "django_migrate"
            Write-EasyImportsServiceFailureAndExit -Message "migrate failed with code $code" -Code $code
        }
        Write-EiInfo "Running Django migrations..."
        $code = Invoke-EasyImportsServicePython `
            -PythonExe $PythonExe `
            -ArgumentList @("manage.py", "migrate", "--noinput") `
            -WorkingDirectory $webRoot `
            -DiagnosticLog $DiagnosticLog `
            -ServiceLabel "django" `
            -InvocationRole "django_migrate"
        if ($code -ne 0) {
            Write-EasyImportsServiceFailureAndExit -Message "migrate failed with code $code" -Code $code
        }
        Write-EiOk "Migrations complete"
    }
    if ($HttpsLoopback) {
        $env:EASYIMPORTS_DEBUG = "0"
        if (-not $env:EASYIMPORTS_ALLOWED_HOSTS -or -not $env:EASYIMPORTS_ALLOWED_HOSTS.Trim()) {
            $env:EASYIMPORTS_ALLOWED_HOSTS = "localhost,127.0.0.1"
        }
        $env:EASYIMPORTS_HTTPS_LOOPBACK = "1"
        if (-not $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS -or -not $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS.Trim()) {
            $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS = "https://${DjangoHost}:${DjangoPort}"
        }
        Write-EiInfo "Launching Django HTTPS loopback on ${DjangoHost}:${DjangoPort} ..."
        Write-Host "Command: & '$PythonExe' manage.py runserver_https ${DjangoHost}:${DjangoPort}"
        Write-Host "Origin:  https://${DjangoHost}:${DjangoPort}/"
        Write-Host "Cookies: Secure + SameSite=Lax"
        Write-Host "Stop with Ctrl+C in this window."
        Write-Host ""
        $code = Invoke-EasyImportsServicePython `
            -PythonExe $PythonExe `
            -ArgumentList @("manage.py", "runserver_https", "${DjangoHost}:${DjangoPort}") `
            -WorkingDirectory $webRoot `
            -DiagnosticLog $DiagnosticLog `
            -ServiceLabel "django" `
            -InvocationRole "django_runserver"
    } else {
        Write-EiInfo "Launching Django on ${DjangoHost}:${DjangoPort} ..."
        Write-Host "Command: & '$PythonExe' manage.py runserver ${DjangoHost}:${DjangoPort}"
        Write-Host "Stop with Ctrl+C in this window."
        Write-Host ""
        $code = Invoke-EasyImportsServicePython `
            -PythonExe $PythonExe `
            -ArgumentList @("manage.py", "runserver", "${DjangoHost}:${DjangoPort}") `
            -WorkingDirectory $webRoot `
            -DiagnosticLog $DiagnosticLog `
            -ServiceLabel "django" `
            -InvocationRole "django_runserver"
    }
    if ($code -ne 0) {
        Write-EasyImportsServiceFailureAndExit -Message "Django exited with code $code" -Code $code
    }
}
