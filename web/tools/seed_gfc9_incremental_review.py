#!/usr/bin/env python3
"""Seed an incremental Company review whose first window is ready.

Used only by the GFC-9 Django operator proof. Owns mappings_2 imports so the
Django test module stays on the web side of the package boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mappings_2.api.app import create_app
from mappings_2.application.crm.duplicates.handoffs import (
    build_incremental_review_handoff,
)
from mappings_2.products.duplicate_resolution import analysis_cycles as cycles
from mappings_2.products.payloads.common import DuplicateAnalysisProgressPayload
from tests.mappings_2.duplicate_resolution.incremental_review_support import (
    _drive_discovery,
    _drive_ranking,
    _drive_recommendations_until,
    _pair_accounts,
    _persist_source,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    state_root = Path(args.state_root).resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    cycles.DEFAULT_FINAL_GROUPS_PER_CYCLE = 5
    app = create_app(state_root=state_root)
    application = app.state.product_application
    store = application.duplicate_analysis_work_store
    accounts = _pair_accounts(8)
    run_id = "gfc9-django-src"
    context = _drive_discovery(store, accounts, run_id=run_id)
    context = _drive_ranking(context)
    context = _drive_recommendations_until(context, ready_at_least=5)
    source = _persist_source(application, context)
    handoff = build_incremental_review_handoff(source)
    if handoff is None:
        raise RuntimeError("incremental review handoff was not available")
    review_run_id = "gfc9-django-review"
    application.start_review_workflow(
        source_run_id=run_id,
        review_handoff_id=handoff.handoff_id,
        idempotency_key="gfc9-django-start",
        run_id=review_run_id,
    )
    progress = context.require(DuplicateAnalysisProgressPayload)
    Path(args.output).write_text(
        json.dumps(
            {
                "source_run_id": run_id,
                "review_run_id": review_run_id,
                "review_ready": bool(progress.review_ready),
                "review_window_ready": bool(progress.review_window_ready),
                "review_groups_ready": int(progress.review_groups_ready),
                "review_groups_total": int(progress.review_groups_total),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
