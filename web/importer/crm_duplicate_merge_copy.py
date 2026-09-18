"""Operator-visible CRM-dupe merge-plan copy (DRUX-3).

Wire names stay ``merge_action=finalize|authorize|invalidate``. Tests and
templates must share these needles so the next copy change is one edit.
"""

from __future__ import annotations

APPROVE_MERGE_PLAN_LABEL = "Approve merge plan"
AUTHORIZE_MERGE_STEP_LABEL = "Authorize merge step"
GO_BACK_TO_REVIEWING_GROUPS_LABEL = "Go back to reviewing groups"
CONTINUE_TO_APPROVE_MERGE_PLAN_LABEL = "Continue to approve merge plan"
EDIT_DISPOSITIONS_LABEL = "Edit dispositions"

LEGACY_FREEZE_MERGE_PLAN_LABEL = "Freeze merge plan"
LEGACY_INVALIDATE_FROZEN_PLAN_LABEL = "Invalidate frozen plan"

MERGE_PLAN_CTA_CLASS = "btn-cta"
MERGE_MODE_CHOICES_CLASS = "merge-mode-choices"


def html_shows_unfrozen_merge_plan(html: str) -> bool:
    return "Review merge plan" in html and APPROVE_MERGE_PLAN_LABEL in html


def html_shows_authorize_merge_step(html: str) -> bool:
    return AUTHORIZE_MERGE_STEP_LABEL in html


def html_shows_merge_ready(html: str) -> bool:
    """True when the merge summary is showing approve or authorize."""
    if "Review merge plan" not in html:
        return False
    return (
        APPROVE_MERGE_PLAN_LABEL in html or AUTHORIZE_MERGE_STEP_LABEL in html
    )
