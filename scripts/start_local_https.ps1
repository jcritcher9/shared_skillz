#Requires -Version 5.1
<#
.SYNOPSIS
    Local launcher for EasyImports API + Django over HTTPS loopback (HTTPS-2).

.DESCRIPTION
    Same two-process stack as start_local.ps1, but Django listens on
    https://127.0.0.1:8001 via manage.py runserver_https (web/dev_tls certs).

    Required for Salesforce External Client App OAuth callbacks that reject HTTP.
    Does NOT flip the product built-in redirect default (that is HTTPS-3).

    Open the entire Connect UI on the HTTPS origin. Do not mix 127.0.0.1 HTTP
    with a tunnel callback.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start_local_https.ps1
#>
[CmdletBinding()]
param(
    [string]$EnvFile = "",
    [string]$RepoRoot = "",
    [switch]$SkipBrowser,
    [switch]$SkipMigrate,
    [switch]$NoWait,
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [string]$DjangoHost = "127.0.0.1",
    [int]$DjangoPort = 8001,
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
    Write-Host "EasyImports local HTTPS loopback launcher (HTTPS-2)" -ForegroundColor White
    Write-Host "==================================================="
    Write-Host "Repo: $RepoRoot"
    Write-Host ""

    $resolvedPython = Resolve-EasyImportsPythonExecutable `
        -RepoRoot $RepoRoot `
        -PythonExe $PythonExe
    $pythonVersion = Get-EasyImportsPythonVersion -PythonExe $resolvedPython
    Show-EasyImportsPythonSummary -PythonExe $resolvedPython -Version $pythonVersion
    Assert-EasyImportsPythonLocalDependencies -PythonExe $resolvedPython -RepoRoot $RepoRoot

    Write-EiInfo "Ensuring per-machine loopback TLS material (never committed)..."
    $tlsScript = Join-Path $RepoRoot "web\tools\ensure_loopback_tls.py"
    Write-EasyImportsPythonInvocationTrace `
        -PythonExe $resolvedPython `
        -ArgumentList @($tlsScript) `
        -Role "tls_generate"
    & $resolvedPython $tlsScript
    if ($LASTEXITCODE -ne 0) {
        throw @"
Failed to generate loopback TLS material.

Install mkcert (recommended) or OpenSSL, then:
  & '$resolvedPython' web/tools/ensure_loopback_tls.py

See web/dev_tls/README.md. Do not commit private keys.
"@
    }

    $resolvedEnv = Resolve-EasyImportsEnvFileOptional -EnvFile $EnvFile -RepoRoot $RepoRoot
    if ($resolvedEnv) {
        Import-EasyImportsEnvFile -Path $resolvedEnv | Out-Null
    } else {
        Write-EiInfo "No .env found; using process defaults (fake-preview / SQLite)"
    }

    if (-not $env:EASYIMPORTS_API_BASE_URL -or -not $env:EASYIMPORTS_API_BASE_URL.Trim()) {
        $env:EASYIMPORTS_API_BASE_URL = "http://${ApiHost}:${ApiPort}"
    }
    if (-not $env:EASYIMPORTS_API_HOST -or -not $env:EASYIMPORTS_API_HOST.Trim()) {
        $env:EASYIMPORTS_API_HOST = $ApiHost
    }
    if (-not $env:EASYIMPORTS_API_PORT -or -not $env:EASYIMPORTS_API_PORT.Trim()) {
        $env:EASYIMPORTS_API_PORT = "$ApiPort"
    }
    # Operator profile: never DEBUG, explicit loopback hosts, HTTPS cookies.
    $env:EASYIMPORTS_DEBUG = "0"
    if (-not $env:EASYIMPORTS_ALLOWED_HOSTS -or -not $env:EASYIMPORTS_ALLOWED_HOSTS.Trim()) {
        $env:EASYIMPORTS_ALLOWED_HOSTS = "localhost,127.0.0.1"
    }

    $env:EASYIMPORTS_HTTPS_LOOPBACK = "1"
    $httpsOrigin = "https://${DjangoHost}:${DjangoPort}"
    if (-not $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS -or -not $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS.Trim()) {
        $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS = $httpsOrigin
    }

    # Same storage truth + refuse path as start_local.ps1.
    $installReport = Get-EasyImportsOperatorInstallReport `
        -RepoRoot $RepoRoot `
        -PythonExe $resolvedPython
    Show-EasyImportsOperatorInstallSummary -Report $installReport
    Assert-EasyImportsOperatorInstallSafe -Report $installReport

    Write-Host ""
    Write-Host "Local HTTPS configuration summary" -ForegroundColor White
    Write-Host "---------------------------------"
    Write-Host ("PYTHON={0}" -f $resolvedPython)
    Write-Host ("PYTHON_VERSION={0}" -f $pythonVersion)
    Write-Host ("API_BASE={0}" -f $env:EASYIMPORTS_API_BASE_URL)
    Write-Host ("DJANGO_HTTPS={0}/" -f $httpsOrigin)
    Write-Host ("HTTPS_LOOPBACK={0}" -f $env:EASYIMPORTS_HTTPS_LOOPBACK)
    Write-Host ("CSRF_TRUSTED_ORIGINS={0}" -f $env:EASYIMPORTS_CSRF_TRUSTED_ORIGINS)
    $hasApi = Test-EasyImportsApiPackagePresent -RepoRoot $RepoRoot
    Write-Host ("API_PACKAGE={0}" -f $(if ($hasApi) { "mappings_2.api.app" } else { "<absent - Django-only>" }))
    Write-Host ("STATE={0}" -f $(if ($installReport.data_root) { $installReport.data_root } else { "<n/a>" }))
    Write-Host ("STATE_SOURCE={0}" -f $installReport.source)
    Write-Host ("BOOTSTRAP={0}" -f $(if ($installReport.bootstrap_path) { $installReport.bootstrap_path } else { "<n/a>" }))
    Write-Host ("TLS_DIR={0}" -f $(if ($env:EASYIMPORTS_HTTPS_CERT_DIR) { $env:EASYIMPORTS_HTTPS_CERT_DIR } else { "%LOCALAPPDATA%\EasyImports\tls" }))
    Write-Host ""
    Write-Host "OAuth: open the whole Connect UI on $httpsOrigin (single origin)."
    Write-Host "Product built-in callback (HTTPS-3): https://127.0.0.1:8001/crm/oauth/callback/"
    Write-Host "Prefer mkcert -install for browser trust of a local CA (not a shared leaf)."
    Write-Host ""

    Write-EiInfo "Collecting static files for the DEBUG-off operator profile..."
    $managePy = Join-Path $RepoRoot "web\manage.py"
    & $resolvedPython $managePy collectstatic --noinput
    if ($LASTEXITCODE -ne 0) {
        throw "collectstatic failed with exit code $LASTEXITCODE"
    }

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
        RepoRoot      = $RepoRoot
        ApiHost       = $ApiHost
        ApiPort       = $ApiPort
        DjangoHost    = $DjangoHost
        DjangoPort    = $DjangoPort
        HttpsLoopback = $true
        PythonExe     = $resolvedPython
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
    $djangoSvc = Start-EasyImportsServiceWindow @startArgs -Title "Django-HTTPS" -Service django -SkipMigrate:$SkipMigrate

    $apiUrl = "http://${ApiHost}:${ApiPort}/health"
    $djangoUrl = "${httpsOrigin}/"

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
        # TLS with self-signed cert: probe via resolved-python ssl client.
        [void](Wait-EasyImportsHttpsReady `
            -Url $djangoUrl `
            -TimeoutSeconds $ReadyTimeoutSeconds `
            -Label "Django-HTTPS" `
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
        Write-EiOk "Local HTTPS stack is ready"
    } else {
        Write-EiOk "Local HTTPS stack launch requested (readiness not verified; -NoWait)"
    }
    Write-Host "  Python:  $resolvedPython ($pythonVersion)"
    if ($hasApi) {
        Write-Host "  API:     http://${ApiHost}:${ApiPort}/health"
    } else {
        Write-Host "  API:     skipped (no mappings_2.api.app in this tree)"
    }
    Write-Host "  Django:  $djangoUrl"
    Write-Host "  Diagnostics: $diagDir"
    Write-Host "  Callback path: ${httpsOrigin}/crm/oauth/callback/"
    Write-Host ""
    Write-Host "Leave the service window(s) open. Close them (or Ctrl+C) to stop."
    Write-Host ""
    exit 0
} catch {
    Write-EiErr $_.Exception.Message
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    }
    exit 1
}
