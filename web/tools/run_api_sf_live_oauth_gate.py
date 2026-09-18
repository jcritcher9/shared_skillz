"""API process launcher for HSR-L real External Client App OAuth.

Starts create_app against EASYIMPORTS_API_STATE_ROOT with:

* live Salesforce composition (not synthetic OAuth stack)
* **real** authorization-code exchange (``live_salesforce_code_exchange``)
* authorization_url **not** suppressed — Connect must redirect the browser to
  Salesforce authorize and return via
  ``https://127.0.0.1:8001/crm/oauth/callback/``

Unlike ``run_api_sf_live_refresh_gate.py`` (Phase 7C pilot), this launcher does
**not** complete Connect from a pre-existing refresh token.

Env:
  EASYIMPORTS_API_PORT (default 8000)
  EASYIMPORTS_API_STATE_ROOT
  optional: EASYIMPORTS_SF_ENV_FILE (loaded for operator convenience; Connect
  registration still supplies client id/secret from the UI)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    env_file = (os.environ.get("EASYIMPORTS_SF_ENV_FILE") or "").strip()
    if env_file:
        _load_env_file(Path(env_file))

    port = int(os.environ.get("EASYIMPORTS_API_PORT") or "8000")
    state_root = Path(
        os.environ.get("EASYIMPORTS_API_STATE_ROOT")
        or (Path.cwd() / ".easyimports_v1")
    ).resolve()
    state_root.mkdir(parents=True, exist_ok=True)

    # Never force synthetic OAuth — that builds offline SF stacks.
    os.environ.pop("EASYIMPORTS_SF_OAUTH_EXCHANGE", None)

    from mappings_2.api.app import create_app

    app = create_app(state_root=state_root)
    install = app.state.local_install
    install.use_synthetic_oauth = False
    # Do not wrap build_salesforce_connection_service — real authorize URL +
    # live_salesforce_code_exchange must remain in effect for HSR-L.

    import uvicorn

    host = os.environ.get("EASYIMPORTS_API_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
