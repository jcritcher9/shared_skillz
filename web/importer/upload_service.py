"""Crash-consistent upload slots backed by the shared mutation journal."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import mimetypes
import os
from pathlib import Path
from uuid import UUID, uuid4

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.text import get_valid_filename

from .api_client import (
    EasyImportsApiClient,
    MutationDispatchResult,
    MutationReuseError,
    create_or_reuse_mutation,
)
from .models import ApiMutation, ImportSession, SourceFile


@dataclass(frozen=True)
class UploadRegistration:
    mutation: ApiMutation
    source: SourceFile | None
    response: dict | None


def _logical_identity(session: ImportSession, role: str) -> str:
    return f"upload:{session.id}:{role}"


def _write_stable_upload(
    session: ImportSession, role: str, uploaded: UploadedFile
) -> tuple[Path, str, str]:
    safe_name = get_valid_filename(uploaded.name or "upload") or "upload"
    session.uploads_dir.mkdir(parents=True, exist_ok=True)
    final_path = session.uploads_dir / f"{role}__{uuid4().hex}__{safe_name}"
    temporary_path = final_path.with_suffix(final_path.suffix + ".part")
    digest = sha256()
    try:
        with temporary_path.open("xb") as target:
            for chunk in uploaded.chunks():
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, final_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    return final_path, safe_name, digest.hexdigest()


def save_and_register_upload(
    *,
    session: ImportSession,
    role: str,
    uploaded: UploadedFile,
    form_instance: UUID,
    logical_action_generation: int,
    csv_encoding: str,
    xlsx_sheet_index: int,
    client: EasyImportsApiClient | None = None,
) -> UploadRegistration:
    """Atomically bind a durable path to one active ``(session, role)`` slot."""
    path, safe_name, content_digest = _write_stable_upload(session, role, uploaded)
    suffix = Path(safe_name).suffix.lower()
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if suffix == ".xlsx"
        else (mimetypes.guess_type(safe_name)[0] or "text/csv")
    )
    form: dict[str, str] = {}
    if suffix == ".xlsx":
        form["xlsx_sheet_index"] = str(xlsx_sheet_index)
        frozen_encoding = ""
        frozen_sheet = xlsx_sheet_index
    else:
        form["csv_encoding"] = csv_encoding
        frozen_encoding = csv_encoding
        frozen_sheet = None
    multipart = {
        "filename": safe_name,
        "media_type": media_type,
        "content_digest": content_digest,
        "byte_count": path.stat().st_size,
        "form": form,
    }
    source: SourceFile | None = None
    old_path: Path | None = None
    try:
        with transaction.atomic():
            # Lock order: ImportSession → SourceFile (by pk) → ApiMutation.
            ImportSession.objects.select_for_update().get(pk=session.pk)
            from .setup_service import latest_detached_source, lock_session_sources

            locked_sources = lock_session_sources(session)
            current = next(
                (
                    item
                    for item in locked_sources
                    if item.role == role
                    and item.upload_mutation_id is not None
                    and item.detached_at is None
                ),
                None,
            )
            identity = _logical_identity(session, role)
            existing = ApiMutation.objects.filter(
                session=session,
                logical_action_identity=identity,
                logical_action_generation=logical_action_generation,
            ).first()

            if (
                current is not None
                and current.slot_generation != logical_action_generation
            ):
                if not (
                    current.upload_mutation.state == ApiMutation.State.REJECTED
                    and current.replacement_authorized_at is not None
                    and logical_action_generation == current.slot_generation + 1
                ):
                    raise MutationReuseError(
                        "This upload slot is reserved; use its exact retry or explicit replacement action."
                    )
            if (
                current is not None
                and current.slot_generation == logical_action_generation
            ):
                existing = current.upload_mutation

            replacement = None
            if (
                current is not None
                and logical_action_generation > current.slot_generation
            ):
                replacement = current.upload_mutation
            elif current is None and logical_action_generation > 0:
                # Reuse after intentional detach requires the prior detached source.
                detached_prior = latest_detached_source(
                    session,
                    role,
                    slot_generation=logical_action_generation - 1,
                )
                if detached_prior is None:
                    raise MutationReuseError(
                        "This upload generation is not authorized. Detach the "
                        "previous file for this role before uploading a replacement."
                    )
                replacement = detached_prior.upload_mutation

            mutation = create_or_reuse_mutation(
                session=session,
                form_instance=form_instance,
                mutation_kind="register_upload",
                route="/v1/uploads",
                logical_action_identity=identity,
                logical_action_generation=logical_action_generation,
                multipart_metadata=multipart,
                resource_identity=f"{session.id}:{role}",
                replacement_of=replacement,
            )

            try:
                source = mutation.uploaded_source
            except SourceFile.DoesNotExist:
                source = None

            if source is None and mutation.state in {
                ApiMutation.State.COMPLETED,
                ApiMutation.State.REJECTED,
            }:
                # A historical form replay needs only its frozen result.
                pass
            elif source is None and current is None:
                source = SourceFile.objects.create(
                    session=session,
                    role=role,
                    original_name=safe_name,
                    stored_path=str(path),
                    media_type=media_type,
                    csv_encoding=frozen_encoding,
                    xlsx_sheet=frozen_sheet,
                    content_digest=content_digest,
                    upload_mutation=mutation,
                    slot_generation=logical_action_generation,
                )
            elif source is None and current is not None:
                if replacement is None:
                    raise MutationReuseError(
                        "This upload slot already has an active mutation."
                    )
                old_path = current.path
                current.original_name = safe_name
                current.stored_path = str(path)
                current.row_count = None
                current.columns = []
                current.media_type = media_type
                current.csv_encoding = frozen_encoding
                current.xlsx_sheet = frozen_sheet
                current.content_digest = content_digest
                current.api_upload_id = ""
                current.upload_mutation = mutation
                current.slot_generation = logical_action_generation
                current.replacement_authorized_at = None
                current.save()
                source = current
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if source is None or source.path != path:
        path.unlink(missing_ok=True)
    if old_path is not None and old_path != path:
        old_path.unlink(missing_ok=True)

    dispatch = (client or EasyImportsApiClient()).dispatch(
        mutation,
        file_path=(source.path if source is not None else None),
    )
    if source is not None and dispatch.mutation.state == ApiMutation.State.COMPLETED:
        resource = dispatch.response
        source.api_upload_id = resource["upload_id"]
        source.row_count = resource["row_count"]
        source.columns = resource["columns"]
        source.save(update_fields=["api_upload_id", "row_count", "columns"])
    return UploadRegistration(dispatch.mutation, source, dispatch.response)


def retry_upload(mutation: ApiMutation, *, client=None) -> UploadRegistration:
    try:
        source = mutation.uploaded_source
    except SourceFile.DoesNotExist as exc:
        raise MutationReuseError("The retained upload path is unavailable.") from exc
    dispatch: MutationDispatchResult = (client or EasyImportsApiClient()).dispatch(
        mutation,
        file_path=source.path,
        explicit_retry=True,
    )
    if dispatch.mutation.state == ApiMutation.State.COMPLETED:
        source.api_upload_id = dispatch.response["upload_id"]
        source.row_count = dispatch.response["row_count"]
        source.columns = dispatch.response["columns"]
        source.save(update_fields=["api_upload_id", "row_count", "columns"])
    return UploadRegistration(dispatch.mutation, source, dispatch.response)


@transaction.atomic
def materialize_upload_result(source: SourceFile) -> SourceFile:
    """Recover SourceFile metadata from an already-frozen upload response."""
    from .setup_service import lock_session_sources

    ImportSession.objects.select_for_update().get(pk=source.session_id)
    locked_sources = lock_session_sources(
        ImportSession.objects.get(pk=source.session_id)
    )
    locked = next(item for item in locked_sources if item.pk == source.pk)
    mutation = locked.upload_mutation
    response = mutation.response_json
    if mutation.state != ApiMutation.State.COMPLETED or not isinstance(response, dict):
        return locked
    locked.api_upload_id = response["upload_id"]
    locked.row_count = response["row_count"]
    locked.columns = response["columns"]
    locked.save(update_fields=["api_upload_id", "row_count", "columns"])
    return locked


@transaction.atomic
def authorize_rejected_upload_replacement(source: SourceFile) -> SourceFile:
    from .setup_service import lock_session_sources

    ImportSession.objects.select_for_update().get(pk=source.session_id)
    locked_sources = lock_session_sources(
        ImportSession.objects.get(pk=source.session_id)
    )
    locked = next(item for item in locked_sources if item.pk == source.pk)
    if locked.upload_mutation.state != ApiMutation.State.REJECTED:
        raise MutationReuseError("Only a rejected upload can be explicitly replaced.")
    locked.replacement_authorized_at = timezone.now()
    locked.save(update_fields=["replacement_authorized_at"])
    return locked


def orphan_upload_paths(session: ImportSession) -> list[Path]:
    """Return durable files not referenced by a SourceFile; cleanup is explicit."""
    if not session.uploads_dir.exists():
        return []
    referenced = {
        Path(value) for value in session.files.values_list("stored_path", flat=True)
    }
    return [
        path
        for path in session.uploads_dir.iterdir()
        if path.is_file() and path not in referenced
    ]
