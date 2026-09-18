#!/usr/bin/env python3
"""Mark selected high-score CRM-dupe groups ineligible for auto-disposition.

Adds ``execution_blockers`` while keeping confidence score/band high (95/high),
rebuilds the approval_bundle + opaque review handoff (new handoff id) so Django
Continue sees the updated projection.

Usage:
  python web/tools/patch_crm_dupe_high_score_ineligible.py \\
    --state-root DIR --run-id RUN --group-index 0
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--group-index",
        type=int,
        action="append",
        default=[],
        help="0-based group index in discovery order (repeatable).",
    )
    parser.add_argument(
        "--blocker",
        default="phase7c_high_score_ineligible_fixture",
        help="Opaque execution blocker token.",
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from dataclasses import replace

    from mappings_2.api.app import create_app
    from mappings_2.application.crm.duplicates.handoffs import (
        _analysis_bundle,
        build_review_handoff,
    )
    from mappings_2.products.payloads.common import DuplicateRecommendationsPayload
    from mappings_2.workflow_operations.engines.duplicate_resolution import (
        build_approval_bundle,
        build_naive_merge_recommendations,
        rank_account_survivors,
    )

    state_root = Path(args.state_root).resolve()
    app = create_app(state_root=state_root)
    service = app.state.product_application
    stored = service.job.load(str(args.run_id))
    run = stored.run
    bundle, _entity = _analysis_bundle(run)
    if bundle is None:
        raise SystemExit(f"run {args.run_id!r} has no analysis approval_bundle")

    discovery = deepcopy(bundle.discovery)
    groups = discovery.groups_df.copy()
    if groups.empty:
        raise SystemExit("discovery groups_df is empty")
    order = list(groups["duplicate_group_id"].astype(str))
    indices = list(args.group_index or [0])
    for index in indices:
        if index < 0 or index >= len(order):
            raise SystemExit(f"group index {index} out of range (n={len(order)})")

    blocker = str(args.blocker)
    if "execution_blockers" not in groups.columns:
        groups["execution_blockers"] = [() for _ in range(len(groups))]
    for index in indices:
        gid = order[index]
        mask = groups["duplicate_group_id"].astype(str).eq(gid)
        groups.loc[mask, "duplicate_confidence_score"] = 95
        groups.loc[mask, "duplicate_confidence_band"] = "high"
        if "default_review_lane" in groups.columns:
            groups.loc[mask, "default_review_lane"] = "approval"
        for idx in groups.index[mask]:
            groups.at[idx, "execution_blockers"] = (blocker,)
    discovery.groups_df = groups

    ranking = rank_account_survivors(discovery, as_of_date=date(2026, 8, 5))
    recommendations = build_naive_merge_recommendations(discovery, ranking)
    final_bundle = build_approval_bundle(discovery, ranking, recommendations)

    rec = run.payloads.require(DuplicateRecommendationsPayload)
    run.payloads.put(replace(rec, approval_bundle=final_bundle))

    # Rebuild handoff so binding/revisions match patched discovery content.
    handoff = build_review_handoff(run)
    if handoff is None:
        raise SystemExit("build_review_handoff returned None after patch")
    run.active_review_handoff = handoff
    service.job.store.save(run, expected_revision=stored.revision)
    print(
        f"patched run={args.run_id} groups={indices} "
        f"high-score+blocker={blocker!r} handoff={handoff.handoff_id} "
        f"state={state_root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
