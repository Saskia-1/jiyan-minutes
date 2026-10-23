"""Local screenshot/note preview, correction and immutable batch commit."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
from typing import Callable
from uuid import uuid4

from chat_to_howto.api.session import digest
from chat_to_howto.domain.content_imports import (
    ContentBatchPage, ContentBatchView, ContentPreviewView, ImageOrderEdit, ImageUpload,
    PreviewImage, PreviewUnit, StoredImageView, UnitEdit,
)
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.content_classification import classify_body
from chat_to_howto.services.image_ocr import OcrError, OcrLine, _image_dimensions


_MAX_IMAGE = 15 * 1024 * 1024
_MAX_TOTAL = 64 * 1024 * 1024
_MAX_NOTE = 1024 * 1024
_MAX_PIXELS = 40_000_000
_MAX_UNITS = 2_000
_MAX_CONFIRMED_TEXT = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ImageCandidate:
    ref: str
    payload: bytes
    sha256: str
    suffix: str
    input_order: int


@dataclass(frozen=True, slots=True)
class _Pending:
    session_digest: str
    view: ContentPreviewView
    images: tuple[_ImageCandidate, ...]


class ContentImportService:
    def __init__(
        self, *, repository: ContentRepository, image_root: Path,
        clock: Callable[[], datetime], ocr: Callable[[Path], tuple[OcrLine, ...]],
        preview_ttl_seconds: int = 300,
    ) -> None:
        if preview_ttl_seconds < 1:
            raise ValueError("preview expiry must be positive")
        self._repository = repository
        self._image_root = Path(image_root)
        self._clock = clock
        self._ocr = ocr
        self._ttl = timedelta(seconds=preview_ttl_seconds)
        self._lock = RLock()
        self._pending: dict[str, _Pending] = {}
        self._ensure_private_root()

    def reconcile_orphan_images(self) -> None:
        """Remove only known application image names left without committed ownership."""
        with self._lock, self._repository._chat._lock:
            owned = {str(row[0]) for row in self._repository._chat._connection.execute("SELECT basename FROM content_images")}
            for path in self._image_root.iterdir():
                if re.fullmatch(r"(?:temp-)?[0-9a-f]{32}\.(?:png|jpg)", path.name) and path.name not in owned:
                    metadata = path.lstat()
                    if stat.S_ISREG(metadata.st_mode):
                        path.unlink()

    def close(self) -> None:
        with self._lock:
            self._pending.clear()

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("content import clock must be timezone-aware")
        return current.astimezone(timezone.utc)

    def _ensure_private_root(self) -> None:
        root = self._image_root
        if root.is_symlink():
            raise ValueError("private image directory is unsafe")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
            raise ValueError("private image directory is unsafe")
        os.chmod(root, 0o700)

    @staticmethod
    def _image_type(image: ImageUpload) -> tuple[str, str]:
        if not isinstance(image.payload, bytes) or not 0 < len(image.payload) <= _MAX_IMAGE:
            raise ValueError("image_limit_exceeded")
        suffix = Path(image.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("unsupported_image")
        dimensions = _image_dimensions(image.payload)
        if dimensions[0] <= 0 or dimensions[1] <= 0 or dimensions[0] * dimensions[1] > _MAX_PIXELS:
            raise ValueError("image_limit_exceeded")
        if suffix == ".png" and not image.payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("unsupported_image")
        if suffix != ".png" and not image.payload.startswith(b"\xff\xd8"):
            raise ValueError("unsupported_image")
        return (".png", "image/png") if suffix == ".png" else (".jpg", "image/jpeg")

    def _temporary_ocr(self, candidate: _ImageCandidate) -> tuple[OcrLine, ...]:
        basename = f"temp-{uuid4().hex}{candidate.suffix}"
        path = self._image_root / basename
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise OcrError("helper_unavailable") from None
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(candidate.payload)
            return self._ocr(path)
        except OSError:
            raise OcrError("helper_unavailable") from None
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _fingerprint(view: ContentPreviewView, session_digest: str) -> str:
        payload = {
            "session": session_digest,
            "preview": view.preview_id,
            "images": [image.model_dump(mode="json") for image in view.images],
            "units": [unit.model_dump(mode="json") for unit in view.units],
            "status": view.status,
        }
        return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def create_preview(
        self, images: tuple[ImageUpload, ...] | None, pasted_note: str | None,
        *, session_id: str, retain_pending: bool = False,
    ) -> ContentPreviewView:
        if not session_id:
            raise PermissionError("local session unavailable")
        if type(retain_pending) is not bool:
            raise ValueError("invalid_preview_retention_flag")
        if (images is None) == (pasted_note is None):
            raise ValueError("provide images or pasted note")
        now = self._now()
        session_hash = digest(session_id)
        preview_id = uuid4().hex
        units: list[PreviewUnit] = []
        image_views: list[PreviewImage] = []
        candidates: list[_ImageCandidate] = []
        warnings: list[str] = []
        suggested: str = "note"
        status: str = "ready"

        if pasted_note is not None:
            if len(pasted_note.encode("utf-8")) > _MAX_NOTE:
                raise ValueError("note_limit_exceeded")
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", pasted_note) if part.strip()]
            if len(paragraphs) > _MAX_UNITS:
                raise ValueError("content_unit_limit_exceeded")
            for index, text in enumerate(paragraphs):
                units.append(PreviewUnit(unit_id=f"candidate-{index}", kind="note_passage", group_id="note-1", text=text, order=index))
            if not units:
                status = "empty_body"
        else:
            assert images is not None
            if not images or len(images) > 20 or sum(len(item.payload) for item in images) > _MAX_TOTAL:
                raise ValueError("image_limit_exceeded")
            if len({item.input_order for item in images}) != len(images) or any(item.input_order < 0 for item in images):
                raise ValueError("image_order_invalid")
            kinds: set[str] = set()
            seen_hashes: set[str] = set()
            for index, image in enumerate(sorted(images, key=lambda item: item.input_order)):
                suffix, _ = self._image_type(image)
                fingerprint = sha256(image.payload).hexdigest()
                if fingerprint in seen_hashes:
                    warnings.append("exact_duplicate_image")
                seen_hashes.add(fingerprint)
                ref = f"image-{index}"
                candidate = _ImageCandidate(ref, image.payload, fingerprint, suffix, image.input_order)
                candidates.append(candidate)
                image_views.append(PreviewImage(image_ref=ref, filename=Path(image.filename).name[:255], input_order=image.input_order, sha256=fingerprint))
                try:
                    classified = classify_body(self._temporary_ocr(candidate))
                except OcrError as error:
                    if error.code == "unsupported_image":
                        raise ValueError("unsupported_image") from None
                    status = "ocr_unavailable"
                    warnings.append(error.code)
                    continue
                kinds.add(classified.kind)
                if classified.boundary_uncertain and status == "ready":
                    status = "boundary_uncertain"
                if classified.kind == "note" and classified.body_lines:
                    # OCR returns visual lines, not semantic paragraphs. Keep one
                    # note page together so retrieval cannot select only one line.
                    units.append(PreviewUnit(
                        unit_id=f"candidate-{len(units)}", kind="note_passage", group_id=ref,
                        text="\n".join(line.text.strip() for line in classified.body_lines),
                        order=len(units), image_ref=ref,
                    ))
                else:
                    for line in classified.body_lines:
                        units.append(PreviewUnit(
                            unit_id=f"candidate-{len(units)}", kind="chat_message" if classified.kind == "chat" else "note_passage",
                            group_id=ref, text=line.text.strip(), order=len(units), image_ref=ref,
                        ))
            suggested = next(iter(kinds)) if len(kinds) == 1 else "uncertain"
            if len(units) > _MAX_UNITS:
                raise ValueError("content_unit_limit_exceeded")
            if not units and status == "ready":
                status = "empty_body"
            elif units and suggested == "uncertain" and status == "ready":
                status = "boundary_uncertain"
            if len(candidates) > 1:
                warnings.append("possible_overlapping_screenshots_review_manually")
        view = ContentPreviewView(
            preview_id=preview_id, preview_fingerprint="0" * 64,
            suggested_kind=suggested, status=status, units=tuple(units),
            images=tuple(image_views), warnings=tuple(dict.fromkeys(warnings)),
            expires_at_utc=now + self._ttl,
        )
        view = view.model_copy(update={"preview_fingerprint": self._fingerprint(view, session_hash)})
        with self._lock:
            self._pending = {key: value for key, value in self._pending.items() if value.view.expires_at_utc > now and (retain_pending or value.session_digest != session_hash)}
            retained_bytes = sum(
                sum(len(image.payload) for image in pending.images)
                + sum(len(unit.text.encode("utf-8")) for unit in pending.view.units)
                for pending in self._pending.values()
            )
            requested_bytes = sum(len(image.payload) for image in candidates) + sum(len(unit.text.encode("utf-8")) for unit in units)
            if len(self._pending) >= 8 or retained_bytes + requested_bytes > 128 * 1024 * 1024:
                raise ValueError("preview_capacity_exceeded")
            self._pending[preview_id] = _Pending(session_hash, view, tuple(candidates))
        return view

    def _owned(self, preview_id: str, session_id: str) -> _Pending:
        pending = self._pending.get(preview_id)
        if pending is None or not session_id or not hmac.compare_digest(pending.session_digest, digest(session_id)):
            raise PermissionError("content preview unavailable")
        if pending.view.expires_at_utc <= self._now():
            del self._pending[preview_id]
            raise ValueError("content preview expired")
        return pending

    def revise_preview(
        self, preview_id: str, edits: tuple[UnitEdit, ...], *, session_id: str,
        image_orders: tuple[ImageOrderEdit, ...] | None = None,
    ) -> ContentPreviewView:
        with self._lock:
            pending = self._owned(preview_id, session_id)
            current = {unit.unit_id: unit for unit in pending.view.units}
            if len(edits) != len(current) or {edit.unit_id for edit in edits} != set(current):
                raise ValueError("content unit selection changed")
            if len({edit.order for edit in edits}) != len(edits):
                raise ValueError("content order duplicates")
            units = []
            for edit in sorted(edits, key=lambda item: item.order):
                if not edit.text.strip() or (edit.kind == "note_passage" and (edit.sender_name is not None or edit.sent_at is not None)):
                    raise ValueError("content metadata is invalid")
                if edit.sent_at is not None and (edit.sent_at.tzinfo is None or edit.sent_at.utcoffset() is None):
                    raise ValueError("content time must have timezone")
                old = current[edit.unit_id]
                units.append(PreviewUnit(
                    unit_id=edit.unit_id, kind=edit.kind, group_id=edit.group_id,
                    text=edit.text.strip(), sender_name=edit.sender_name,
                    sent_at=edit.sent_at, order=edit.order, image_ref=old.image_ref,
                ))
            if sum(len(unit.text.encode("utf-8")) for unit in units) > _MAX_CONFIRMED_TEXT:
                raise ValueError("content_text_limit_exceeded")
            images = pending.view.images
            candidates = pending.images
            if image_orders is not None:
                old_refs = {image.image_ref for image in images}
                if {item.image_ref for item in image_orders} != old_refs or len(image_orders) != len(old_refs) or len({item.input_order for item in image_orders}) != len(image_orders):
                    raise ValueError("image order invalid")
                order_by_ref = {item.image_ref: item.input_order for item in image_orders}
                images = tuple(sorted((image.model_copy(update={"input_order": order_by_ref[image.image_ref]}) for image in images), key=lambda item: item.input_order))
                candidates = tuple(replace(candidate, input_order=order_by_ref[candidate.ref]) for candidate in candidates)
            status = "ready" if units and pending.view.status != "ocr_unavailable" else pending.view.status
            view = pending.view.model_copy(update={"units": tuple(units), "images": images, "status": status})
            view = view.model_copy(update={"preview_fingerprint": self._fingerprint(view, pending.session_digest)})
            self._pending[preview_id] = replace(pending, view=view, images=candidates)
            return view

    def commit_preview(self, preview_id: str, fingerprint: str, *, session_id: str) -> ContentBatchView:
        with self._lock:
            pending = self._owned(preview_id, session_id)
            view = pending.view
            if not hmac.compare_digest(view.preview_fingerprint, fingerprint):
                raise PermissionError("content preview changed")
            if view.status != "ready" or not view.units:
                raise ValueError("content preview not ready")
            batch_id = uuid4().hex
            created = self._now()
            image_rows: list[dict[str, object]] = []
            new_paths: list[Path] = []
            id_by_ref: dict[str, str] = {}
            try:
                for candidate in pending.images:
                    image_id = uuid4().hex
                    basename = f"{image_id}{candidate.suffix}"
                    path = self._image_root / basename
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    descriptor = os.open(path, flags, 0o600)
                    new_paths.append(path)
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(candidate.payload)
                    id_by_ref[candidate.ref] = image_id
                    image_rows.append({
                        "image_id": image_id, "input_order": candidate.input_order,
                        "sha256": candidate.sha256, "basename": basename,
                        "mime_type": "image/png" if candidate.suffix == ".png" else "image/jpeg",
                    })
                units = []
                for unit in view.units:
                    typed = "chat" if unit.kind == "chat_message" else "note"
                    stable = sha256(f"{batch_id}:{unit.order}:{typed}".encode()).hexdigest()
                    units.append({
                        "unit_id": f"content:v1:{typed}:{stable}", "image_id": id_by_ref.get(unit.image_ref),
                        "kind": unit.kind, "group_id": unit.group_id,
                        "source_order": unit.order, "text": unit.text,
                        "sender_name": unit.sender_name,
                        "sent_at": unit.sent_at.isoformat() if unit.sent_at is not None else None,
                    })
                self._repository.save_batch(
                    batch_id=batch_id, session_digest=pending.session_digest,
                    created_at_utc=created.isoformat(), units=units, images=image_rows,
                )
            except BaseException:
                for path in new_paths:
                    path.unlink(missing_ok=True)
                raise
            del self._pending[preview_id]
            return ContentBatchView(
                batch_id=batch_id, unit_count=len(units), image_count=len(image_rows),
                images=tuple(StoredImageView(image_id=row["image_id"], input_order=row["input_order"], sha256=row["sha256"]) for row in image_rows),
                created_at_utc=created,
            )

    def recover_batches(self, *, session_id: str, cursor: str | None, limit: int) -> ContentBatchPage:
        if not session_id:
            raise PermissionError("local session unavailable")
        if not 1 <= limit <= 50:
            raise ValueError("recovery limit invalid")
        rows = self._repository.list_batches(session_digest=digest(session_id))
        if cursor is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", cursor):
                raise ValueError("recovery cursor invalid")
            start = next((index + 1 for index, row in enumerate(rows) if row["batch_id"] == cursor), None)
            if start is None:
                raise ValueError("recovery cursor invalid")
            rows = rows[start:]
        selected = rows[:limit]
        items = tuple(ContentBatchView(
            batch_id=row["batch_id"], unit_count=row["unit_count"],
            image_count=len(images := self._repository.list_images(row["batch_id"], session_digest=digest(session_id))),
            images=tuple(StoredImageView(image_id=image["image_id"], input_order=image["input_order"], sha256=image["sha256"]) for image in images),
            created_at_utc=datetime.fromisoformat(row["created_at_utc"]),
        ) for row in selected)
        return ContentBatchPage(items=items, next_cursor=selected[-1]["batch_id"] if len(rows) > limit else None)

    def get_image(self, image_id: str, *, session_id: str) -> tuple[bytes, str]:
        if not session_id or not re.fullmatch(r"[0-9a-f]{32}", image_id):
            raise PermissionError("image unavailable")
        row = self._repository.owned_image(image_id, session_digest=digest(session_id))
        if row is None:
            raise PermissionError("image unavailable")
        basename = row["basename"]
        if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg)", basename):
            raise ValueError("invalid stored image path")
        path = self._image_root / basename
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_IMAGE:
                raise ValueError("invalid stored image")
            with os.fdopen(descriptor, "rb", closefd=False) as file:
                payload = file.read(_MAX_IMAGE + 1)
            return payload, row["mime_type"]
        finally:
            os.close(descriptor)
