#!/usr/bin/env python3
"""Seed durable fake CRM org state for real-process FE-EXEC tests.

Runs as a **separate process** with PYTHONPATH pointing at the repository root.
Django tests must not import ``mappings_2``; they invoke this helper instead.

Usage:
  python web/tools/seed_fake_crm_org.py --state-root DIR \\
    [--ids A1,A2,A3] [--name Acme] \\
    [--fail-loser-ids A1,A2] [--lost-response-loser-ids A2] \\
    [--clear-injections]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _split_ids_ordered(raw: str | None) -> tuple[str, ...]:
    """Preserve caller order for fixture rank / SystemModstamp assignment."""

    if not raw:
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for part in raw.split(","):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _split_ids_set(raw: str | None) -> set[str]:
    """Unordered id set for injection controls."""

    return set(_split_ids_ordered(raw))


# FE-CM-4 fixture identities. Campaign Ids are well-formed 701… keys so Django
# configure/freeze and FE-CM-1 lookup share the same shape. Contact emails are
# unique per operator-path case so execute tests do not NOOP each other.
FE_CM4_CAMPAIGN_ID = "701FAKE00000003AAA"
FE_CM4_CAMPAIGN_NAME = "Unique Webinar"
FE_CM4_FLOOD_NAME = "Flooded Name"
FE_CM4_FLOOD_COUNT = 26  # CAMPAIGN_SEARCH_MAX_ROWS (25) + 1
FE_CM4_ACCOUNT_ID = "A-CM4"
FE_CM4_ACCOUNT_NAME = "Nomatch Co"
FE_CM4_CONTACTS = (
    ("C-CM4-DRY", "cm4-dry@example.test", "Dry"),
    ("C-CM4-EXEC", "cm4-exec@example.test", "Exec"),
    ("C-CM4-REPLAY", "cm4-replay@example.test", "Replay"),
    ("C-CM4-STALE", "cm4-stale@example.test", "Stale"),
    ("C-CM4-DISC", "cm4-disc@example.test", "Disc"),
    ("C-CM4-PKG", "cm4-pkg@example.test", "Pkg"),
)


def _seed_fe_cm4_fixture(org) -> list[str]:
    """Unique Campaign/Contact pairs plus a too-many-matches name flood."""

    org.upsert(
        record_id=FE_CM4_ACCOUNT_ID,
        object_type="Account",
        fields={
            "Name": FE_CM4_ACCOUNT_NAME,
            "account_name": FE_CM4_ACCOUNT_NAME,
            "rank": 1,
        },
        system_modstamp="2026-07-19T12:00:01+00:00",
    )
    seeded = [FE_CM4_ACCOUNT_ID]
    for index, (contact_id, email, given) in enumerate(FE_CM4_CONTACTS, start=1):
        org.upsert(
            record_id=contact_id,
            object_type="Contact",
            fields={
                "FirstName": given,
                "LastName": "Person",
                "Email": email,
                "contact_first_name": given,
                "contact_last_name": "Person",
                "contact_email": email,
                "AccountId": FE_CM4_ACCOUNT_ID,
                "AccountName": FE_CM4_ACCOUNT_NAME,
                "rank": index,
            },
            system_modstamp=f"2026-07-19T12:00:{index:02d}+00:00",
        )
        seeded.append(contact_id)
    org.ensure_campaign(
        FE_CM4_CAMPAIGN_ID,
        name=FE_CM4_CAMPAIGN_NAME,
        allowed_statuses=("Sent", "Responded"),
        status="In Progress",
        start_date="2026-05-01",
    )
    seeded.append(FE_CM4_CAMPAIGN_ID)
    for index in range(FE_CM4_FLOOD_COUNT):
        campaign_id = f"701FLOD{index:08d}AAA"
        org.ensure_campaign(
            campaign_id,
            name=FE_CM4_FLOOD_NAME,
            allowed_statuses=("Sent",),
        )
        seeded.append(campaign_id)
    return seeded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-root",
        required=True,
        help="API state root (contains fake_crm/ after seed).",
    )
    parser.add_argument("--ids", default="A1,A2,A3")
    parser.add_argument("--name", default="Acme")
    parser.add_argument(
        "--pair-count",
        type=int,
        default=0,
        help=(
            "When >0, seed this many two-record company pairs with distinct "
            "names (P0L/P0R Holdings000 …) for multi-window review fixtures. "
            "Ignores --ids/--name. Default website uses unique .com hosts so "
            "pairs score high (exact name+domain) for Phase 7 auto-merge."
        ),
    )
    parser.add_argument(
        "--medium-pair-count",
        type=int,
        default=0,
        help=(
            "When >0, seed this many same-name pairs without Website so they "
            "score medium (~70) for mixed auto-merge dual-process fixtures "
            "(M0L/M0R Medium000 …)."
        ),
    )
    parser.add_argument(
        "--pair-website-mode",
        choices=("high", "legacy"),
        default="high",
        help=(
            "high: unique holdingsN.com per pair (score ~95). "
            "legacy: shared acme.example website (prior Phase 5B seed shape)."
        ),
    )
    parser.add_argument("--fail-loser-ids", default="")
    parser.add_argument("--lost-response-loser-ids", default="")
    parser.add_argument(
        "--clear-injections",
        action="store_true",
        help="Reset injection controls to empty (default when flags omitted).",
    )
    parser.add_argument(
        "--inject-only",
        action="store_true",
        help="Only update injection controls on an existing org.json; do not reseed rows.",
    )
    parser.add_argument(
        "--field-fill-pair",
        action="store_true",
        help=(
            "Seed a two-member company pair for field-merge 4A-WIRE: A1 empty "
            "logical fill keys, A2 with account_website / account_linkedin_url / "
            "account_domain_final (no Website mix — Fake reference rejects both)."
        ),
    )
    parser.add_argument(
        "--fe-cm4",
        action="store_true",
        help=(
            "Seed the FE-CM-4 fake-stack fixture: unique Campaign + Contact "
            "pairs for list-import CM E2E, plus a too-many-matches name flood. "
            "Ignores --ids/--name/--pair-count."
        ),
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from mappings_2.integrations.crm.fake.org import FakeCrmOrg
    from mappings_2.integrations.crm.fake.provider import (
        build_fake_crm_stack,
        seed_company_duplicates,
    )

    state_root = Path(args.state_root).resolve()
    fake_root = state_root / "fake_crm"
    org_path = fake_root / "org.json"
    fail_ids = _split_ids_set(args.fail_loser_ids)
    lost_ids = _split_ids_set(args.lost_response_loser_ids)
    if args.clear_injections and not fail_ids and not lost_ids:
        fail_ids = set()
        lost_ids = set()

    if args.inject_only:
        if not org_path.exists():
            raise SystemExit(f"org.json not found for inject-only: {org_path}")
        org = FakeCrmOrg.load(org_path)
        org.fail_loser_ids = set(fail_ids)
        org.lost_response_loser_ids = set(lost_ids)
        org.save()
        print(f"injected fail={sorted(org.fail_loser_ids)} lost={sorted(org.lost_response_loser_ids)}")
        return 0

    stack = build_fake_crm_stack(fake_root)
    # Drop any leftover rows so pair-count / single-group seeds are exclusive.
    stack.org.records.clear()
    seeded_ids: list[str] = []
    high_pairs = int(args.pair_count or 0)
    medium_pairs = int(args.medium_pair_count or 0)
    if args.fe_cm4:
        seeded_ids = _seed_fe_cm4_fixture(stack.org)
    elif args.field_fill_pair:
        # A1: empty-field survivor candidate (rank 1). A2: loser with fills.
        stack.org.upsert(
            record_id="A1",
            object_type="Account",
            fields={"Name": str(args.name or "Acme"), "rank": 1},
            system_modstamp="2026-07-19T12:00:01+00:00",
        )
        stack.org.upsert(
            record_id="A2",
            object_type="Account",
            fields={
                "Name": str(args.name or "Acme"),
                "rank": 2,
                "account_website": "https://from-a2.example",
                "account_domain_final": "from-a2.example",
                "account_linkedin_url": "https://linkedin.com/company/a2",
            },
            system_modstamp="2026-07-19T12:00:02+00:00",
        )
        seeded_ids = ["A1", "A2"]
    elif high_pairs > 0 or medium_pairs > 0:
        for index in range(high_pairs):
            left = f"P{index}L"
            right = f"P{index}R"
            website = (
                f"holdings{index:03d}.com"
                if str(args.pair_website_mode) == "high"
                else "acme.example"
            )
            seed_company_duplicates(
                stack.org,
                ids=(left, right),
                name=f"Holdings{index:03d}",
                website=website,
            )
            seeded_ids.extend([left, right])
        for index in range(medium_pairs):
            left = f"M{index}L"
            right = f"M{index}R"
            # Same name, no Website → exact-name edge (~70), below auto floor 90.
            seed_company_duplicates(
                stack.org,
                ids=(left, right),
                name=f"Medium{index:03d}",
                website=None,
            )
            seeded_ids.extend([left, right])
    else:
        ids = _split_ids_ordered(args.ids) or ("A1", "A2", "A3")
        seed_company_duplicates(stack.org, ids=ids, name=args.name)
        seeded_ids = list(ids)
    # Fresh seed for FE-EXEC isolation across shared real-process state roots.
    stack.org.mutation_count = 0
    stack.org.transport_calls = []
    stack.org.fail_loser_ids = set(fail_ids)
    stack.org.lost_response_loser_ids = set(lost_ids)
    stack.org.stale_record_ids = set()
    # Clear merge residue so deleted losers do not leak into the next scenario.
    for record in stack.org.records.values():
        record.is_deleted = False
        record.master_record_id = None
    stack.org.save()
    stack.close()
    print(
        f"seeded {org_path} ids={seeded_ids} "
        f"fail={sorted(fail_ids)} lost={sorted(lost_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
