#!/usr/bin/env python3
"""Generate per-machine loopback TLS material for EasyImports HTTPS-2.

Writes cert/key under %%LOCALAPPDATA%%\\EasyImports\\tls (or
EASYIMPORTS_HTTPS_CERT_DIR). Never writes private keys into the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python web/tools/ensure_loopback_tls.py` from repo root.
_WEB = Path(__file__).resolve().parent.parent
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

from importer.https_loopback import (  # noqa: E402
    ensure_loopback_tls_material,
    resolve_tls_paths,
)


def main() -> int:
    cert, key = resolve_tls_paths()
    print(f"Target cert: {cert}")
    print(f"Target key:  {key}")
    try:
        cert, key = ensure_loopback_tls_material(cert_file=cert, key_file=key)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Ready: {cert}")
    print(f"Ready: {key}")
    print(
        "Prefer mkcert -install so the browser trusts the local CA. "
        "Do not commit these files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
