#Requires -Version 5.1
<#
.SYNOPSIS
    Shared EasyImports local-development helpers (env load, validation, process start).

.DESCRIPTION
    Dot-source this file from repository launchers:

        . "$PSScriptRoot\lib\EasyImportsDev.ps1"

    Future entry points (start_fake.ps1, start_oauth.ps1, start_tests.ps1) should
    reuse these functions instead of redefining Load-EnvFile or process spawn logic.

    Never print secrets (access tokens, client secrets, service keys).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Capture this file's directory at load time. After dot-sourcing from another
# script, runtime $PSScriptRoot may point at the caller (e.g. scripts/), not lib/.
$script:EasyImportsLibDir = $PSScriptRoot

function Get-EasyImportsRepoRoot {
    <#
    .SYNOPSIS
        Resolve the repository root (parent of scripts/).
    #>
    param(
        [string]$StartPath = $PSScriptRoot
    )
    $here = (Resolve-Path -LiteralPath $StartPath).Path
    # scripts/lib -> scripts -> repo root
    if ((Split-Path -Leaf $here) -eq "lib") {
        $here = Split-Path -Parent $here
    }
    if ((Split-Path -Leaf $here) -eq "scripts") {
        return (Split-Path -Parent $here)
    }
    # Already at repo root or custom. Mill layout has mappings_2/;
    # this share package has web/ + scripts/ (and often agent_protocol/).
    if (
        (Test-Path -LiteralPath (Join-Path $here "mappings_2")) -or
        (
            (Test-Path -LiteralPath (Join-Path $here "web\manage.py")) -and
            (Test-Path -LiteralPath (Join-Path $here "scripts"))
        ) -or
        (Test-Path -LiteralPath (Join-Path $here "agent_protocol"))
    ) {
        return $here
    }
    throw "Could not resolve repository root from '$StartPath'."
}

function Test-EasyImportsApiPackagePresent {
    <#
    .SYNOPSIS
        True when this tree contains the FastAPI application module.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot
    )
    return (Test-Path -LiteralPath (Join-Path $RepoRoot "mappings_2\api\app.py"))
}

function Write-EiOk {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[OK] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-EiWarn {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-EiErr {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
}

function Write-EiInfo {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[..] " -ForegroundColor Cyan -NoNewline
    Write-Host $Message
}

function Resolve-EasyImportsEnvFile {
    <#
    .SYNOPSIS
        Locate a .env file for local development.

    .DESCRIPTION
        Search order:
          1. Explicit -EnvFile argument (if provided and non-empty)
          2. EASYIMPORTS_ENV_FILE process environment variable
          3. <repo>/.env.path-c
          4. <repo>/.env
          5. %USERPROFILE%\Documents\easyimports_hubspot_test\.env
             (optional Path C pilot convenience path)
    #>
    param(
        [string]$EnvFile,
        [string]$RepoRoot
    )

    # Explicit -EnvFile is fail-closed: do not silently fall back.
    if ($EnvFile -and $EnvFile.Trim()) {
        $explicit = $EnvFile.Trim()
        if (-not (Test-Path -LiteralPath $explicit)) {
            throw @"
Env file not found: $explicit

Pass a real path with -EnvFile, or omit -EnvFile to use discovery order:
  EASYIMPORTS_ENV_FILE, <repo>/.env.path-c, <repo>/.env,
  %USERPROFILE%\Documents\easyimports_hubspot_test\.env
"@
        }
        return (Resolve-Path -LiteralPath $explicit).Path
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:EASYIMPORTS_ENV_FILE -and $env:EASYIMPORTS_ENV_FILE.Trim()) {
        $candidates.Add($env:EASYIMPORTS_ENV_FILE.Trim())
    }
    if ($RepoRoot) {
        $candidates.Add((Join-Path $RepoRoot ".env.path-c"))
        $candidates.Add((Join-Path $RepoRoot ".env"))
    }
    $homeDocs = Join-Path $env:USERPROFILE "Documents\easyimports_hubspot_test\.env"
    $candidates.Add($homeDocs)

    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $tried = ($candidates | Where-Object { $_ } | Select-Object -Unique) -join "`n  - "
    throw @"
No .env file found.

Tried:
  - $tried

Fix: create a .env at one of those paths, or pass:
  -EnvFile <absolute-path-to-.env>

Example:
  powershell -ExecutionPolicy Bypass -File scripts\start_path_c.ps1 -EnvFile C:\Users\you\Documents\easyimports_hubspot_test\.env
"@
}

function Resolve-EasyImportsEnvFileOptional {
    <#
    .SYNOPSIS
        Locate an optional .env for generic local development, or return $null.

    .DESCRIPTION
        Deliberately narrower than Resolve-EasyImportsEnvFile (Path C):
          1. Explicit -EnvFile (fails closed if missing)
          2. EASYIMPORTS_ENV_FILE
          3. <repo>/.env.local
          4. <repo>/.env

        Does NOT auto-load Path C pilot paths (.env.path-c or
        Documents\easyimports_hubspot_test\.env). Use start_path_c.ps1 for those.
    #>
    param(
        [string]$EnvFile,
        [string]$RepoRoot
    )

    if ($EnvFile -and $EnvFile.Trim()) {
        $explicit = $EnvFile.Trim()
        if (-not (Test-Path -LiteralPath $explicit)) {
            throw @"
Env file not found: $explicit

Pass a real path with -EnvFile, or omit -EnvFile to run with process defaults
(and optional discovery of .env.local / .env if present).
"@
        }
        return (Resolve-Path -LiteralPath $explicit).Path
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:EASYIMPORTS_ENV_FILE -and $env:EASYIMPORTS_ENV_FILE.Trim()) {
        $candidates.Add($env:EASYIMPORTS_ENV_FILE.Trim())
    }
    if ($RepoRoot) {
        $candidates.Add((Join-Path $RepoRoot ".env.local"))
        $candidates.Add((Join-Path $RepoRoot ".env"))
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

function Import-EasyImportsEnvFile {
    <#
    .SYNOPSIS
        Load KEY=VALUE pairs from a .env file into the current process environment.

    .DESCRIPTION
        Supports blank lines and # comments. Values may be optionally single- or
        double-quoted. Does not expand variables inside values.
        Does not print secret values.
    #>
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file not found: $Path"
    }

    $loaded = 0
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }
        # Allow optional "export KEY=..."
        if ($line -match '^\s*export\s+') {
            $line = $line -replace '^\s*export\s+', ''
        }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        if (-not $name) { return }
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"') -and $value.Length -ge 2) -or
            ($value.StartsWith("'") -and $value.EndsWith("'") -and $value.Length -ge 2)
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "Env:$name" -Value $value
        $loaded++
    }

    Write-EiOk "Loaded environment from $Path ($loaded variables)"
    return $loaded
}

function Get-EasyImportsTokenLength {
    $token = @(
        $env:EASYIMPORTS_HUBSPOT_ACCESS_TOKEN,
        $env:EASYIMPORTS_HUBSPOT_SERVICE_KEY
    ) | Where-Object { $_ -and $_.Trim() } | Select-Object -First 1
    if (-not $token) { return 0 }
    return $token.Trim().Length
}

function Test-EasyImportsTokenPresent {
    return (Get-EasyImportsTokenLength) -gt 0
}

function Get-EasyImportsAuthModeSummary {
    <#
    .SYNOPSIS
        Canonical auth label for display - never echoes raw AUTH_MODE (PC-2 P0).
    #>
    $raw = if ($env:EASYIMPORTS_HUBSPOT_AUTH_MODE) {
        $env:EASYIMPORTS_HUBSPOT_AUTH_MODE.Trim().ToLowerInvariant()
    } else {
        ""
    }
    if ($raw) {
        if ($raw -in @("service_key", "private_app", "access_token", "pat")) {
            return "service_key"
        }
        if ($raw -in @("oauth", "oauth_code", "authorization_code")) {
            return "oauth"
        }
        return "invalid"
    }
    if (Test-EasyImportsTokenPresent) {
        $clientId = if ($env:EASYIMPORTS_HUBSPOT_CLIENT_ID) {
            $env:EASYIMPORTS_HUBSPOT_CLIENT_ID.Trim()
        } else {
            ""
        }
        if (-not $clientId) {
            return "service_key (implied)"
        }
    }
    return "oauth (default)"
}

function Show-EasyImportsPathCSummary {
    <#
    .SYNOPSIS
        Print a non-secret configuration summary for Path C live startup.
    #>
    $tokenLen = Get-EasyImportsTokenLength
    $tokenPresent = if ($tokenLen -gt 0) { "yes" } else { "no" }
    $auth = Get-EasyImportsAuthModeSummary

    Write-Host ""
    Write-Host "Path C configuration summary" -ForegroundColor White
    Write-Host "----------------------------"
    Write-Host ("MODE={0}" -f $(if ($env:EASYIMPORTS_CRM_HUBSPOT_MODE) { $env:EASYIMPORTS_CRM_HUBSPOT_MODE } else { "<unset>" }))
    Write-Host ("AUTH={0}" -f $auth)
    # PC1-G: never print hub/portal ids (live identifier redaction).
    Write-Host ("HUB_CONFIGURED={0}" -f $(if ($env:EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID -and $env:EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID.Trim()) { "yes" } else { "no" }))
    Write-Host ("TOKEN_PRESENT={0}" -f $tokenPresent)
    Write-Host ("TOKEN_LENGTH={0}" -f $tokenLen)
    Write-Host ("STATE={0}" -f $(if ($env:EASYIMPORTS_API_STATE_ROOT) { $env:EASYIMPORTS_API_STATE_ROOT } else { "<unset>" }))
    Write-Host ("DB={0}" -f $(if ($env:EASYIMPORTS_DB_PATH) { $env:EASYIMPORTS_DB_PATH } else { "<unset>" }))
    Write-Host ("MEDIA={0}" -f $(if ($env:EASYIMPORTS_MEDIA_ROOT) { $env:EASYIMPORTS_MEDIA_ROOT } else { "<unset>" }))
    Write-Host ("API_BASE={0}" -f $(if ($env:EASYIMPORTS_API_BASE_URL) { $env:EASYIMPORTS_API_BASE_URL } else { "http://127.0.0.1:8000" }))
    Write-Host ""
}

function Test-EasyImportsPathFullyQualified {
    <#
    .SYNOPSIS
        True when Candidate is a fully qualified absolute path.

        Rejects forms that [System.IO.Path]::IsPathRooted accepts on Windows
        but that still depend on the current drive or process state (PC1-E):
        - drive-relative: C:relative-state
        - root-relative:  \state or /state  (expand to <current-drive>\state)
        UNC (\\server\share) and drive-qualified (C:\state) remain valid.
    #>
    param(
        [Parameter(Mandatory)][string]$Candidate
    )
    $path = $Candidate.Trim()
    if ([string]::IsNullOrWhiteSpace($path)) {
        return $false
    }
    # Prefer IsPathFullyQualified when present (.NET Core / modern PS).
    try {
        return [System.IO.Path]::IsPathFullyQualified($path)
    } catch {
        # Method missing on older .NET Framework hosts (Windows PowerShell 5.1).
    }
    # Fallback for .NET Framework / PS 5.1:
    # 1) Must be rooted.
    if (-not [System.IO.Path]::IsPathRooted($path)) {
        return $false
    }
    # 2) Reject drive-relative: letter + colon without a following separator.
    if ($path -match '^[A-Za-z]:(?![\\/])') {
        return $false
    }
    # 3) Reject current-drive root-relative: single leading slash/backslash
    #    that is not UNC (\\server\share).
    if ($path -match '^[\\/](?![\\/])') {
        return $false
    }
    return $true
}

function Test-EasyImportsPathOutsideRepo {
    <#
    .SYNOPSIS
        Return error strings when Candidate is not fully qualified absolute
        or resolves under RepoRoot.
    #>
    param(
        [Parameter(Mandatory)][string]$Candidate,
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Label
    )
    $errors = New-Object System.Collections.Generic.List[string]
    $path = $Candidate.Trim()
    if (-not (Test-EasyImportsPathFullyQualified -Candidate $path)) {
        $errors.Add(
            "$Label must be a fully qualified absolute path (got '$path'). " +
            "Drive-relative (C:folder) and root-relative (\folder or /folder) " +
            "paths are not absolute."
        )
        # Unary comma prevents PowerShell from unrolling a single error string.
        return , $errors.ToArray()
    }
    try {
        $resolved = [System.IO.Path]::GetFullPath($path)
        $repo = [System.IO.Path]::GetFullPath($RepoRoot.TrimEnd('\', '/'))
    } catch {
        $errors.Add("$Label is not a resolvable path (got '$path').")
        return , $errors.ToArray()
    }
    $repoPrefix = $repo + [System.IO.Path]::DirectorySeparatorChar
    if (
        [string]::Equals($resolved, $repo, [StringComparison]::OrdinalIgnoreCase) -or
        $resolved.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        $errors.Add("$Label must resolve outside the repository (got '$resolved').")
    }
    return , $errors.ToArray()
}

function Assert-EasyImportsPathCConfig {
    <#
    .SYNOPSIS
        Fail closed if Path C live/service-key configuration is incomplete.
    #>
    param(
        [ValidateSet("service_key", "oauth", "any")]
        [string]$RequireAuth = "any",
        [string]$RepoRoot = ""
    )

    $errors = New-Object System.Collections.Generic.List[string]
    $repo = $RepoRoot
    if (-not $repo -or -not $repo.Trim()) {
        try {
            $repo = Get-EasyImportsRepoRoot
        } catch {
            $repo = ""
        }
    }

    if (-not $env:EASYIMPORTS_API_STATE_ROOT -or -not $env:EASYIMPORTS_API_STATE_ROOT.Trim()) {
        $errors.Add("Missing EASYIMPORTS_API_STATE_ROOT (absolute path outside the git repo).")
    } elseif ($repo) {
        foreach ($item in (Test-EasyImportsPathOutsideRepo -Candidate $env:EASYIMPORTS_API_STATE_ROOT -RepoRoot $repo -Label "EASYIMPORTS_API_STATE_ROOT")) {
            $errors.Add($item)
        }
    } else {
        $state = $env:EASYIMPORTS_API_STATE_ROOT.Trim()
        if (-not (Test-EasyImportsPathFullyQualified -Candidate $state)) {
            $errors.Add(
                "EASYIMPORTS_API_STATE_ROOT must be a fully qualified absolute path (got '$state')."
            )
        }
    }

    $mode = if ($env:EASYIMPORTS_CRM_HUBSPOT_MODE) { $env:EASYIMPORTS_CRM_HUBSPOT_MODE.Trim().ToLowerInvariant() } else { "" }
    if ($mode -ne "live") {
        $errors.Add("EASYIMPORTS_CRM_HUBSPOT_MODE must be 'live' for Path C (got '$mode').")
    }

    if (-not $env:EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID -or -not $env:EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID.Trim()) {
        $errors.Add("Missing EASYIMPORTS_HUBSPOT_EXPECTED_HUB_ID (HubSpot portal / hub id).")
    }

    $authExplicit = if ($env:EASYIMPORTS_HUBSPOT_AUTH_MODE) {
        $env:EASYIMPORTS_HUBSPOT_AUTH_MODE.Trim().ToLowerInvariant()
    } else {
        ""
    }
    $tokenPresent = Test-EasyImportsTokenPresent
    $clientId = if ($env:EASYIMPORTS_HUBSPOT_CLIENT_ID) { $env:EASYIMPORTS_HUBSPOT_CLIENT_ID.Trim() } else { "" }

    # Explicit mode must be a known alias; typos must not fall through to
    # implicit token/OAuth selection (PC-2).
    $effectiveAuth = ""
    if ($authExplicit) {
        if ($authExplicit -in @("service_key", "private_app", "access_token", "pat")) {
            $effectiveAuth = "service_key"
        } elseif ($authExplicit -in @("oauth", "oauth_code", "authorization_code")) {
            $effectiveAuth = "oauth"
        } else {
            # Do not echo the invalid value - it may be a pasted token (PC-2 P0).
            $errors.Add(
                "EASYIMPORTS_HUBSPOT_AUTH_MODE is set to an unsupported value. " +
                "Use service_key or oauth (aliases: private_app, access_token, pat, oauth_code, authorization_code)."
            )
            $effectiveAuth = "unknown"
        }
    } else {
        if ($tokenPresent -and -not $clientId) { $effectiveAuth = "service_key" }
        elseif ($clientId) { $effectiveAuth = "oauth" }
        else { $effectiveAuth = "unknown" }
    }

    if ($RequireAuth -eq "service_key" -or ($RequireAuth -eq "any" -and $effectiveAuth -eq "service_key")) {
        if (-not $tokenPresent) {
            $errors.Add(
                "Service-key auth requires EASYIMPORTS_HUBSPOT_ACCESS_TOKEN (or EASYIMPORTS_HUBSPOT_SERVICE_KEY)."
            )
        }
    }
    if ($RequireAuth -eq "oauth" -or ($RequireAuth -eq "any" -and $effectiveAuth -eq "oauth")) {
        if (-not $clientId) {
            $errors.Add("OAuth auth requires EASYIMPORTS_HUBSPOT_CLIENT_ID.")
        }
        if (-not $env:EASYIMPORTS_HUBSPOT_CLIENT_SECRET -or -not $env:EASYIMPORTS_HUBSPOT_CLIENT_SECRET.Trim()) {
            $errors.Add("OAuth auth requires EASYIMPORTS_HUBSPOT_CLIENT_SECRET.")
        }
        if (-not $env:EASYIMPORTS_HUBSPOT_REDIRECT_URI -or -not $env:EASYIMPORTS_HUBSPOT_REDIRECT_URI.Trim()) {
            $errors.Add("OAuth auth requires EASYIMPORTS_HUBSPOT_REDIRECT_URI.")
        }
    }
    if ($RequireAuth -eq "any" -and $effectiveAuth -eq "unknown") {
        $errors.Add(
            "Could not determine HubSpot auth mode. Set EASYIMPORTS_HUBSPOT_AUTH_MODE=service_key with a token, or oauth with client credentials."
        )
    }

    if (-not $env:EASYIMPORTS_DB_PATH -or -not $env:EASYIMPORTS_DB_PATH.Trim()) {
        $errors.Add("Missing EASYIMPORTS_DB_PATH (absolute path to django db.sqlite3 file).")
    } elseif ($repo) {
        foreach ($item in (Test-EasyImportsPathOutsideRepo -Candidate $env:EASYIMPORTS_DB_PATH -RepoRoot $repo -Label "EASYIMPORTS_DB_PATH")) {
            $errors.Add($item)
        }
    } else {
        $db = $env:EASYIMPORTS_DB_PATH.Trim()
        if (-not (Test-EasyImportsPathFullyQualified -Candidate $db)) {
            $errors.Add(
                "EASYIMPORTS_DB_PATH must be a fully qualified absolute path (got '$db')."
            )
        }
    }
    if (-not $env:EASYIMPORTS_MEDIA_ROOT -or -not $env:EASYIMPORTS_MEDIA_ROOT.Trim()) {
        $errors.Add("Missing EASYIMPORTS_MEDIA_ROOT (absolute directory for Django media).")
    } elseif ($repo) {
        foreach ($item in (Test-EasyImportsPathOutsideRepo -Candidate $env:EASYIMPORTS_MEDIA_ROOT -RepoRoot $repo -Label "EASYIMPORTS_MEDIA_ROOT")) {
            $errors.Add($item)
        }
    } else {
        $media = $env:EASYIMPORTS_MEDIA_ROOT.Trim()
        if (-not (Test-EasyImportsPathFullyQualified -Candidate $media)) {
            $errors.Add(
                "EASYIMPORTS_MEDIA_ROOT must be a fully qualified absolute path (got '$media')."
            )
        }
    }

    if ($errors.Count -gt 0) {
        Write-EiErr "Path C configuration is incomplete:"
        foreach ($item in $errors) {
            Write-Host "  - $item" -ForegroundColor Red
        }
        throw "Path C configuration validation failed. Fix the .env file and re-run."
    }

    Write-EiOk "Configuration validated (auth=$effectiveAuth)"
    return $effectiveAuth
}

function Ensure-EasyImportsPathCDirectories {
    <#
    .SYNOPSIS
        Create API state root, Django DB parent, and media root if missing.
    #>
    $created = New-Object System.Collections.Generic.List[string]

    $paths = @(
        $env:EASYIMPORTS_API_STATE_ROOT
        $env:EASYIMPORTS_MEDIA_ROOT
    )
    $dbPath = $env:EASYIMPORTS_DB_PATH
    if ($dbPath -and $dbPath.Trim()) {
        $dbParent = Split-Path -Parent $dbPath.Trim()
        if ($dbParent) { $paths += $dbParent }
    }

    foreach ($raw in $paths) {
        if (-not $raw) { continue }
        $path = $raw.Trim()
        if (-not $path) { continue }
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Force -Path $path | Out-Null
            $created.Add($path)
        }
    }

    if ($created.Count -gt 0) {
        Write-EiOk ("Created state directories:`n  - " + ($created -join "`n  - "))
    } else {
        Write-EiOk "State directories already present"
    }
}

function New-EasyImportsServiceDiagnosticDir {
    <#
    .SYNOPSIS
        Create a non-secret local diagnostic directory for one launch attempt (Phase 1B).
    #>
    param(
        [string]$LocalAppData = ""
    )
    $rootBase = if ($LocalAppData -and $LocalAppData.Trim()) {
        $LocalAppData.Trim()
    } elseif ($env:LOCALAPPDATA -and $env:LOCALAPPDATA.Trim()) {
        $env:LOCALAPPDATA.Trim()
    } else {
        [System.IO.Path]::GetTempPath()
    }
    $root = Join-Path $rootBase "EasyImports\diagnostics"
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $dir = Join-Path $root ("launch_" + $stamp + "_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    # Bound retention: keep newest 20 launch dirs under diagnostics root.
    try {
        $dirs = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "launch_*" } |
            Sort-Object LastWriteTimeUtc -Descending)
        if ($dirs.Count -gt 20) {
            $dirs | Select-Object -Skip 20 | ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
    }
    return $dir
}

# Per-service diagnostic file hard cap (UTF-8 bytes, exclusive of BOM). Content
# is a sanitized rolling tail so late startup failures remain visible.
$script:EasyImportsDiagnosticMaxBytes = 64 * 1024
$script:EasyImportsDiagnosticBoundMarker = "…(earlier diagnostic output omitted)…"

function Get-EasyImportsDiagnosticMaxBytes {
    <#
    .SYNOPSIS
        Hard size bound for a single service diagnostic log file (UTF-8 bytes).
    #>
    return [int]$script:EasyImportsDiagnosticMaxBytes
}

function Get-EasyImportsUtf8Encoding {
    return (New-Object System.Text.UTF8Encoding $false)
}

function Protect-EasyImportsDiagnosticText {
    <#
    .SYNOPSIS
        Redact common secret-shaped values from diagnostic text (never log secrets).
    #>
    param(
        [AllowNull()]
        [string]$Text
    )
    if ($null -eq $Text -or $Text.Length -eq 0) {
        return ""
    }
    $out = $Text
    $patterns = @(
        '(?i)(password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret|private[_-]?app[_-]?token|access[_-]?token|refresh[_-]?token|authorization)\s*[=:]\s*([^\s;,''"]+)',
        '(?i)(Bearer)\s+([A-Za-z0-9\-._~+/]+=*)',
        '(?i)(sk_live|sk_test|xox[baprs])-[A-Za-z0-9\-]+'
    )
    foreach ($pat in $patterns) {
        $out = [regex]::Replace($out, $pat, {
            param($m)
            if ($m.Groups.Count -ge 3 -and $m.Groups[2].Success) {
                return ($m.Groups[1].Value + "=<redacted>")
            }
            return "<redacted>"
        })
    }
    return $out
}

function Compact-EasyImportsDiagnosticPayload {
    <#
    .SYNOPSIS
        Fit sanitized diagnostic text into MaxBytes as a rolling tail (exact UTF-8 cap).

    .DESCRIPTION
        Keeps the newest content via UTF-8 byte-suffix slicing (no split multi-byte
        sequences). When older content is dropped, prefixes a single omission marker.
        Final traceback / exit_code lines remain because they are newest.
    #>
    param(
        [AllowNull()]
        [string]$Text,
        [Parameter(Mandatory)][int]$MaxBytes
    )
    $utf8 = Get-EasyImportsUtf8Encoding
    $body = if ($null -eq $Text) { "" } else { [string]$Text }
    if ($MaxBytes -le 0) {
        return ""
    }
    $bodyBytes = $utf8.GetBytes($body)
    if ($bodyBytes.Length -le $MaxBytes) {
        return $body
    }
    $marker = $script:EasyImportsDiagnosticBoundMarker + [Environment]::NewLine
    $markerBytes = $utf8.GetBytes($marker)
    if ($markerBytes.Length -ge $MaxBytes) {
        # Extreme: return as much of the marker as fits on a UTF-8 boundary.
        $take = $MaxBytes
        while ($take -gt 0 -and ($markerBytes[$take - 1] -band 0xC0) -eq 0x80) {
            $take--
        }
        if ($take -le 0) { return "" }
        return $utf8.GetString($markerBytes, 0, $take)
    }
    $budget = $MaxBytes - $markerBytes.Length
    # Take the last $budget bytes of body, aligned to a UTF-8 character start.
    $start = $bodyBytes.Length - $budget
    if ($start -lt 0) { $start = 0 }
    while ($start -lt $bodyBytes.Length -and ($bodyBytes[$start] -band 0xC0) -eq 0x80) {
        $start++
    }
    $suffixLen = $bodyBytes.Length - $start
    if ($suffixLen -lt 0) { $suffixLen = 0 }
    $suffix = if ($suffixLen -gt 0) { $utf8.GetString($bodyBytes, $start, $suffixLen) } else { "" }
    $payload = $marker + $suffix
    $payloadBytes = $utf8.GetBytes($payload)
    if ($payloadBytes.Length -le $MaxBytes) {
        return $payload
    }
    # Exact hard cap (should be rare after budget math).
    $start2 = $payloadBytes.Length - $MaxBytes
    if ($start2 -lt 0) { $start2 = 0 }
    while ($start2 -lt $payloadBytes.Length -and ($payloadBytes[$start2] -band 0xC0) -eq 0x80) {
        $start2++
    }
    $len2 = $payloadBytes.Length - $start2
    if ($len2 -le 0) { return "" }
    return $utf8.GetString($payloadBytes, $start2, $len2)
}

function Write-EasyImportsDiagnosticContent {
    <#
    .SYNOPSIS
        Persist diagnostic text only after secret redaction, as a rolling UTF-8 tail.

    .DESCRIPTION
        Phase 1B rem: on-disk files never store raw secrets. Each file is hard-capped
        at Get-EasyImportsDiagnosticMaxBytes() UTF-8 bytes by keeping a rolling tail
        so late startup failures (traceback, exit_code) remain visible. Returns the
        sanitized chunk for console echo.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [AllowNull()]
        [string]$Text,
        # When set, replace the file (header / reset) instead of appending.
        [switch]$Replace
    )
    $clean = Protect-EasyImportsDiagnosticText -Text $(if ($null -eq $Text) { "" } else { $Text })
    $maxBytes = Get-EasyImportsDiagnosticMaxBytes
    $utf8 = Get-EasyImportsUtf8Encoding
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $chunk = $clean
    if ($chunk.Length -gt 0 -and -not $chunk.EndsWith("`n") -and -not $chunk.EndsWith("`r`n")) {
        $chunk = $chunk + [Environment]::NewLine
    }

    if ($Replace) {
        $payload = Compact-EasyImportsDiagnosticPayload -Text $chunk -MaxBytes $maxBytes
        [System.IO.File]::WriteAllText($Path, $payload, $utf8)
        return $clean
    }

    $existing = ""
    if (Test-Path -LiteralPath $Path) {
        try {
            $existing = [System.IO.File]::ReadAllText($Path, $utf8)
        } catch {
            $existing = ""
        }
    }
    # Defense in depth: re-sanitize combined history before compacting.
    $combined = Protect-EasyImportsDiagnosticText -Text ($existing + $chunk)
    $payload = Compact-EasyImportsDiagnosticPayload -Text $combined -MaxBytes $maxBytes
    [System.IO.File]::WriteAllText($Path, $payload, $utf8)
    return $clean
}

function Get-EasyImportsDiagnosticTail {
    <#
    .SYNOPSIS
        Bounded diagnostic tail for parent launcher surfaces.

    .DESCRIPTION
        Files are sanitized on write; this still re-applies redaction as defense
        in depth and bounds the parent-visible character count.
    #>
    param(
        [string]$Path = "",
        [int]$MaxChars = 3000
    )
    if (-not $Path -or -not $Path.Trim() -or -not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    } catch {
        return ""
    }
    if (-not $raw) {
        return ""
    }
    $clean = Protect-EasyImportsDiagnosticText -Text $raw
    if ($clean.Length -gt $MaxChars) {
        $clean = "…(truncated)…" + $clean.Substring($clean.Length - $MaxChars)
    }
    return $clean.TrimEnd()
}

function Format-EasyImportsServiceStartupFailure {
    <#
    .SYNOPSIS
        Build an actionable parent-visible startup failure message (Phase 1B).
    #>
    param(
        [Parameter(Mandatory)][string]$Service,
        [string]$Reason = "exited before readiness",
        [Nullable[int]]$ExitCode = $null,
        [string]$PythonExe = "",
        [string]$DiagnosticLog = "",
        [string]$Url = ""
    )
    $tail = Get-EasyImportsDiagnosticTail -Path $DiagnosticLog
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add(("Service '{0}' {1}." -f $Service, $Reason))
    if ($null -ne $ExitCode) {
        $lines.Add(("exit_code={0}" -f $ExitCode))
    }
    if ($PythonExe -and $PythonExe.Trim()) {
        $lines.Add(("python_exe={0}" -f $PythonExe.Trim()))
    }
    if ($Url -and $Url.Trim()) {
        $lines.Add(("ready_url={0}" -f $Url.Trim()))
    }
    if ($DiagnosticLog -and $DiagnosticLog.Trim()) {
        $lines.Add(("diagnostic_log={0}" -f $DiagnosticLog.Trim()))
    }
    if ($tail) {
        $lines.Add("diagnostic_tail:")
        $lines.Add($tail)
    } else {
        $lines.Add("diagnostic_tail=<empty or unavailable>")
    }
    return ($lines -join [Environment]::NewLine)
}

function Invoke-EasyImportsServicePython {
    <#
    .SYNOPSIS
        Run a long-lived or one-shot service Python process with diagnostic capture.

    .DESCRIPTION
        Streams stdout/stderr to the host and a bounded diagnostic log file so the
        parent launcher can surface an actionable tail when the process exits
        before readiness. Avoids pipe-buffer deadlocks via async line reads.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [string]$WorkingDirectory = "",
        [string]$DiagnosticLog = "",
        [string]$ServiceLabel = "service",
        # Optional explicit invocation-trace role (api / django_migrate / django_runserver).
        [string]$InvocationRole = ""
    )
    $headerText = ""
    if ($DiagnosticLog -and $DiagnosticLog.Trim()) {
        $headerText = @(
            ("service={0}" -f $ServiceLabel)
            ("python_exe={0}" -f $PythonExe)
            ("argv={0}" -f (($ArgumentList -join " ") -replace '[\r\n]+', ' '))
            ("started_utc={0}" -f ([DateTime]::UtcNow.ToString("o")))
            "-----"
        ) -join [Environment]::NewLine
        [void](Write-EasyImportsDiagnosticContent -Path $DiagnosticLog -Text $headerText -Replace)
        Write-Host ("Service output is captured to: {0}" -f $DiagnosticLog)
    }

    if ($InvocationRole -and $InvocationRole.Trim()) {
        Write-EasyImportsPythonInvocationTrace `
            -PythonExe $PythonExe `
            -ArgumentList $ArgumentList `
            -Role $InvocationRole.Trim()
    } else {
        Write-EasyImportsPythonInvocationTrace -PythonExe $PythonExe -ArgumentList $ArgumentList
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = ConvertTo-EasyImportsWindowsCommandLine -ArgumentList $ArgumentList
    if ($WorkingDirectory -and $WorkingDirectory.Trim()) {
        $psi.WorkingDirectory = $WorkingDirectory
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    # Independent stdout/stderr raw buffers + tagged chunk queue.
    # Cross-stream interleaving must not splice stderr into the middle of a
    # stdout secret (or vice versa). Sanitize each stream as a whole on
    # persist/roll, then compose and hard-cap the combined diagnostic file.
    $logPath = $(if ($DiagnosticLog -and $DiagnosticLog.Trim()) { $DiagnosticLog } else { "" })
    $maxBytes = Get-EasyImportsDiagnosticMaxBytes
    $chunkQueue = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'
    $stdoutRaw = New-Object System.Text.StringBuilder
    $stderrRaw = New-Object System.Text.StringBuilder
    $headerBlock = ""
    if ($headerText) {
        $headerBlock = Protect-EasyImportsDiagnosticText -Text $headerText
        if (-not $headerBlock.EndsWith("`n") -and -not $headerBlock.EndsWith("`r`n")) {
            $headerBlock = $headerBlock + [Environment]::NewLine
        }
    }
    $utf8 = Get-EasyImportsUtf8Encoding
    # Hashtable so nested scriptblocks mutate shared suppress flags (PS closure-safe).
    $consoleState = @{
        OutAcc      = (New-Object System.Text.StringBuilder)
        ErrAcc      = (New-Object System.Text.StringBuilder)
        OutSuppress = $false
        ErrSuppress = $false
        MaxAcc      = 8192
    }

    $composeProtectedBody = {
        $outP = Protect-EasyImportsDiagnosticText -Text $stdoutRaw.ToString()
        $errP = Protect-EasyImportsDiagnosticText -Text $stderrRaw.ToString()
        $parts = New-Object System.Collections.Generic.List[string]
        if ($headerBlock) { [void]$parts.Add($headerBlock.TrimEnd() + [Environment]::NewLine) }
        [void]$parts.Add("[stdout]")
        if ($outP) { [void]$parts.Add($outP.TrimEnd()) }
        [void]$parts.Add("[stderr]")
        if ($errP) { [void]$parts.Add($errP.TrimEnd()) }
        return ($parts -join [Environment]::NewLine) + [Environment]::NewLine
    }
    $rollStreamsIfNeeded = {
        $total = $stdoutRaw.Length + $stderrRaw.Length
        if ($total -le ($maxBytes * 2)) {
            return
        }
        # Protect each stream with full per-stream context, then size-bound each.
        $outP = Protect-EasyImportsDiagnosticText -Text $stdoutRaw.ToString()
        $errP = Protect-EasyImportsDiagnosticText -Text $stderrRaw.ToString()
        $outBudget = [int][Math]::Floor($maxBytes * 0.65)
        $errBudget = [int][Math]::Max(1024, $maxBytes - $outBudget - 256)
        $outP = Compact-EasyImportsDiagnosticPayload -Text $outP -MaxBytes $outBudget
        $errP = Compact-EasyImportsDiagnosticPayload -Text $errP -MaxBytes $errBudget
        [void]$stdoutRaw.Clear()
        [void]$stderrRaw.Clear()
        if ($outP) { [void]$stdoutRaw.Append($outP) }
        if ($errP) { [void]$stderrRaw.Append($errP) }
    }
    $persistMem = {
        param([string]$Footer = "")
        if (-not $logPath) { return }
        $body = & $composeProtectedBody
        if ($Footer) {
            $body = $body + $Footer
            if (-not $Footer.EndsWith("`n")) {
                $body = $body + [Environment]::NewLine
            }
        }
        $payload = Compact-EasyImportsDiagnosticPayload -Text $body -MaxBytes $maxBytes
        [System.IO.File]::WriteAllText($logPath, $payload, $utf8)
    }
    $echoConsoleLine = {
        param([string]$LineText)
        $clean = Protect-EasyImportsDiagnosticText -Text $(if ($null -eq $LineText) { "" } else { $LineText })
        $capVar = Get-Variable -Name EasyImportsDiagnosticConsoleCapture -Scope Script -ErrorAction SilentlyContinue
        if ($null -ne $capVar -and $null -ne $capVar.Value) {
            try { [void]$capVar.Value.Add($clean) } catch { }
        }
        try {
            if ($clean.Length -gt 500) {
                Write-Host ($clean.Substring(0, 200) + "…(" + $clean.Length + " chars sanitized; see diagnostic log)…")
            } elseif ($clean.Length -gt 0) {
                Write-Host $clean
            }
        } catch { }
    }
    $echoConsoleNotice = {
        param([string]$Notice)
        $capVar = Get-Variable -Name EasyImportsDiagnosticConsoleCapture -Scope Script -ErrorAction SilentlyContinue
        if ($null -ne $capVar -and $null -ne $capVar.Value) {
            try { [void]$capVar.Value.Add($Notice) } catch { }
        }
        try { Write-Host $Notice } catch { }
    }
    $acceptStreamText = {
        param([string]$Stream, [string]$Text)
        if (-not $Text) { return }
        $rawBuilder = if ($Stream -eq "stdout") { $stdoutRaw } else { $stderrRaw }
        $consoleAcc = if ($Stream -eq "stdout") { $consoleState.OutAcc } else { $consoleState.ErrAcc }
        # Contiguous per-stream append (never mix stderr into stdout secret spans).
        [void]$rawBuilder.Append($Text)
        & $rollStreamsIfNeeded

        foreach ($ch in $Text.ToCharArray()) {
            $suppress = if ($Stream -eq "stdout") { [bool]$consoleState.OutSuppress } else { [bool]$consoleState.ErrSuppress }
            if ($ch -eq [char]10) {
                if ($suppress) {
                    & $echoConsoleNotice ("…(truncated {0} line; see diagnostic log)…" -f $Stream)
                    if ($Stream -eq "stdout") { $consoleState.OutSuppress = $false } else { $consoleState.ErrSuppress = $false }
                } else {
                    $line = $consoleAcc.ToString().TrimEnd([char]13)
                    & $echoConsoleLine $line
                }
                [void]$consoleAcc.Clear()
            } elseif ($suppress) {
                continue
            } else {
                [void]$consoleAcc.Append($ch)
                if ($consoleAcc.Length -ge [int]$consoleState.MaxAcc) {
                    # Bound newline-free console accumulation; never echo raw partials.
                    [void]$consoleAcc.Clear()
                    if ($Stream -eq "stdout") {
                        $consoleState.OutSuppress = $true
                    } else {
                        $consoleState.ErrSuppress = $true
                    }
                    & $echoConsoleNotice ("…(console {0} buffer capped at {1} chars; see diagnostic log)…" -f $Stream, $consoleState.MaxAcc)
                }
            }
        }
    }
    $drainChunks = {
        $piece = $null
        while ($chunkQueue.TryDequeue([ref]$piece)) {
            if (-not $piece -or $piece.Length -lt 2) { continue }
            $tag = $piece.Substring(0, 1)
            $text = $piece.Substring(1)
            if ($tag -eq "O") {
                & $acceptStreamText "stdout" $text
            } elseif ($tag -eq "E") {
                & $acceptStreamText "stderr" $text
            }
        }
    }
    $readerScript = {
        param($Reader, $Queue, $Tag)
        try {
            $buf = New-Object char[] 4096
            while ($true) {
                $n = $Reader.Read($buf, 0, $buf.Length)
                if ($n -le 0) { break }
                [void]$Queue.Enqueue($Tag + (New-Object string ($buf, 0, $n)))
            }
        } catch {
        }
    }

    $outPs = $null
    $errPs = $null
    try {
        [void]$proc.Start()
        $outPs = [powershell]::Create()
        [void]$outPs.AddScript($readerScript).AddArgument($proc.StandardOutput).AddArgument($chunkQueue).AddArgument("O")
        $errPs = [powershell]::Create()
        [void]$errPs.AddScript($readerScript).AddArgument($proc.StandardError).AddArgument($chunkQueue).AddArgument("E")
        $outHandle = $outPs.BeginInvoke()
        $errHandle = $errPs.BeginInvoke()

        $lastPersist = [DateTime]::UtcNow
        while (-not $proc.HasExited) {
            & $drainChunks
            if ($logPath -and (([DateTime]::UtcNow - $lastPersist).TotalMilliseconds -gt 500)) {
                & $persistMem
                $lastPersist = [DateTime]::UtcNow
            }
            Start-Sleep -Milliseconds 20
        }
        $deadline = (Get-Date).AddSeconds(5)
        while (((-not $outHandle.IsCompleted) -or (-not $errHandle.IsCompleted)) -and ((Get-Date) -lt $deadline)) {
            & $drainChunks
            Start-Sleep -Milliseconds 20
        }
        try { [void]$outPs.EndInvoke($outHandle) } catch { }
        try { [void]$errPs.EndInvoke($errHandle) } catch { }
        & $drainChunks
        # Incomplete console lines: protect whole per-stream accumulator or note suppress.
        if ($consoleState.OutSuppress) {
            & $echoConsoleNotice "…(truncated stdout line; see diagnostic log)…"
            $consoleState.OutSuppress = $false
        } elseif ($consoleState.OutAcc.Length -gt 0) {
            & $echoConsoleLine ($consoleState.OutAcc.ToString())
            [void]$consoleState.OutAcc.Clear()
        }
        if ($consoleState.ErrSuppress) {
            & $echoConsoleNotice "…(truncated stderr line; see diagnostic log)…"
            $consoleState.ErrSuppress = $false
        } elseif ($consoleState.ErrAcc.Length -gt 0) {
            & $echoConsoleLine ($consoleState.ErrAcc.ToString())
            [void]$consoleState.ErrAcc.Clear()
        }
        $code = $proc.ExitCode
        if ($logPath) {
            $footer = ("-----" + [Environment]::NewLine + "exit_code={0}" + [Environment]::NewLine) -f $code
            & $persistMem $footer
        }
        return $code
    } finally {
        if ($null -ne $outPs) { try { $outPs.Dispose() } catch { } }
        if ($null -ne $errPs) { try { $errPs.Dispose() } catch { } }
        try { if (-not $proc.HasExited) { $proc.Kill() } } catch { }
        try { $proc.Dispose() } catch { }
    }
}

function Start-EasyImportsServiceWindow {
    <#
    .SYNOPSIS
        Open a new PowerShell window that optionally loads .env and runs a service script.

    .OUTPUTS
        PSCustomObject with Process, DiagnosticLog, Service, PythonExe, Label (Phase 1B).
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        # Optional. When empty, the child runs with process defaults only.
        [string]$EnvFile = "",
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][ValidateSet("api", "django")][string]$Service,
        [string]$ApiHost = "127.0.0.1",
        [int]$ApiPort = 8000,
        [string]$DjangoHost = "127.0.0.1",
        [int]$DjangoPort = 8001,
        [switch]$SkipMigrate,
        [switch]$HttpsLoopback,
        # Parent-resolved absolute Python executable (required for deterministic services).
        [Parameter(Mandatory)][string]$PythonExe,
        # Optional diagnostic directory; when omitted a new launch dir is created.
        [string]$DiagnosticDir = ""
    )

    $runner = Join-Path $script:EasyImportsLibDir "run_service.ps1"
    if (-not (Test-Path -LiteralPath $runner)) {
        throw "Missing service runner: $runner"
    }

    if (-not $DiagnosticDir -or -not $DiagnosticDir.Trim()) {
        $DiagnosticDir = New-EasyImportsServiceDiagnosticDir
    }
    $diagnosticLog = Join-Path $DiagnosticDir ("{0}.log" -f $Service)

    # Phase 1B: do NOT use -NoExit. When Python exits, run_service exits and the
    # host PowerShell process exits so the parent readiness wait can observe it.
    # Do not ReadLine on failure (that made dead Python look alive to the parent).
    $argList = @(
        "-ExecutionPolicy", "Bypass"
        "-File", $runner
        "-RepoRoot", $RepoRoot
        "-Service", $Service
        "-ApiHost", $ApiHost
        "-ApiPort", $ApiPort
        "-DjangoHost", $DjangoHost
        "-DjangoPort", $DjangoPort
        "-PythonExe", $PythonExe
        "-DiagnosticLog", $diagnosticLog
    )
    if ($EnvFile -and $EnvFile.Trim()) {
        $argList += @("-EnvFile", $EnvFile.Trim())
    }
    if ($SkipMigrate) {
        $argList += "-SkipMigrate"
    }
    if ($HttpsLoopback) {
        $argList += "-HttpsLoopback"
    }

    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $argList `
        -WorkingDirectory $RepoRoot `
        -PassThru
    Write-EiOk "Starting $Title (new window, PID $($proc.Id)); diagnostic=$diagnosticLog"
    return [pscustomobject]@{
        Process       = $proc
        DiagnosticLog = $diagnosticLog
        DiagnosticDir = $DiagnosticDir
        Service       = $Service
        PythonExe     = $PythonExe
        Label         = $Title
    }
}

function Test-EasyImportsLocalPortListening {
    <#
    .SYNOPSIS
        True when something accepts TCP connections on Host:Port.
    #>
    param(
        [Parameter(Mandatory)][string]$HostName,
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutMs = 400
    )
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $client) {
            try { $client.Close() } catch { }
        }
    }
}

function Assert-EasyImportsPortsFree {
    <#
    .SYNOPSIS
        Fail closed when API/Django ports are already occupied (stale service).
    #>
    param(
        [string]$ApiHost = "127.0.0.1",
        [int]$ApiPort = 8000,
        [string]$DjangoHost = "127.0.0.1",
        [int]$DjangoPort = 8001
    )
    $targets = @(
        @{ Label = "API"; HostName = $ApiHost; Port = $ApiPort },
        @{ Label = "Django"; HostName = $DjangoHost; Port = $DjangoPort }
    )
    foreach ($t in $targets) {
        if (Test-EasyImportsLocalPortListening -HostName $t.HostName -Port $t.Port) {
            throw (
                "$($t.Label) address $($t.HostName):$($t.Port) is already accepting connections. " +
                "Stop the existing process on that port before launching " +
                "(a stale service must not satisfy readiness for a new launch)."
            )
        }
    }
}

function Wait-EasyImportsHttpReady {
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 45,
        [string]$Label = "service",
        [System.Diagnostics.Process]$Process = $null,
        [string]$PythonExe = "",
        [string]$DiagnosticLog = "",
        # When true (default), process-exit and timeout raise actionable errors (Phase 1B).
        [bool]$ThrowOnFailure = $true
    )

    Write-EiInfo "Waiting for $Label at $Url (up to ${TimeoutSeconds}s)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        if ($null -ne $Process) {
            try {
                $Process.Refresh()
                if ($Process.HasExited) {
                    $msg = Format-EasyImportsServiceStartupFailure `
                        -Service $Label `
                        -Reason "exited before readiness" `
                        -ExitCode $Process.ExitCode `
                        -PythonExe $PythonExe `
                        -DiagnosticLog $DiagnosticLog `
                        -Url $Url
                    Write-EiWarn $msg
                    if ($ThrowOnFailure) {
                        throw $msg
                    }
                    return $false
                }
            } catch {
                if ($ThrowOnFailure -and $_.Exception.Message -match 'exited before readiness|diagnostic_tail') {
                    throw
                }
                # Process object may be unavailable across sessions; continue HTTP probe.
            }
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            # 2xx only - 4xx (including 404) must not count as ready (PC-2).
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-EiOk "$Label is responding (HTTP $($response.StatusCode))"
                return $true
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            if ($ThrowOnFailure -and $_.Exception.Message -match 'exited before readiness|diagnostic_tail') {
                throw
            }
            $status = $null
            try {
                if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                    $status = [int]$_.Exception.Response.StatusCode
                }
            } catch {
                $status = $null
            }
            if ($null -ne $status -and $status -ge 200 -and $status -lt 300) {
                Write-EiOk "$Label is responding (HTTP $status)"
                return $true
            }
            if ($null -ne $status) {
                $lastError = "HTTP $status"
            } else {
                $lastError = $_.Exception.Message
            }
        }
        Start-Sleep -Milliseconds 750
    }
    $timeoutMsg = Format-EasyImportsServiceStartupFailure `
        -Service $Label `
        -Reason ("did not become ready within {0}s (last_error={1})" -f $TimeoutSeconds, $lastError) `
        -PythonExe $PythonExe `
        -DiagnosticLog $DiagnosticLog `
        -Url $Url
    Write-EiWarn $timeoutMsg
    if ($ThrowOnFailure) {
        throw $timeoutMsg
    }
    return $false
}

function Wait-EasyImportsHttpsReady {
    <#
    .SYNOPSIS
        Probe an HTTPS URL, accepting self-signed loopback certificates (HTTPS-2).
    #>
    param(
        [Parameter(Mandatory)][string]$Url,
        [int]$TimeoutSeconds = 45,
        [string]$Label = "service",
        [System.Diagnostics.Process]$Process = $null,
        # Parent-resolved absolute Python (never ambient bare python).
        [Parameter(Mandatory)][string]$PythonExe,
        [string]$DiagnosticLog = "",
        [bool]$ThrowOnFailure = $true
    )

    Write-EiInfo "Waiting for $Label at $Url (TLS, up to ${TimeoutSeconds}s)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    $py = @'
import ssl
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
ctx = ssl._create_unverified_context()
try:
    with urllib.request.urlopen(url, context=ctx, timeout=2) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        sys.exit(0 if 200 <= int(code) < 300 else 2)
except Exception as exc:
    sys.stderr.write(str(exc))
    sys.exit(1)
'@
    while ((Get-Date) -lt $deadline) {
        if ($null -ne $Process) {
            try {
                $Process.Refresh()
                if ($Process.HasExited) {
                    $msg = Format-EasyImportsServiceStartupFailure `
                        -Service $Label `
                        -Reason "exited before readiness" `
                        -ExitCode $Process.ExitCode `
                        -PythonExe $PythonExe `
                        -DiagnosticLog $DiagnosticLog `
                        -Url $Url
                    Write-EiWarn $msg
                    if ($ThrowOnFailure) {
                        throw $msg
                    }
                    return $false
                }
            } catch {
                if ($ThrowOnFailure -and $_.Exception.Message -match 'exited before readiness|diagnostic_tail') {
                    throw
                }
            }
        }
        try {
            $probe = Invoke-EasyImportsPythonCapture `
                -PythonExe $PythonExe `
                -ArgumentList @("-c", $py, $Url)
            if ($probe.ExitCode -eq 0) {
                Write-EiOk "$Label is responding (HTTPS 2xx)"
                return $true
            }
            $lastError = if ($probe.StdErr -and $probe.StdErr.Trim()) {
                $probe.StdErr.Trim()
            } else {
                "exit $($probe.ExitCode)"
            }
        } catch {
            if ($ThrowOnFailure -and $_.Exception.Message -match 'exited before readiness|diagnostic_tail') {
                throw
            }
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 750
    }
    $timeoutMsg = Format-EasyImportsServiceStartupFailure `
        -Service $Label `
        -Reason ("did not become ready within {0}s (last_error={1})" -f $TimeoutSeconds, $lastError) `
        -PythonExe $PythonExe `
        -DiagnosticLog $DiagnosticLog `
        -Url $Url
    Write-EiWarn $timeoutMsg
    if ($ThrowOnFailure) {
        throw $timeoutMsg
    }
    return $false
}

function Open-EasyImportsBrowser {
    param(
        [Parameter(Mandatory)][string]$Url
    )
    try {
        Start-Process $Url | Out-Null
        Write-EiOk "Opening browser: $Url"
    } catch {
        Write-EiWarn "Could not open browser automatically. Navigate to: $Url"
    }
}

function Get-EasyImportsLocalPythonModules {
    <#
    .SYNOPSIS
        Module names required for the local stack in this tree.

    .DESCRIPTION
        Django is always required (web/). FastAPI + uvicorn are required when
        launching the API window. pandas is required only when mappings_2 is
        present (mill application package).
    #>
    param(
        [string]$RepoRoot = "",
        [switch]$IncludeApi
    )
    $modules = New-Object System.Collections.Generic.List[string]
    [void]$modules.Add("django")
    $wantApi = $IncludeApi.IsPresent
    if (-not $wantApi -and $RepoRoot -and (Test-EasyImportsApiPackagePresent -RepoRoot $RepoRoot)) {
        $wantApi = $true
    }
    if ($wantApi) {
        [void]$modules.Add("fastapi")
        [void]$modules.Add("uvicorn")
    }
    if ($RepoRoot -and (Test-Path -LiteralPath (Join-Path $RepoRoot "mappings_2"))) {
        [void]$modules.Add("pandas")
    }
    return @($modules | Select-Object -Unique)
}

function ConvertTo-EasyImportsWindowsArgument {
    <#
    .SYNOPSIS
        Escape one argv element for ProcessStartInfo.Arguments (CommandLineToArgvW).

    .DESCRIPTION
        Matches the Windows rules used by CPython list2cmdline / MSVC: quote when
        empty or when space/tab/quote present; backslashes before a quote are
        doubled; backslashes immediately before the closing quote are doubled so
        a trailing \ inside a quoted arg is preserved (not treated as escaping ").
    #>
    param(
        [AllowNull()]
        [string]$Argument
    )
    if ($null -eq $Argument) {
        return '""'
    }
    $s = [string]$Argument
    $needsQuote = ($s.Length -eq 0) -or ($s.IndexOfAny(@([char]' ', [char]"`t", [char]'"')) -ge 0)
    if (-not $needsQuote) {
        return $s
    }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append([char]'"')
    $backslashCount = 0
    foreach ($ch in $s.ToCharArray()) {
        if ($ch -eq [char]'\') {
            $backslashCount++
            continue
        }
        if ($ch -eq [char]'"') {
            # Double backslashes, then escape the quote.
            if ($backslashCount -gt 0) {
                [void]$sb.Append(('\') * ($backslashCount * 2))
                $backslashCount = 0
            }
            [void]$sb.Append('\"')
            continue
        }
        if ($backslashCount -gt 0) {
            [void]$sb.Append(('\') * $backslashCount)
            $backslashCount = 0
        }
        [void]$sb.Append($ch)
    }
    # Trailing backslashes before the closing quote must be doubled.
    if ($backslashCount -gt 0) {
        [void]$sb.Append(('\') * ($backslashCount * 2))
    }
    [void]$sb.Append([char]'"')
    return $sb.ToString()
}

function ConvertTo-EasyImportsWindowsCommandLine {
    <#
    .SYNOPSIS
        Join argv into a single Windows command-line string for ProcessStartInfo.
    #>
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$ArgumentList
    )
    if ($null -eq $ArgumentList -or $ArgumentList.Count -eq 0) {
        return ""
    }
    $parts = foreach ($a in $ArgumentList) {
        ConvertTo-EasyImportsWindowsArgument -Argument $a
    }
    return ($parts -join " ")
}

function Get-EasyImportsPythonInvocationRole {
    <#
    .SYNOPSIS
        Classify a Python argv list for optional invocation tracing (tests).
    #>
    param(
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$ArgumentList
    )
    $joined = ($ArgumentList -join " ")
    if ($ArgumentList -contains "-c") {
        $idx = [array]::IndexOf($ArgumentList, "-c")
        $code = ""
        if ($idx -ge 0 -and $idx + 1 -lt $ArgumentList.Count) {
            $code = [string]$ArgumentList[$idx + 1]
        }
        if ($code -match 'import\s+(fastapi|uvicorn|django|pandas)\b') {
            return "dep_probe"
        }
        if ($code -match 'sys\.version') {
            return "version_probe"
        }
        if ($code -match 'urlopen|ssl\._create_unverified_context') {
            return "https_ready"
        }
        return "inline"
    }
    if ($joined -match 'operator_install_check') { return "storage_inspect" }
    if ($joined -match 'ensure_loopback_tls') { return "tls_generate" }
    if ($joined -match 'mappings_2\.api\.app' -or $joined -match '(^|\s)-m\s+mappings_2\.api(\s|$)') {
        return "api"
    }
    if ($joined -match '(^|\s)migrate(\s|$)') { return "django_migrate" }
    if ($joined -match 'runserver_https|runserver') { return "django_runserver" }
    return "other"
}

function Write-EasyImportsPythonInvocationTrace {
    <#
    .SYNOPSIS
        Append one invocation line when EASYIMPORTS_PYTHON_INVOCATION_LOG is set.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)]
        [AllowEmptyCollection()]
        [string[]]$ArgumentList,
        [string]$Role = ""
    )
    $log = $env:EASYIMPORTS_PYTHON_INVOCATION_LOG
    if (-not $log -or -not $log.Trim()) {
        return
    }
    if (-not $Role -or -not $Role.Trim()) {
        $Role = Get-EasyImportsPythonInvocationRole -ArgumentList $ArgumentList
    }
    $argvPreview = (($ArgumentList -join " ") -replace '[\r\n]+', ' ').Trim()
    if ($argvPreview.Length -gt 240) {
        $argvPreview = $argvPreview.Substring(0, 240)
    }
    $line = "{0}`t{1}`t{2}" -f $PythonExe, $Role, $argvPreview
    try {
        Add-Content -LiteralPath $log -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch {
        # Tracing must never break launches.
    }
}

function Invoke-EasyImportsPythonCapture {
    <#
    .SYNOPSIS
        Run a resolved Python executable with argv and capture exit/stdout/stderr.

    .DESCRIPTION
        Uses ProcessStartInfo so -c scripts are not re-tokenized by Start-Process
        ArgumentList joining (which breaks "import sys; ..." on Windows).
        Arguments are escaped with ConvertTo-EasyImportsWindowsArgument.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [string]$WorkingDirectory = ""
    )
    Write-EasyImportsPythonInvocationTrace -PythonExe $PythonExe -ArgumentList $ArgumentList
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = ConvertTo-EasyImportsWindowsCommandLine -ArgumentList $ArgumentList
    if ($WorkingDirectory -and $WorkingDirectory.Trim()) {
        $psi.WorkingDirectory = $WorkingDirectory
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    # Ensure child inherits current process environment (PYTHONPATH tracer, etc.).
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut   = $stdout
        StdErr   = $stderr
    }
}

function Test-EasyImportsPythonRunnable {
    <#
    .SYNOPSIS
        True when the path is an existing file that executes as Python.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe
    )
    if (-not $PythonExe -or -not $PythonExe.Trim()) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return $false
    }
    $item = Get-Item -LiteralPath $PythonExe -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer) {
        return $false
    }
    try {
        $result = Invoke-EasyImportsPythonCapture `
            -PythonExe $PythonExe `
            -ArgumentList @("-c", "import sys; print(sys.version.split()[0])")
        return ($result.ExitCode -eq 0 -and $result.StdOut -and $result.StdOut.Trim())
    } catch {
        return $false
    }
}

function Get-EasyImportsPythonVersion {
    <#
    .SYNOPSIS
        Short Python version string for a resolved executable (never secrets).
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe
    )
    $result = Invoke-EasyImportsPythonCapture `
        -PythonExe $PythonExe `
        -ArgumentList @("-c", "import sys; print(sys.version.split()[0])")
    if ($result.ExitCode -ne 0) {
        $detail = if ($result.StdErr -and $result.StdErr.Trim()) { $result.StdErr.Trim() } else { "no stderr" }
        throw "Python version probe failed for '$PythonExe' (exit $($result.ExitCode)): $detail"
    }
    $ver = $result.StdOut
    if (-not $ver -or -not $ver.Trim()) {
        throw "Python version probe produced no output for '$PythonExe'."
    }
    return $ver.Trim()
}

function Get-EasyImportsMissingPythonModules {
    <#
    .SYNOPSIS
        Return module names that fail import under the given Python executable.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [string[]]$Modules = @(),
        [string]$RepoRoot = ""
    )
    if (-not $Modules -or $Modules.Count -eq 0) {
        $Modules = Get-EasyImportsLocalPythonModules -RepoRoot $RepoRoot
    }
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($mod in $Modules) {
        try {
            $result = Invoke-EasyImportsPythonCapture `
                -PythonExe $PythonExe `
                -ArgumentList @("-c", "import $mod")
            if ($result.ExitCode -ne 0) {
                $missing.Add($mod)
            }
        } catch {
            $missing.Add($mod)
        }
    }
    return @($missing)
}

function Test-EasyImportsPythonHasLocalDependencies {
    <#
    .SYNOPSIS
        True when the executable imports the complete local stack modules.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [string[]]$Modules = @(),
        [string]$RepoRoot = ""
    )
    $missing = @(Get-EasyImportsMissingPythonModules -PythonExe $PythonExe -Modules $Modules -RepoRoot $RepoRoot)
    return ($missing.Count -eq 0)
}

function Assert-EasyImportsPythonLocalDependencies {
    <#
    .SYNOPSIS
        Fail closed when the resolved interpreter lacks local stack dependencies.
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [string[]]$Modules = @(),
        [string]$RepoRoot = ""
    )
    $missing = @(Get-EasyImportsMissingPythonModules -PythonExe $PythonExe -Modules $Modules -RepoRoot $RepoRoot)
    if ($missing.Count -eq 0) {
        return
    }
    throw @"
Missing Python package(s) in the resolved interpreter: $($missing -join ', ')

Interpreter: $PythonExe

From the repo root, install into this environment (see DEPENDENCIES.md):

  & '$PythonExe' -m pip install -r requirements-api.txt
  & '$PythonExe' -m pip install "Django>=5.0,<5.3" "requests>=2.31" "python-dotenv>=1.0"

If this tree also has mappings_2/, prefer the mill umbrella instead:

  & '$PythonExe' -m pip install -r requirements-local.txt

Then re-run the launcher (prefer the repository .venv).
"@
}

function Show-EasyImportsPythonSummary {
    <#
    .SYNOPSIS
        Print absolute interpreter path and version (never secrets).
    #>
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [string]$Version = ""
    )
    if (-not $Version -or -not $Version.Trim()) {
        $Version = Get-EasyImportsPythonVersion -PythonExe $PythonExe
    }
    Write-Host ""
    Write-Host "Python interpreter (resolved for this launch)" -ForegroundColor White
    Write-Host "---------------------------------------------"
    Write-Host ("python_exe={0}" -f $PythonExe)
    Write-Host ("python_version={0}" -f $Version)
    Write-Host ""
}

function Resolve-EasyImportsPythonExecutable {
    <#
    .SYNOPSIS
        Resolve one absolute Python executable for the local launcher surface.

    .DESCRIPTION
        Selection order:
          1. Explicit -PythonExe override (must be absolute, a file, and runnable).
          2. Repository .venv\Scripts\python.exe when present and healthy
             (complete local dependency probe).
          3. Active PATH interpreter only when it passes the same probe.

        Never returns a bare name like "python". Callers must pass the resolved
        absolute path into every child Python subprocess for that launch.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        # Optional absolute override (-PythonExe on launchers).
        [string]$PythonExe = "",
        # When true (default), require complete local stack imports.
        [bool]$RequireLocalDependencies = $true
    )

    if ($PythonExe -and $PythonExe.Trim()) {
        $candidate = $PythonExe.Trim()
        if (-not (Test-EasyImportsPathFullyQualified -Candidate $candidate)) {
            throw (
                "PythonExe must be an absolute fully-qualified path (got '$candidate'). " +
                "Example: -PythonExe C:\path\to\.venv\Scripts\python.exe"
            )
        }
        if (-not (Test-Path -LiteralPath $candidate)) {
            throw "PythonExe does not exist: $candidate"
        }
        $item = Get-Item -LiteralPath $candidate -ErrorAction Stop
        if ($item.PSIsContainer) {
            throw "PythonExe is a directory, not a file: $candidate"
        }
        if (-not (Test-EasyImportsPythonRunnable -PythonExe $candidate)) {
            throw (
                "PythonExe is not a runnable Python interpreter: $candidate " +
                "(version probe failed)."
            )
        }
        if ($RequireLocalDependencies) {
            Assert-EasyImportsPythonLocalDependencies -PythonExe $candidate -RepoRoot $RepoRoot
        }
        return $candidate
    }

    $venvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPy) {
        $venvItem = Get-Item -LiteralPath $venvPy -ErrorAction SilentlyContinue
        if ($null -ne $venvItem -and -not $venvItem.PSIsContainer) {
            if (Test-EasyImportsPythonRunnable -PythonExe $venvPy) {
                if (-not $RequireLocalDependencies -or (Test-EasyImportsPythonHasLocalDependencies -PythonExe $venvPy -RepoRoot $RepoRoot)) {
                    return $venvPy
                }
                $venvMissing = @(Get-EasyImportsMissingPythonModules -PythonExe $venvPy -RepoRoot $RepoRoot)
                Write-EiWarn (
                    "Repository .venv is present but missing packages: $($venvMissing -join ', '). " +
                    "Trying active interpreter only if it passes the full local probe."
                )
            } else {
                Write-EiWarn "Repository .venv python.exe exists but is not runnable: $venvPy"
            }
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $cmd -and $cmd.Source) {
        $ambient = $cmd.Source
        if (-not (Test-EasyImportsPathFullyQualified -Candidate $ambient)) {
            try {
                $ambient = (Resolve-Path -LiteralPath $ambient).Path
            } catch {
                throw "Active PATH python is not a fully-qualified path: $($cmd.Source)"
            }
        }
        if (Test-EasyImportsPythonRunnable -PythonExe $ambient) {
            if (-not $RequireLocalDependencies -or (Test-EasyImportsPythonHasLocalDependencies -PythonExe $ambient -RepoRoot $RepoRoot)) {
                return $ambient
            }
            $ambientMissing = @(Get-EasyImportsMissingPythonModules -PythonExe $ambient -RepoRoot $RepoRoot)
            throw @"
No healthy Python interpreter for the local stack.

Repository .venv: $venvPy
  (missing or incomplete — see DEPENDENCIES.md)

Active PATH python: $ambient
  Missing package(s): $($ambientMissing -join ', ')

Fix (recommended):
  & '$venvPy' -m pip install -r requirements-api.txt
  & '$venvPy' -m pip install "Django>=5.0,<5.3" "requests>=2.31"

Or pass an absolute healthy interpreter:
  -PythonExe C:\path\to\python.exe
"@
        }
    }

    throw (
        "Python executable not found. Create a repository .venv (see DEPENDENCIES.md), " +
        "or pass -PythonExe with an absolute path. Checked: $venvPy and PATH."
    )
}

function Get-EasyImportsOperatorInstallReport {
    <#
    .SYNOPSIS
        Resolve the API data root the same way create_default_app does (Phase 1A).

    .DESCRIPTION
        Invokes the parent-resolved Python -m mappings_2.application.local_install.operator_install_check
        so launcher STATE lines match resolve_local_install (bootstrap | default | env).
        Does not create directories or write machine bootstrap.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        # Parent-resolved absolute Python. When omitted, resolves once for this call.
        [string]$PythonExe = ""
    )
    if (-not $PythonExe -or -not $PythonExe.Trim()) {
        $PythonExe = Resolve-EasyImportsPythonExecutable -RepoRoot $RepoRoot
    }
    if (-not (Test-EasyImportsApiPackagePresent -RepoRoot $RepoRoot)) {
        return [pscustomobject]@{
            ok                   = $true
            skipped              = $true
            bootstrap_path       = ""
            data_root            = ""
            source               = "api_package_absent"
            bootstrap_exists     = $false
            data_root_exists     = $false
            is_default_data_root = $false
            error_code           = ""
            error_message        = ""
            restore_guidance     = ""
        }
    }
    $module = "mappings_2.application.local_install.operator_install_check"
    $result = Invoke-EasyImportsPythonCapture `
        -PythonExe $PythonExe `
        -ArgumentList @("-m", $module) `
        -WorkingDirectory $RepoRoot
    $stdout = $result.StdOut
    $stderr = $result.StdErr
    if (-not $stdout -or -not $stdout.Trim()) {
        $detail = if ($stderr -and $stderr.Trim()) { $stderr.Trim() } else { "no output" }
        throw "Operator install inspection produced no JSON (exit $($result.ExitCode)): $detail"
    }
    try {
        $report = $stdout | ConvertFrom-Json
    } catch {
        throw "Operator install inspection returned non-JSON output: $stdout"
    }
    return $report
}

function Show-EasyImportsOperatorInstallSummary {
    <#
    .SYNOPSIS
        Print resolved bootstrap_path / data_root / source (Phase 1A truth).
    #>
    param(
        [Parameter(Mandatory)]$Report
    )
    $skipped = $false
    try { $skipped = [bool]$Report.skipped } catch { $skipped = $false }
    if ($skipped) {
        Write-EiWarn "FastAPI application (mappings_2.api.app) is not in this tree; skipping API storage inspect."
        Write-EiInfo "Django-only launch. Start the mill API separately if the UI needs /health on :8000."
        return
    }
    Write-Host ""
    Write-Host "API storage (resolved like the API process)" -ForegroundColor White
    Write-Host "------------------------------------------"
    Write-Host ("bootstrap_path={0}" -f $Report.bootstrap_path)
    Write-Host ("data_root={0}" -f $Report.data_root)
    Write-Host ("source={0}" -f $Report.source)
    Write-Host ("bootstrap_exists={0}" -f $Report.bootstrap_exists)
    Write-Host ("data_root_exists={0}" -f $Report.data_root_exists)
    Write-Host ("is_default_data_root={0}" -f $Report.is_default_data_root)
    Write-Host ""
}

function Assert-EasyImportsOperatorInstallSafe {
    <#
    .SYNOPSIS
        Fail closed when machine bootstrap/env points at a poisoned or missing
        non-default operator data root (Phase 1A). First-run (no bootstrap) is OK.
    #>
    param(
        [Parameter(Mandatory)]$Report
    )
    $ok = $false
    try {
        $ok = [bool]$Report.ok
    } catch {
        $ok = $false
    }
    if ($ok) {
        Write-EiOk (
            "Operator data root is safe for launch (source={0})" -f $Report.source
        )
        return
    }
    $code = if ($Report.error_code) { [string]$Report.error_code } else { "unsafe_data_root" }
    $message = if ($Report.error_message) { [string]$Report.error_message } else {
        "Operator data root is not safe for local launch."
    }
    Write-EiErr "Refusing to start: $message"
    Write-Host ("  error_code={0}" -f $code) -ForegroundColor Red
    if ($Report.bootstrap_path) {
        Write-Host ("  bootstrap_path={0}" -f $Report.bootstrap_path) -ForegroundColor Red
    }
    if ($Report.data_root) {
        Write-Host ("  data_root={0}" -f $Report.data_root) -ForegroundColor Red
    }
    if ($Report.source) {
        Write-Host ("  source={0}" -f $Report.source) -ForegroundColor Red
    }
    if ($Report.restore_guidance) {
        Write-Host ""
        Write-Host "Restore guidance:" -ForegroundColor Yellow
        Write-Host ([string]$Report.restore_guidance)
    }
    throw (
        "Local launch refused: unsafe API data root ($code). " +
        "Fix machine bootstrap / EASYIMPORTS_API_STATE_ROOT before registering CRM."
    )
}

# Export-style note for discoverability when dot-sourced
$script:EasyImportsDevLoaded = $true
