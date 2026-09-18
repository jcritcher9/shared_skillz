from __future__ import annotations

"""HTTPS-2 loopback TLS helpers for EasyImports Django.

Serves product UI + OAuth callback on ``https://127.0.0.1:8001`` without
``EASYIMPORTS_OAUTH_REDIRECT_URI`` override. Tunnel mode is documented separately
and is not a substitute for this loopback path.

TLS private keys are **per-machine** under the operator local data root (never
committed). Prefer mkcert; openssl is a fallback generator only.
"""

import os
import shutil
import subprocess
from pathlib import Path

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 8001
LOOPBACK_ORIGIN = f"https://{LOOPBACK_HOST}:{LOOPBACK_PORT}"
HTTPS_LOOPBACK_ENV = "EASYIMPORTS_HTTPS_LOOPBACK"

_WEB_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _WEB_ROOT.parent


def default_cert_dir() -> Path:
    """Per-machine TLS directory (not in the repository)."""

    override = (os.environ.get("EASYIMPORTS_HTTPS_CERT_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app:
        return Path(local_app) / "EasyImports" / "tls"
    return Path.home() / ".easyimports" / "tls"


DEFAULT_CERT_DIR = default_cert_dir()
DEFAULT_CERT_FILE = DEFAULT_CERT_DIR / "loopback.crt"
DEFAULT_KEY_FILE = DEFAULT_CERT_DIR / "loopback.key"


def https_loopback_enabled() -> bool:
    raw = os.environ.get(HTTPS_LOOPBACK_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def resolve_tls_paths(
    *,
    cert_file: str | Path | None = None,
    key_file: str | Path | None = None,
) -> tuple[Path, Path]:
    cert = Path(
        cert_file
        or os.environ.get("EASYIMPORTS_HTTPS_CERT_FILE")
        or default_cert_dir() / "loopback.crt"
    )
    key = Path(
        key_file
        or os.environ.get("EASYIMPORTS_HTTPS_KEY_FILE")
        or default_cert_dir() / "loopback.key"
    )
    return cert.expanduser().resolve(), key.expanduser().resolve()


def require_tls_material(
    *,
    cert_file: str | Path | None = None,
    key_file: str | Path | None = None,
    ensure: bool = True,
) -> tuple[Path, Path]:
    """Return existing cert/key paths; optionally generate per-machine material."""

    cert, key = resolve_tls_paths(cert_file=cert_file, key_file=key_file)
    if cert.is_file() and key.is_file():
        return cert, key
    if ensure:
        return ensure_loopback_tls_material(cert_file=cert, key_file=key)
    raise FileNotFoundError(
        f"Loopback TLS material not found:\n  cert={cert}\n  key={key}\n"
        "Run: python web/tools/ensure_loopback_tls.py\n"
        "Or: mkcert -cert-file <cert> -key-file <key> 127.0.0.1 localhost\n"
        "See web/dev_tls/README.md (docs only; no committed private keys)."
    )


def _path_is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` resolves under ``root`` (file need not exist yet)."""

    try:
        path.expanduser().resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def ensure_loopback_tls_material(
    *,
    cert_file: str | Path | None = None,
    key_file: str | Path | None = None,
) -> tuple[Path, Path]:
    """Create per-machine loopback cert+key if missing (mkcert preferred)."""

    cert, key = resolve_tls_paths(cert_file=cert_file, key_file=key_file)
    if cert.is_file() and key.is_file():
        return cert, key

    # Refuse if *either* path is inside the repository (split paths must not
    # smuggle a private key under the tree).
    repo_root = _REPO_ROOT.resolve()
    for label, path in (("certificate", cert), ("private key", key)):
        if _path_is_inside(path, repo_root):
            raise RuntimeError(
                f"Refusing to write TLS {label} inside the repository "
                f"({path}). Use a path under {default_cert_dir()} or set "
                "EASYIMPORTS_HTTPS_CERT_DIR / EASYIMPORTS_HTTPS_CERT_FILE + "
                "KEY_FILE."
            )

    cert.parent.mkdir(parents=True, exist_ok=True)
    mkcert = shutil.which("mkcert")
    if mkcert:
        # mkcert -install is operator-owned; generate leaf only.
        subprocess.run(
            [
                mkcert,
                "-cert-file",
                str(cert),
                "-key-file",
                str(key),
                LOOPBACK_HOST,
                "localhost",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return cert, key

    openssl = _find_openssl()
    if openssl:
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "825",
                "-subj",
                f"/CN={LOOPBACK_HOST}",
                "-addext",
                f"subjectAltName=IP:{LOOPBACK_HOST},DNS:localhost",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return cert, key

    raise FileNotFoundError(
        "Cannot generate loopback TLS material: neither mkcert nor openssl found. "
        "Install mkcert (recommended) or OpenSSL, then re-run "
        "python web/tools/ensure_loopback_tls.py. "
        "Do not commit private keys to the repository."
    )


def _find_openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
        Path(r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe"),
        Path.home() / "AppData/Local/Programs/Git/usr/bin/openssl.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def apply_https_loopback_runtime_settings() -> None:
    """Mutate Django settings for Secure + SameSite=Lax on HTTPS loopback."""

    from django.conf import settings

    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SESSION_COOKIE_SAMESITE = "Lax"
    settings.CSRF_COOKIE_SAMESITE = "Lax"

    origin = LOOPBACK_ORIGIN
    trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    if origin not in trusted:
        trusted.append(origin)
        settings.CSRF_TRUSTED_ORIGINS = trusted

    hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    if hosts != ["*"] and LOOPBACK_HOST not in hosts:
        hosts.append(LOOPBACK_HOST)
        settings.ALLOWED_HOSTS = hosts
