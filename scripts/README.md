# Local launchers

Universal two-process start/stop from the mill, minus HubSpot Path C and
live CRM env discovery.

```text
FastAPI   127.0.0.1:8000   only when mappings_2/api/app.py is in this tree
Django    127.0.0.1:8001   always (web/)
```

This share package ships `web/` and FastAPI **thoughts** (`api/`). It does
not ship `python -m mappings_2.api.app`. On a mill checkout the API window
starts. Here the launcher starts Django only and says so.

## Commands

From the repository root:

```powershell
# HTTP (day-to-day)
powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1

# HTTPS loopback (OAuth callbacks that reject HTTP)
powershell -ExecutionPolicy Bypass -File scripts\start_local_https.ps1

# Free ports 8000/8001 and orphaned python/powershell service processes
powershell -ExecutionPolicy Bypass -File scripts\stop_local.ps1
```

Optional: `-EnvFile <absolute .env>`, `-SkipBrowser`, `-SkipMigrate`,
`-NoWait`, `-ApiPort`, `-DjangoPort`, `-PythonExe <absolute python.exe>`.

## What start does

1. Resolve **one** absolute Python for the whole stack: `-PythonExe`, else
   repo `.venv\Scripts\python.exe` if it imports the required modules, else
   PATH python only if it passes the same probe. Never a bare `python` for
   service children.
2. Optionally load `.env` (`-EnvFile`, else `EASYIMPORTS_ENV_FILE`,
   `<repo>/.env.local`, `<repo>/.env`). No Path C / HubSpot auto-paths.
   Never print tokens.
3. Default API/Django hosts, ports, and debug flags when env omits them.
4. If `mappings_2` is present: inspect API storage the same way the API
   process does and refuse poisoned bootstrap roots.
5. Fail if the ports are already accepting connections.
6. Open a new PowerShell window per service (`scripts/lib/run_service.ps1`).
7. Wait for `/health` (API, when started) and `/` (Django).
8. Open the browser to the Django origin.

HTTPS also generates per-machine TLS via `web/tools/ensure_loopback_tls.py`
(default `%LOCALAPPDATA%\EasyImports\tls\`). Never commit private keys.
Prefer `mkcert -install`. See `web/dev_tls/README.md`.

## Interpreter

Required imports here: `django`, plus `fastapi`/`uvicorn` when the API
package is present, plus `pandas` when `mappings_2/` is present.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
.\.venv\Scripts\python.exe -m pip install "Django>=5.0,<5.3" "requests>=2.31" "python-dotenv>=1.0"
```

Mill checkouts with `mappings_2/` should keep using `requirements-local.txt`.
See `DEPENDENCIES.md`.

## Shared library

| Path | Role |
|---|---|
| `scripts/lib/EasyImportsDev.ps1` | Env load, python resolve, ports, readiness, diagnostics (secret-redacted) |
| `scripts/lib/run_service.ps1` | Child-window API / Django runner |

Dot-source the lib; do not copy env-load or process-spawn into a new script.

## Stop

Closing a service window with **X** often orphans the Python child on
Windows. `stop_local.ps1` kills listeners on the service ports, then sweeps
`python.exe` / `powershell.exe` command lines that match this repo's
`mappings_2.api.app`, `manage.py runserver`, or `run_service.ps1`. Prefer
Ctrl+C in each window; use stop when you are not sure.

## Not copied

- `start_path_c.ps1` (live HubSpot)
- `restore_local_storage.ps1` (needs mill `local_install`)
- Benchmark scripts
