"""Phase 7C Grade B: backfill claim auto_merge from claimed session options.

Databases that already applied 0010 (field default ``none`` only) need this
separate migration — rewriting 0010 would never re-run on those installs.

Present-but-invalid session thresholds fail the upgrade (fail closed) rather
than silently becoming ``none``.
"""

from __future__ import annotations

from django.db import migrations


def _encode_threshold(raw) -> str | None:
    """Return ``none``, a valid 90–100 string, or raise for present-invalid values.

    Returns None when the key is absent (caller leaves claim unchanged / none).
    """

    if raw is None or raw == "" or raw == "none":
        return "none"
    # Reject bool (bool is int subclass).
    if isinstance(raw, bool):
        raise ValueError(
            f"auto_merge_min_confidence must be an integer 90–100 or 'none'; "
            f"got {raw!r}."
        )
    if isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(
                f"auto_merge_min_confidence must be a whole integer 90–100; "
                f"got {raw!r}."
            )
        number = int(raw)
    elif isinstance(raw, int):
        number = raw
    else:
        text = str(raw).strip()
        if text.lower() == "none":
            return "none"
        # Reject floats and non-digits in text form (e.g. "90.5", "9e1").
        if not text.lstrip("-").isdigit():
            raise ValueError(
                f"auto_merge_min_confidence must be an integer 90–100 or 'none'; "
                f"got {raw!r}."
            )
        number = int(text)
    if 90 <= number <= 100:
        return str(number)
    raise ValueError(
        f"auto_merge_min_confidence must be an integer 90–100 or 'none'; "
        f"got {number}."
    )


def _backfill_auto_merge_from_session(apps, schema_editor):
    Claim = apps.get_model("importer", "CrmDuplicateJourneyAttemptClaim")
    ImportSession = apps.get_model("importer", "ImportSession")
    for claim in Claim.objects.all().iterator():
        try:
            session = ImportSession.objects.get(pk=claim.claimed_session_id)
        except ImportSession.DoesNotExist:
            continue
        options = dict(session.options or {})
        if "auto_merge_min_confidence" not in options:
            # No durable operator threshold on the session — leave claim default.
            continue
        encoded = _encode_threshold(options.get("auto_merge_min_confidence"))
        assert encoded is not None
        current = str(getattr(claim, "auto_merge_min_confidence", "none") or "none")
        if current == encoded:
            continue
        claim.auto_merge_min_confidence = encoded
        claim.save(update_fields=["auto_merge_min_confidence"])


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0010_crm_dupe_7c_attempt_auto_merge"),
    ]

    operations = [
        migrations.RunPython(
            _backfill_auto_merge_from_session,
            migrations.RunPython.noop,
        ),
    ]
