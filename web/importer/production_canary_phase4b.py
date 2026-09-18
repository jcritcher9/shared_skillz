"""Phase 4B — Controlled production HubSpot Company canary contracts.

Network-free helpers for seed-ID allowlist equality and finally-style cleanup.
Live dual-process runs require explicit per-run production authorization flags;
default CI never performs network I/O.

Authority:
``mappings_2/.../production_hubspot_crm_duplicate_operator_path_reliability.md``
Phase **4B**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import os
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence


LIVE_FLAG = "EASYIMPORTS_CRM_DUPE_PATH_4B_PRODUCTION_CANARY"
PORTAL_CLASS_FLAG = "EASYIMPORTS_CRM_DUPE_PATH_4B_PORTAL_CLASS"
OPERATOR_ACK_FLAG = "EASYIMPORTS_CRM_DUPE_PATH_4B_OPERATOR_ACK"
REQUIRED_OPERATOR_ACK = "I_AUTHORIZE_PRODUCTION_SYNTHETIC_COMPANY_CANARY"

# Production-intended portal classes only (never developer/sandbox reuse of 7B).
_ALLOWED_PRODUCTION_PORTAL_CLASSES = frozenset(
    {
        "standard",
        "production",
        "prod",
        "standard_production",
        "customer_work_portal",
    }
)
_REFUSED_NON_PRODUCTION_CLASSES = frozenset(
    {
        "developer_test",
        "developer",
        "sandbox",
        "developer_sandbox",
        "app_developer",
    }
)

MARKER_PREFIX = "ei-crmdupe-4b-prod-synthetic"


class ProductionCanaryAuthorizationError(RuntimeError):
    """Fail-closed production canary authorization boundary."""


class SeedAllowlistError(RuntimeError):
    """Allowlist equality violated; abort before write and enter cleanup."""


class CleanupRegistryError(RuntimeError):
    """Cleanup remaining active marked companies after finally-style attempt."""


def live_canary_enabled() -> bool:
    return os.environ.get(LIVE_FLAG, "").strip() == "1"


def require_production_canary_authorization(
    values: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Fail closed unless operator opts in and attests a production work portal.

    Does **not** authorize customer-record mutation. Only uniquely marked
    synthetic Companies created in the current run may be merged/cleaned.
    """

    env = dict(values or {})
    if str(env.get(LIVE_FLAG) or os.environ.get(LIVE_FLAG) or "").strip() != "1":
        raise ProductionCanaryAuthorizationError(
            f"Production canary refused: set {LIVE_FLAG}=1 for an explicit run."
        )
    ack = str(
        env.get(OPERATOR_ACK_FLAG) or os.environ.get(OPERATOR_ACK_FLAG) or ""
    ).strip()
    if ack != REQUIRED_OPERATOR_ACK:
        raise ProductionCanaryAuthorizationError(
            "Production canary refused: missing operator acknowledgement. "
            f"Set {OPERATOR_ACK_FLAG}={REQUIRED_OPERATOR_ACK!r}."
        )
    portal_class = (
        str(env.get(PORTAL_CLASS_FLAG) or os.environ.get(PORTAL_CLASS_FLAG) or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )
    if not portal_class:
        raise ProductionCanaryAuthorizationError(
            f"Production canary refused: set {PORTAL_CLASS_FLAG} to a production "
            f"work portal class ({sorted(_ALLOWED_PRODUCTION_PORTAL_CLASSES)})."
        )
    if portal_class in _REFUSED_NON_PRODUCTION_CLASSES:
        raise ProductionCanaryAuthorizationError(
            f"Production canary refused portal_class={portal_class!r}: do not "
            "reuse the historical non-production 7B developer/sandbox gate."
        )
    if portal_class not in _ALLOWED_PRODUCTION_PORTAL_CLASSES:
        raise ProductionCanaryAuthorizationError(
            f"Production canary refused portal_class={portal_class!r}: must be "
            f"one of {sorted(_ALLOWED_PRODUCTION_PORTAL_CLASSES)}."
        )
    return {
        "live_gate_flag": LIVE_FLAG,
        "portal_class": portal_class,
        "portal_attestation": "operator_declared_production_work_portal",
        "operator_ack": True,
        "customer_records_authorized": False,
        "synthetic_marker_only": True,
        "marker_prefix": MARKER_PREFIX,
        "entity_family": "company",
        "modes": ["dry_run", "execute"],
        "max_merges": 1,
        "seed_pair_count": 2,
    }


def digest_ids(ids: Iterable[str]) -> str:
    material = "|".join(sorted(str(i).strip() for i in ids if str(i).strip()))
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SeedIdAllowlist:
    """Immutable in-memory set of exactly two newly created synthetic IDs."""

    ids: frozenset[str]

    def __post_init__(self) -> None:
        cleaned = frozenset(str(i).strip() for i in self.ids if str(i).strip())
        if len(cleaned) != 2:
            raise SeedAllowlistError(
                f"Seed allowlist requires exactly two distinct IDs; got {len(cleaned)}."
            )
        object.__setattr__(self, "ids", cleaned)

    @classmethod
    def from_create_responses(cls, created_ids: Sequence[str]) -> "SeedIdAllowlist":
        return cls(ids=frozenset(str(i).strip() for i in created_ids))

    def digest(self) -> str:
        return digest_ids(self.ids)

    def assert_equals(
        self,
        candidate: Iterable[str],
        *,
        stage: str,
    ) -> None:
        actual = frozenset(str(i).strip() for i in candidate if str(i).strip())
        if actual != self.ids:
            raise SeedAllowlistError(
                f"Allowlist equality failed at {stage}: "
                f"expected_count=2 actual_count={len(actual)} "
                f"equal={actual == self.ids} digest_expected={self.digest()} "
                f"digest_actual={digest_ids(actual)}"
            )


@dataclass
class CleanupRegistry:
    """Finally-style tracking of every created synthetic Company ID."""

    created_ids: list[str] = field(default_factory=list)
    cleanup_attempts: list[str] = field(default_factory=list)
    cleanup_errors: list[str] = field(default_factory=list)
    cleaned_ids: list[str] = field(default_factory=list)

    def register_created(self, record_id: str) -> None:
        rid = str(record_id or "").strip()
        if not rid:
            raise SeedAllowlistError("Cannot register empty Company ID.")
        if rid not in self.created_ids:
            self.created_ids.append(rid)

    def run_finally(
        self,
        archive: Callable[[str], None],
        *,
        is_active: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Attempt cleanup for every created ID; report failures independently."""

        for rid in list(self.created_ids):
            self.cleanup_attempts.append(rid)
            try:
                archive(rid)
                self.cleaned_ids.append(rid)
            except Exception as exc:  # noqa: BLE001 — accumulate independently
                self.cleanup_errors.append(f"{rid}: {type(exc).__name__}")
        active_remaining: list[str] = []
        if is_active is not None:
            for rid in self.created_ids:
                try:
                    if is_active(rid):
                        active_remaining.append(rid)
                except Exception as exc:  # noqa: BLE001
                    self.cleanup_errors.append(
                        f"{rid}:active_check:{type(exc).__name__}"
                    )
                    active_remaining.append(rid)
        status = {
            "created_count": len(self.created_ids),
            "cleanup_attempt_count": len(self.cleanup_attempts),
            "cleanup_success_count": len(self.cleaned_ids),
            "cleanup_error_count": len(self.cleanup_errors),
            "active_remaining_count": len(active_remaining),
            "cleanup_ok": not active_remaining and not self.cleanup_errors,
            "id_digest": digest_ids(self.created_ids),
        }
        if active_remaining:
            raise CleanupRegistryError(
                "Cleanup left active marked synthetic Companies "
                f"(count={len(active_remaining)}). "
                "Archive remaining test Companies before retry. "
                f"cleanup_errors={len(self.cleanup_errors)}"
            )
        return status


def count_active_marked_companies(
    *,
    marker: str,
    search_marked_page: Callable[[str, str | None], tuple[Sequence[Any], str | None]],
    registered_ids: Sequence[str] = (),
    get_record: Callable[[str], Any] | None = None,
    max_pages: int = 20,
) -> int:
    """Independently prove zero active *marked* Companies (fail-closed).

    Contract (Phase 4B cleanup):
    - Requires a **marker-filtered** portal search (not an empty-registry free
      pass, and not an unfiltered full-portal ``active_population`` list).
    - Marker search finds Companies whose create response was lost/transient
      after remote creation (no registered ID) as long as the synthetic marker
      is present on a filterable property (Company ``description`` EQ).
    - Transport/search failures raise — never report zero when verification
      could not complete.
    - Hitting ``max_pages`` with a remaining cursor fails closed.
    - Registered IDs are re-checked via ``get_record`` when provided (fail
      closed on get errors) and unioned into the active count.
    - Returns a count only (callers must not log raw IDs).
    """

    marker_s = str(marker or "").strip()
    if len(marker_s) < 8:
        raise AssertionError(
            "marker required for independent cleanup verify (min 8 chars)"
        )
    if not callable(search_marked_page):
        raise CleanupRegistryError(
            "marker verify missing search_marked_page capability; fail closed"
        )

    active_keys: set[str] = set()
    cursor: str | None = None
    pages = 0
    while pages < max_pages:
        pages += 1
        try:
            records, next_cursor = search_marked_page(marker_s, cursor)
        except CleanupRegistryError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface, do not swallow
            raise CleanupRegistryError(
                "marker search failed "
                f"(class={type(exc).__name__}); fail closed — cannot claim "
                "zero active remaining"
            ) from exc
        for rec in records or []:
            if getattr(rec, "archived", False) is True:
                continue
            if isinstance(rec, Mapping) and rec.get("archived") is True:
                continue
            rid = ""
            if isinstance(rec, Mapping):
                rid = str(rec.get("id") or rec.get("record_id") or "").strip()
            else:
                rid = str(
                    getattr(rec, "record_id", None) or getattr(rec, "id", None) or ""
                ).strip()
            # Prefer stable id keys; fall back to object identity for mocks.
            active_keys.add(rid or f"anon:{id(rec)}")
        if not next_cursor:
            break
        cursor = str(next_cursor).strip() or None
        if not cursor:
            break
    else:
        raise CleanupRegistryError(
            f"marker search exceeded max_pages={max_pages} with remaining "
            "cursor; fail closed — cannot claim zero active remaining"
        )

    # Supplemental: every registered ID must also prove inactive when get is available.
    for rid in registered_ids:
        rid_s = str(rid or "").strip()
        if not rid_s:
            continue
        if get_record is None:
            # Registry alone is not independent proof; search already ran.
            continue
        if not callable(get_record):
            raise CleanupRegistryError(
                "marker verify get_record is not callable; fail closed"
            )
        try:
            rec = get_record(rid_s)
        except Exception as exc:  # noqa: BLE001
            raise CleanupRegistryError(
                "marker verify get() failed for a registered Company "
                f"(class={type(exc).__name__}); fail closed — cannot claim "
                "zero active remaining"
            ) from exc
        if rec is None or getattr(rec, "archived", False):
            continue
        active_keys.add(rid_s)

    return len(active_keys)


def count_active_registered_companies(
    registered_ids: Sequence[str],
    *,
    get_record: Callable[[str], Any],
    marker: str | None = None,
) -> int:
    """Deprecated path: registered-ID recheck only (not independent marker proof).

    Prefer :func:`count_active_marked_companies` which requires marker search.
    Kept for narrow unit tests of get() fail-closed behaviour.
    """

    if marker is not None and not str(marker).strip():
        raise AssertionError("marker required when provided for independent verify")
    if not hasattr(get_record, "__call__"):
        raise CleanupRegistryError(
            "marker verify missing get_record capability; fail closed"
        )
    # Empty registry must not report zero without a portal marker search.
    if not list(registered_ids):
        raise CleanupRegistryError(
            "empty registry cannot independently prove zero active marked "
            "Companies without marker search; fail closed"
        )
    active = 0
    for rid in registered_ids:
        rid_s = str(rid or "").strip()
        if not rid_s:
            continue
        try:
            rec = get_record(rid_s)
        except Exception as exc:  # noqa: BLE001 — surface, do not swallow
            raise CleanupRegistryError(
                "marker verify get() failed for a registered Company "
                f"(class={type(exc).__name__}); fail closed — cannot claim "
                "zero active remaining"
            ) from exc
        if rec is None or getattr(rec, "archived", False):
            continue
        active += 1
    return active


def seed_pair_with_immediate_registration(
    registry: CleanupRegistry,
    create_one: Callable[[int], str],
    *,
    fail_after_first_create: bool = False,
) -> SeedIdAllowlist:
    """Create exactly two Companies, registering each ID before the next create.

    Cleanup protection begins immediately after the first successful create.
    ``fail_after_first_create`` injects a failure after the first register so
    the finally path must still attempt cleanup for that ID.
    """

    ids: list[str] = []
    for index in range(2):
        rid = str(create_one(index) or "").strip()
        if not rid:
            raise SeedAllowlistError(f"create_one({index}) returned empty id")
        # Register before the next external action (next create or stage work).
        registry.register_created(rid)
        ids.append(rid)
        if fail_after_first_create and index == 0:
            raise RuntimeError("injected_failure_after:first_create")
    return SeedIdAllowlist.from_create_responses(ids)


def run_canary_stages(
    *,
    stages: Sequence[str],
    create_one: Callable[[int], str],
    on_stage: Callable[[str, SeedIdAllowlist, CleanupRegistry], None],
    archive: Callable[[str], None],
    is_active: Callable[[str], bool],
    fail_after: str | None = None,
    fail_after_first_create: bool = False,
) -> dict[str, Any]:
    """Drive allowlist stages with finally-style cleanup (network-free harness).

    Each Company ID is registered immediately after its create response.
    ``fail_after`` injects a failure after a named post-seed stage.
    ``fail_after_first_create`` injects a failure after the first create
    (before the second create) so cleanup must still cover the first ID.
    """

    registry = CleanupRegistry()
    allowlist: SeedIdAllowlist | None = None
    primary_error: str | None = None
    try:
        allowlist = seed_pair_with_immediate_registration(
            registry,
            create_one,
            fail_after_first_create=fail_after_first_create,
        )
        for stage in stages:
            on_stage(stage, allowlist, registry)
            if fail_after is not None and stage == fail_after:
                raise RuntimeError(f"injected_failure_after:{stage}")
    except Exception as exc:  # noqa: BLE001 — primary vs cleanup separation
        primary_error = f"{type(exc).__name__}:{exc}"
    cleanup_status: dict[str, Any] | None = None
    cleanup_error: str | None = None
    try:
        cleanup_status = registry.run_finally(archive, is_active=is_active)
    except Exception as exc:  # noqa: BLE001
        cleanup_error = f"{type(exc).__name__}:{exc}"
        cleanup_status = {
            "created_count": len(registry.created_ids),
            "cleanup_attempt_count": len(registry.cleanup_attempts),
            "cleanup_success_count": len(registry.cleaned_ids),
            "cleanup_error_count": len(registry.cleanup_errors),
            "active_remaining_count": -1,
            "cleanup_ok": False,
            "id_digest": digest_ids(registry.created_ids),
        }
    return {
        "primary_error": primary_error,
        "cleanup_error": cleanup_error,
        "cleanup": cleanup_status,
        "allowlist_digest": None if allowlist is None else allowlist.digest(),
        "created_count": len(registry.created_ids),
        "cleanup_attempt_count": len(registry.cleanup_attempts),
        "marker_prefix": MARKER_PREFIX,
    }


def sanitized_evidence_template(
    *,
    authorization: Mapping[str, Any],
    allowlist_converged: bool,
    dry_run_zero_write: bool,
    execute_one_merge: bool,
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    """Sanitized evidence: counts/booleans/digests only (no raw IDs/secrets)."""

    return {
        "phase": "4B",
        "mode": "production_canary",
        "authorization": dict(authorization),
        "allowlist_converged": bool(allowlist_converged),
        "seed_pair_count": 2,
        "dry_run_zero_write": bool(dry_run_zero_write),
        "execute_one_merge": bool(execute_one_merge),
        "cleanup": dict(cleanup),
        "marker_prefix": MARKER_PREFIX,
    }


def assert_evidence_safe(obj: Any, *, path: str = "$") -> None:
    """Refuse secrets/raw identifiers in committed evidence payloads."""

    forbidden_keys = {
        "access_token",
        "token",
        "refresh_token",
        "client_secret",
        "api_key",
        "password",
        "secret",
        "hub_id",
        "portal_id",
        "private_app_token",
        "record_id",
        "company_id",
        "survivor_id",
        "loser_id",
        "member_ids",
        "seed_ids",
        "created_ids",
    }
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).strip().lower()
            if lowered in forbidden_keys and not lowered.endswith("_digest"):
                raise AssertionError(f"Evidence refuses key at {path}.{key}")
            if lowered.endswith("_ids") and lowered not in {
                "cleanup_ok",
            }:
                # allow counts only, not id lists
                if isinstance(value, (list, tuple, set)):
                    raise AssertionError(
                        f"Evidence refuses raw id list at {path}.{key}"
                    )
            assert_evidence_safe(value, path=f"{path}.{key}")
        return
    if isinstance(obj, list):
        for index, item in enumerate(obj):
            assert_evidence_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(obj, str):
        if obj.startswith("pat-") or "BEGIN PRIVATE KEY" in obj:
            raise AssertionError(f"Evidence refuses secret material at {path}")
        return
