"""Entity-specific auto-merge score helper copy (DRC-1 / DRC-3).

Django-only display strings. Do not import ``mappings_2``. DRC-3 amended the
Companies list in place (insert 80; narrow 70); People stays 100 / 90 / 75.
"""

from __future__ import annotations

# Compact tooltip copy for the existing 260px ``i`` bubble.
AUTO_MERGE_INFO_TIP = (
    "Groups at or above the threshold skip manual review. "
    "No CRM write until you authorize."
)
THRESHOLD_INFO_TIP = (
    "Integer from 90 to 100. Values below 90 are rejected, not lowered."
)

COMPANY_SCORE_BANDS: tuple[dict[str, str], ...] = (
    {
        "range": "90–100 (high)",
        "text": (
            "same company domain, or same name and same domain. "
            "Eligible to skip review if auto-merge is on."
        ),
    },
    {
        "range": "80 (medium)",
        "text": (
            "exact same name, every account in the pair has no meaningful "
            "domain. Always reviewed."
        ),
    },
    {
        "range": "70 (medium)",
        "text": (
            "exact same name; exactly one side has a meaningful domain and "
            "the other has none. Always reviewed. Two different company "
            "domains are split, not scored 70."
        ),
    },
    {
        "range": "Below 70 (low)",
        "text": (
            "similar names or weak geography. Not auto-merged; often quarantine."
        ),
    },
)

PERSON_SCORE_BANDS: tuple[dict[str, str], ...] = (
    {
        "range": "100 (high)",
        "text": (
            "same usable email. Eligible to skip review if auto-merge is on."
        ),
    },
    {
        "range": "90 (high)",
        "text": (
            "same name + same account + same phone. "
            "Eligible to skip review if auto-merge is on."
        ),
    },
    {
        "range": "75 (medium)",
        "text": (
            "same name + same account + same title. Always reviewed."
        ),
    },
)

# Audit-only People evidence. Do not list 50 or 25 as threshold scores.
PERSON_AUDIT_NOTE = (
    "Additional audit evidence: same name + same account only, or a shared "
    "email domain, may appear on an already-grouped pair. They do not form "
    "a group and do not set confidence."
)
