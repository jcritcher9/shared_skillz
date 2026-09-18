#!/usr/bin/env python3
"""Seed one durable SCR-3C clearable reference-acquisition pause.

This helper is used only by the dual-process Django acceptance test. It owns
the backend imports so the Django test module preserves the web/API package
boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from mappings_2.api.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--owner-session", required=True)
    args = parser.parse_args()

    state_root = Path(args.state_root).resolve()
    owner = str(args.owner_session)
    app = create_app(state_root=state_root)

    with TestClient(app, raise_server_exceptions=False) as client:
        started = client.post(
            "/v1/crm/connections",
            headers={
                "Idempotency-Key": "scr3c-connect",
                "X-Owner-Session": owner,
            },
            json={"provider_key": "fake"},
        )
        started.raise_for_status()
        connection = started.json()
        completed = client.post(
            f"/v1/crm/connections/{connection['connection_id']}/oauth/complete",
            headers={
                "Idempotency-Key": "scr3c-oauth",
                "X-Owner-Session": owner,
            },
            json={
                "authorization_code": "code",
                "state": connection["authorization"]["state"],
            },
        )
        completed.raise_for_status()

        journey_response = client.post(
            "/v1/crm/duplicate-journeys",
            headers={
                "Idempotency-Key": "scr3c-journey",
                "X-Owner-Session": owner,
            },
            json={
                "connection_id": connection["connection_id"],
                "entity_family": "company",
                "source_mode": "acquire_all",
                "duplicate_execution_maximum": "dry_run",
            },
        )
        journey_response.raise_for_status()
        journey = journey_response.json()
        run_id = str(journey["run_id"])

        stack = app.state.crm_stack_ports.fake_stack
        if stack is None:
            raise RuntimeError("The SCR-3C fixture requires the fake CRM stack.")
        cursor_path = stack.reference._cursors.path
        original_execute = stack.reference.execute
        captured_digest: list[str] = []

        def execute_with_malformed_saved_progress(invocation, *, attempt_id: str):
            captured_digest.append(invocation.work_digest)
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            cursor_path.write_text(
                json.dumps({invocation.work_digest: []}),
                encoding="utf-8",
            )
            return original_execute(invocation, attempt_id=attempt_id)

        stack.reference.execute = execute_with_malformed_saved_progress
        try:
            grant_response = client.post(
                "/v1/crm/duplicate-read-grants",
                headers={
                    "Idempotency-Key": "scr3c-grant",
                    "X-Owner-Session": owner,
                },
                json={
                    "population_source": "crm_scan",
                    "connection_id": connection["connection_id"],
                    "entity_family": "company",
                },
            )
            grant_response.raise_for_status()
            authorized = client.post(
                "/v1/crm/duplicate-read-grants/"
                f"{grant_response.json()['grant_id']}"
                "/implied-reference-authorization",
                headers={
                    "Idempotency-Key": "scr3c-authorize",
                    "X-Owner-Session": owner,
                },
                json={"run_id": run_id},
            )
            authorized.raise_for_status()
        finally:
            stack.reference.execute = original_execute

        workflow_response = client.get(
            f"/v1/workflows/{run_id}",
            headers={"X-Owner-Session": owner},
        )
        workflow_response.raise_for_status()
        workflow = workflow_response.json()
        if workflow.get("status") != "paused_unknown":
            raise RuntimeError(f"SCR-3C fixture did not pause: {workflow!r}")
        diagnostic = workflow.get("paused_effect_diagnostic") or {}
        if diagnostic.get("code") != (
            "reference_acquisition_saved_progress_reset_required"
        ):
            raise RuntimeError(f"SCR-3C fixture has wrong diagnostic: {workflow!r}")
        if len(captured_digest) != 1:
            raise RuntimeError("SCR-3C fixture did not capture exactly one digest.")

        print(
            json.dumps(
                {
                    "journey_id": journey["journey_id"],
                    "run_id": run_id,
                    "revision": workflow["revision"],
                    "work_digest": captured_digest[0],
                    "cursor_path": str(cursor_path),
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
