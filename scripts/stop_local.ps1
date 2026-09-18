#Requires -Version 5.1
<#
.SYNOPSIS
    Stop the EasyImports local stack: free its service ports AND sweep for
    orphaned API/Django processes that are no longer holding those ports.

.DESCRIPTION
    The start_local.ps1 / start_local_https.ps1 launchers open the API and
    Django servers in separate windows. Closing those windows with the X button
    does NOT reliably terminate the Python child process on Windows — it is
    orphaned and keeps holding its port, so the next launch fails the port
    pre-flight check ("address ... is already accepting connections").

    Killing only whatever is CURRENTLY bound to the listening socket is not
    enough: Django's runserver / runserver_https autoreloader forks a parent
    watcher process plus a child worker process, and a repeated start/stop
    cycle (or a window closed with X, or a launch retried before the previous
    one fully released its port) can leave one or more of these behind even
    after the port itself looks free. An orphaned API or Django process left
    running like this can keep holding the same local SQLite/JSON file locks
    the new process needs, which looks like the app "hanging" on the next
    launch even though the port pre-flight check passed.

    This script therefore does two passes:
      1. Kill whatever is currently listening on -Ports (original behavior).
      2. Sweep ALL python.exe / powershell.exe processes on the machine for
         command lines that match this repo's known EasyImports service
         invocations (API, Django runserver/runserver_https, and the
         run_service.ps1 window host script) and kill any that are still
         alive, whether or not they hold a listening port right now.

    It is safe to run when nothing is listening and nothing is orphaned.

    Prefer Ctrl+C in each service window for a clean shutdown; use this when
    windows were closed with X, a previous stop left orphans, or you are not
    sure of the current state.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\stop_local.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\stop_local.ps1 -Ports 8000,8001,8443
#>
[CmdletBinding()]
param(
    # Local service ports to free. Defaults to the launcher API + Django ports.
    [int[]]$Ports = @(8000, 8001),

    # Repo root used to scope the orphan sweep. Defaults to the parent of
    # scripts\ (this file's own location), matching the other launchers.
    [string]$RepoRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $RepoRoot -or -not $RepoRoot.Trim()) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
} else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}

Write-Host ""
Write-Host "EasyImports local stop" -ForegroundColor White
Write-Host "======================"
Write-Host ("Repo:  {0}" -f $RepoRoot)
Write-Host ("Ports: {0}" -f ($Ports -join ", "))
Write-Host ""

$stoppedIds = New-Object System.Collections.Generic.HashSet[int]

function Stop-EasyImportsProcessById {
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][string]$Reason
    )
    if ($stoppedIds.Contains($ProcessId)) {
        return $true
    }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { "unknown" }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-Host ("[OK] Stopped PID {0} ({1}) - {2}" -f $ProcessId, $name, $Reason) -ForegroundColor Green
        [void]$stoppedIds.Add($ProcessId)
        return $true
    } catch {
        Write-Host ("[WARN] Could not stop PID {0} ({1}) - {2}: {3}" -f $ProcessId, $name, $Reason, $_.Exception.Message) -ForegroundColor Yellow
        return $false
    }
}

# --- Pass 1: whatever currently holds the listening socket -----------------

$conns = Get-NetTCPConnection -LocalPort $Ports -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    foreach ($processId in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        [void](Stop-EasyImportsProcessById -ProcessId $processId -Reason "listening on tracked port")
    }
} else {
    Write-Host ("[OK] No listeners on {0}." -f ($Ports -join ", ")) -ForegroundColor Green
}

# --- Pass 2: orphan sweep by command line, independent of port state -------
#
# Matches this repo's known EasyImports service invocations:
#   - API:     "-m mappings_2.api.app"
#   - Django:  "manage.py runserver" (also matches "runserver_https")
#   - Window host: "run_service.ps1" (spawned by Start-EasyImportsServiceWindow)
# scoped to processes whose command line also references this repo root, so a
# same-named process from an unrelated project/repo is left alone.

$patterns = @(
    "mappings_2.api.app",
    "manage.py runserver",
    "run_service.ps1"
)

# @(...) forces array context: a bare pipeline result unwraps to a scalar
# object (no .Count) when exactly one process matches, which throws under
# Set-StrictMode -Version Latest wherever .Count is later checked.
$candidates = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        if ($cmd -notlike "*$RepoRoot*") { return $false }
        foreach ($pattern in $patterns) {
            if ($cmd -like "*$pattern*") { return $true }
        }
        return $false
    })

$orphansFound = 0
foreach ($candidate in $candidates) {
    if ($stoppedIds.Contains([int]$candidate.ProcessId)) { continue }
    $orphansFound++
    [void](Stop-EasyImportsProcessById -ProcessId ([int]$candidate.ProcessId) -Reason "orphaned EasyImports process (command-line sweep)")
}
if ($orphansFound -eq 0) {
    Write-Host "[OK] No orphaned EasyImports processes found (command-line sweep)." -ForegroundColor Green
}

Start-Sleep -Milliseconds 600

# --- Verify: ports free AND no matching process remains ---------------------

$leftoverPorts = New-Object System.Collections.Generic.List[int]
foreach ($port in $Ports) {
    $still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        $leftoverPorts.Add($port)
        Write-Host ("[WARN] Port {0} is still held by PID {1}" -f $port, $still.OwningProcess) -ForegroundColor Yellow
    }
}

# @(...): see Pass 2 note above — a single matching process would otherwise
# unwrap to a scalar CimInstance and fail the .Count checks below.
$stillRunning = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        if ($cmd -notlike "*$RepoRoot*") { return $false }
        foreach ($pattern in $patterns) {
            if ($cmd -like "*$pattern*") { return $true }
        }
        return $false
    })
foreach ($proc in $stillRunning) {
    Write-Host ("[WARN] PID {0} still matches an EasyImports service pattern" -f $proc.ProcessId) -ForegroundColor Yellow
}

Write-Host ""
if ($leftoverPorts.Count -gt 0 -or $stillRunning.Count -gt 0) {
    if ($leftoverPorts.Count -gt 0) {
        Write-Host ("[ERROR] Some ports are still in use: {0}" -f ($leftoverPorts -join ", ")) -ForegroundColor Red
    }
    if ($stillRunning.Count -gt 0) {
        Write-Host ("[ERROR] Some EasyImports processes are still running: {0}" -f (($stillRunning | Select-Object -ExpandProperty ProcessId) -join ", ")) -ForegroundColor Red
    }
    exit 1
}

Write-Host ("[OK] Freed {0} process(es); ports {1} are clear; no orphans remain." -f $stoppedIds.Count, ($Ports -join ", ")) -ForegroundColor Green
Write-Host "You can relaunch now:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start_local_https.ps1"
Write-Host ""
exit 0
