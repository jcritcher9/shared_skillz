# Dependencies

This file covers the two product folders in this package: `web/`
(Django operator UI) and `api/` (FastAPI HTTP-adapter thoughts). It is
not the mill's full pandas / notebook stack.

Install pins live next to this file. Friends implementing the FastAPI
thoughts should use `requirements-api.txt`. Friends running the Django
UI should use the **Web UI** set below; that UI still talks HTTP to an
API process that is not shipped as Python here.

## FastAPI adapter (`api/`)

Thoughts in `api/` assume FastAPI as the HTTP framework. Pins match the
mill's `requirements-api.txt`:

| Package | Pin | Why |
|---|---|---|
| fastapi | `==0.115.8` | Public HTTP adapter, OpenAPI generation |
| pydantic | `==2.10.6` | Request/response models, discriminated unions |
| uvicorn | `==0.34.0` | ASGI server (`--factory` against `create_default_app`) |
| httpx | `==0.27.2` | In-process / test client |
| python-multipart | `==0.0.20` | `POST /v1/uploads` multipart |

```text
python -m pip install -r requirements-api.txt
```

Implementing the thoughts also needs an application service, durable
stores, and a domain-blind workflow engine. Those packages are **not**
in this repo. FastAPI stays a thin transport over them.

Python 3.11+ is assumed (the mill runs 3.12/3.13).

## Web UI (`web/`)

Django operator UI. It does not import the API package; it is an HTTP
client of the FastAPI process.

| Package | Constraint | Why |
|---|---|---|
| Django | `>=5.0,<5.3` | Server-rendered operator UI |
| gunicorn | `>=26.0,<27.0` | WSGI server for deploy |
| whitenoise[brotli] | `>=6.12,<7.0` | Static files |
| dj-database-url | `>=3.0,<4.0` | `DATABASE_URL` |
| psycopg[binary] | `>=3.2,<4.0` | Postgres when not on SQLite |
| requests | `>=2.31` | HTTP client to the API (and payments) |
| pydantic | `==2.10.6` | Shared with the API pin |
| python-dotenv | `>=1.0` | Local env files outside the repo |
| openpyxl | `>=3.1` | XLSX handling in upload tests / samples |

Local default is SQLite (`web/db.sqlite3`, gitignored). State and media
paths for real use belong **outside** the git tree.

Optional for `web/importer/test_column_mapping_map_r4_a11y.py`:

- `playwright==1.49.1` then `python -m playwright install chromium`

`web/` tests in the mill also import `mappings_2`. That application
package is not in this share repo, so those tests are not a runnable
suite here.

## Two-process local shape

```text
FastAPI  127.0.0.1:8000   credentials + durable workflows
Django   127.0.0.1:8001   operator UI only
```

Launch from the repo root (see `scripts/README.md`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop_local.ps1
```

HTTPS loopback: `scripts\start_local_https.ps1` (uses
`web/tools/ensure_loopback_tls.py`). Without `mappings_2/api/app.py` the
launcher starts Django only.

CORS: localhost browser origins allowed on the API by default. Django
uses a finite mutation read timeout (mill default 180s) on API POSTs.

## Not in this package

- pandas / numpy / rapidfuzz pipeline
- Salesforce or HubSpot SDKs
- Shuttle
- Postgres in local fake-only mode (SQLite is enough)
