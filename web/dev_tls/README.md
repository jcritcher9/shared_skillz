# Loopback TLS (HTTPS-2) — docs only

**No private keys are stored in this directory or the repository.**

EasyImports generates **per-machine** loopback TLS material for:

```text
https://127.0.0.1:8001/
```

## Default locations

| Platform | Directory |
|---|---|
| Windows | `%LOCALAPPDATA%\EasyImports\tls\` |
| Other | `~/.easyimports/tls/` |

Files: `loopback.crt`, `loopback.key`.

Override with:

- `EASYIMPORTS_HTTPS_CERT_DIR`
- or `EASYIMPORTS_HTTPS_CERT_FILE` + `EASYIMPORTS_HTTPS_KEY_FILE`

## Generate

Recommended (**mkcert** — local CA, browser trust without importing a shared leaf):

```powershell
mkcert -install
$dir = "$env:LOCALAPPDATA\EasyImports\tls"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
mkcert -cert-file "$dir\loopback.crt" -key-file "$dir\loopback.key" 127.0.0.1 localhost
```

Or the product helper (mkcert if present, else openssl; writes only outside the repo):

```powershell
python web/tools/ensure_loopback_tls.py
```

## Run HTTPS Django

```powershell
$env:EASYIMPORTS_HTTPS_LOOPBACK = "1"
python web/manage.py runserver_https 127.0.0.1:8001
# or
powershell -ExecutionPolicy Bypass -File scripts\start_local_https.ps1
```

After readiness, open `https://127.0.0.1:8001/` and confirm a **reload / second
tab** also loads. TLS handshakes run **per connection** off the accept loop
(HSR-1); a stalled speculative connection must not wedge the browser
(`ERR_TIMED_OUT`). See
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/https_loopback_tls_handshake_reliability/https_loopback_tls_handshake_reliability.md`.

## Trust policy

- **Do not** commit `loopback.key` or any private key.
- **Do not** import a repository-shared leaf certificate into a trust store.
- Trust **your machine’s mkcert CA** (or OS trust of a key **you** generated locally).

Self-signed openssl fallbacks may require a browser exception for localhost only;
prefer mkcert for a clean trust path.
