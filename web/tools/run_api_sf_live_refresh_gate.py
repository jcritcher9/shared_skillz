"""API process launcher for storage E2E Phase 7C (SF Account live FE gate).

Starts create_app against EASYIMPORTS_API_STATE_ROOT with:

* live Salesforce composition (not synthetic OAuth stack)
* refresh-token code exchange (from SF_REFRESH_TOKEN) so Connect can complete
  without a browser OAuth dance (same posture as CRM-dupe Phase 6A live gate)
* authorization_url suppressed so Django completes with a one-time opaque code

Env (required for live exchange):
  EASYIMPORTS_API_PORT
  EASYIMPORTS_API_STATE_ROOT
  SF_CLIENT_ID / SF_CLIENT_SECRET / SF_REFRESH_TOKEN
  optional: SF_LOGIN_URL, SF_API_VERSION, SF_REDIRECT_URI

Does not write the developer machine bootstrap when LOCALAPPDATA is isolated.
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
    from mappings_2.integrations.salesforce.connection.auth import (
        SalesforceOAuthConfig,
        refresh_access_token,
    )
    from mappings_2.integrations.salesforce.connection.service import (
        SalesforceAuthorizationExchange,
    )
    from mappings_2.integrations.salesforce.reference.client import (
        SalesforceRestClient,
    )

    client_id = (os.environ.get("SF_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("SF_CLIENT_SECRET") or "").strip()
    refresh_token = (os.environ.get("SF_REFRESH_TOKEN") or "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise SystemExit(
            "run_api_sf_live_refresh_gate requires SF_CLIENT_ID, "
            "SF_CLIENT_SECRET, and SF_REFRESH_TOKEN in the environment."
        )

    redirect_uri = (
        (os.environ.get("SF_REDIRECT_URI") or "").strip()
        or "https://127.0.0.1:8001/crm/oauth/callback/"
    )
    login_url = (
        (os.environ.get("SF_LOGIN_URL") or "").strip()
        or "https://login.salesforce.com"
    )
    api_version = (os.environ.get("SF_API_VERSION") or "").strip() or "v61.0"

    oauth = SalesforceOAuthConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        login_url=login_url,
        api_version=api_version,
    )
    boot = refresh_access_token(oauth, refresh_token)
    boot_client = SalesforceRestClient(token=boot, api_version=api_version)
    org_rows = boot_client.query_all(
        "SELECT Id, IsSandbox, OrganizationType FROM Organization LIMIT 1"
    )
    if len(org_rows) != 1:
        raise SystemExit("Organization verification failed at API launch.")
    org_id = str(org_rows[0]["Id"])
    org_type = str(org_rows[0].get("OrganizationType") or "")
    is_sandbox = bool(org_rows[0].get("IsSandbox"))
    if not (is_sandbox or org_type in {"Developer Edition", "Sandbox"}):
        raise SystemExit(
            f"API launch refused production org type={org_type!r} "
            f"IsSandbox={is_sandbox}."
        )

    # Expose sanitized launch metadata for the dual-process test (no secrets).
    os.environ["EASYIMPORTS_PHASE7C_LAUNCH_ORG_TYPE"] = org_type
    os.environ["EASYIMPORTS_PHASE7C_LAUNCH_IS_SANDBOX"] = (
        "1" if is_sandbox else "0"
    )
    os.environ["EASYIMPORTS_PHASE7C_LAUNCH_ORG_ID"] = org_id

    app = create_app(state_root=state_root)
    install = app.state.local_install
    install.use_synthetic_oauth = False

    def _refresh_code_exchange(authorization_code: str, context: object):
        del context
        if not str(authorization_code or "").strip():
            raise RuntimeError("authorization_code must be non-empty.")
        refreshed = refresh_access_token(oauth, refresh_token)
        return SalesforceAuthorizationExchange(
            organization_id=org_id,
            display_label=f"7C-SF Live ({org_type or 'org'})",
            instance_url=refreshed.instance_url,
            access_token=refreshed.access_token,
            refresh_token=refreshed.refresh_token or refresh_token,
        )

    original_build = install.build_salesforce_connection_service

    def _suppress_oauth_redirect(service) -> None:
        """Force Connect complete without browser OAuth (refresh-token pilot).

        CrmConnectionRuntime uses service._adapter (not the service method) for
        authorization_url_for_attempt — patch the adapter oauth_config.
        """

        if service is None:
            return
        service._oauth_config = None  # noqa: SLF001
        adapter = getattr(service, "_adapter", None)
        if adapter is not None:
            adapter._oauth_config = None  # noqa: SLF001

    def _build_conn(*, data_root=None, code_exchange=None):
        service = original_build(
            data_root=data_root,
            code_exchange=_refresh_code_exchange,
        )
        _suppress_oauth_redirect(service)
        return service

    install.build_salesforce_connection_service = _build_conn  # type: ignore[method-assign]

    # Also wrap build_salesforce_stack so every registration rebind is covered.
    original_stack = install.build_salesforce_stack

    def _build_stack(*, data_root=None, org_id_override=None):
        stack = original_stack(
            data_root=data_root, org_id_override=org_id_override
        )
        if stack is not None:
            _suppress_oauth_redirect(getattr(stack, "connections", None))
        return stack

    install.build_salesforce_stack = _build_stack  # type: ignore[method-assign]

    import uvicorn

    host = os.environ.get("EASYIMPORTS_API_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
