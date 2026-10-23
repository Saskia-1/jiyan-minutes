"""A confirmed body is the only text persisted from a local content preview."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import stat

import pytest

from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.api.session import digest
from chat_to_howto.services.image_ocr import OcrLine


def _line(text: str, y: float, x: float = 0.1) -> OcrLine:
    return OcrLine(text=text, box=(x, y, 0.7, 0.04), confidence=0.95)


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (200).to_bytes(4, "big") + (200).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"


@pytest.fixture
def service(tmp_path):
    from chat_to_howto.repositories.content import ContentRepository
    from chat_to_howto.services.content_imports import ContentImportService

    chat = ChatRepository(tmp_path / "chat.sqlite3")
    current = datetime(2026, 10, 12, tzinfo=timezone.utc)
    repository = ContentRepository(chat)
    instance = ContentImportService(
        repository=repository, image_root=tmp_path / "private-images",
        clock=lambda: current,
        ocr=lambda _: (
            _line("11:08", 0.02), _line("关注", 0.10),
            _line("一段真实的笔记正文", 0.35), _line("另一段正文", 0.48),
            _line("点赞 85", 0.94),
        ),
    )
    yield instance, repository, tmp_path
    instance.close()
    chat.close()


def test_preview_filters_chrome_and_commits_only_confirmed_body(service):
    from chat_to_howto.domain.content_imports import ImageUpload

    importer, repository, root = service
    preview = importer.create_preview(
        images=(ImageUpload(filename="screen.png", payload=_png(), input_order=0),),
        pasted_note=None, session_id="owner",
    )
    assert preview.suggested_kind == "note"
    assert len(preview.units) == 1
    assert preview.units[0].text == "一段真实的笔记正文\n另一段正文"
    assert all("关注" not in unit.text and "点赞" not in unit.text for unit in preview.units)
    assert repository.list_batches(session_digest="other") == ()

    batch = importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    assert batch.unit_count == 1
    assert batch.image_count == 1
    stored = repository.list_units(batch.batch_id, session_digest=digest("owner"))
    assert [unit["text"] for unit in stored] == ["一段真实的笔记正文\n另一段正文"]
    assert all(unit["sent_at"] is None and unit["sender_name"] is None for unit in stored)
    assert importer.recover_batches(session_id="owner", cursor=None, limit=10).items[0].batch_id == batch.batch_id
    saved = list((root / "private-images").glob("*.png"))
    assert len(saved) == 1
    assert stat.S_IMODE((root / "private-images").stat().st_mode) == 0o700
    assert stat.S_IMODE(saved[0].stat().st_mode) == 0o600


def test_edit_changes_fingerprint_and_type_without_inventing_time(service):
    from chat_to_howto.domain.content_imports import ImageUpload, UnitEdit

    importer, repository, _ = service
    preview = importer.create_preview(
        images=(ImageUpload(filename="screen.png", payload=_png(), input_order=0),),
        pasted_note=None, session_id="owner",
    )
    edits = tuple(UnitEdit(
        unit_id=unit.unit_id, kind="chat_message", group_id="chat-1",
        text=unit.text + "！", sender_name=None, sent_at=None, order=index,
    ) for index, unit in enumerate(preview.units))
    revised = importer.revise_preview(preview.preview_id, edits, session_id="owner")
    assert revised.preview_fingerprint != preview.preview_fingerprint
    with pytest.raises(PermissionError):
        importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    batch = importer.commit_preview(preview.preview_id, revised.preview_fingerprint, session_id="owner")
    assert all(unit["kind"] == "chat_message" for unit in repository.list_units(batch.batch_id, session_digest=digest("owner")))
    assert all(unit["sent_at"] is None for unit in repository.list_units(batch.batch_id, session_digest=digest("owner")))


def test_empty_body_and_cross_session_cannot_commit(service):
    from chat_to_howto.domain.content_imports import ImageUpload

    importer, _, _ = service
    importer._ocr = lambda _: (_line("关注", 0.10), _line("点赞 85", 0.94))
    empty = importer.create_preview(
        images=(ImageUpload(filename="screen.png", payload=_png(), input_order=0),),
        pasted_note=None, session_id="owner",
    )
    assert empty.status == "empty_body"
    with pytest.raises(ValueError):
        importer.commit_preview(empty.preview_id, empty.preview_fingerprint, session_id="owner")
    with pytest.raises(PermissionError):
        importer.commit_preview(empty.preview_id, empty.preview_fingerprint, session_id="other")


def test_pasted_note_is_bounded_and_keeps_order(service):
    importer, repository, _ = service
    preview = importer.create_preview(images=None, pasted_note="第一段\n\n第二段", session_id="owner")
    assert [unit.text for unit in preview.units] == ["第一段", "第二段"]
    batch = importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    assert [unit["text"] for unit in repository.list_units(batch.batch_id, session_digest=digest("owner"))] == ["第一段", "第二段"]
    with pytest.raises(ValueError):
        importer.create_preview(images=None, pasted_note="文" * 600_000, session_id="owner")


def test_image_limits_and_duplicate_warning(service):
    from chat_to_howto.domain.content_imports import ImageUpload

    importer, _, _ = service
    image = ImageUpload(filename="first.png", payload=_png(), input_order=0)
    with pytest.raises(ValueError):
        importer.create_preview(images=tuple(image for _ in range(21)), pasted_note=None, session_id="owner")
    preview = importer.create_preview(images=(image, ImageUpload(filename="other.png", payload=_png(), input_order=1)), pasted_note=None, session_id="owner")
    assert "exact_duplicate_image" in preview.warnings


def test_database_failure_cleans_new_image_file(service, monkeypatch):
    from chat_to_howto.domain.content_imports import ImageUpload

    importer, repository, root = service
    preview = importer.create_preview(images=(ImageUpload(filename="screen.png", payload=_png(), input_order=0),), pasted_note=None, session_id="owner")
    monkeypatch.setattr(repository, "save_batch", lambda **_: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    assert list((root / "private-images").glob("*.png")) == []


def test_image_is_private_and_the_preview_expires(service):
    from chat_to_howto.domain.content_imports import ImageUpload

    importer, _, _ = service
    preview = importer.create_preview(images=(ImageUpload(filename="screen.png", payload=_png(), input_order=0),), pasted_note=None, session_id="owner")
    with pytest.raises(PermissionError):
        importer.revise_preview(preview.preview_id, (), session_id="other")
    batch = importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    assert importer.get_image(batch.images[0].image_id, session_id="owner")[0] == _png()
    with pytest.raises(PermissionError):
        importer.get_image(batch.images[0].image_id, session_id="other")
    importer._clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc) + timedelta(hours=1)
    another = importer.create_preview(images=None, pasted_note="临时正文", session_id="owner")
    importer._clock = lambda: another.expires_at_utc + timedelta(seconds=1)
    with pytest.raises(ValueError):
        importer.commit_preview(another.preview_id, another.preview_fingerprint, session_id="owner")


def test_uncertain_boundary_requires_an_explicit_correction(service):
    from chat_to_howto.domain.content_imports import ImageUpload, UnitEdit

    importer, _, _ = service
    importer._ocr = lambda _: (_line("裁切在顶部的正文，是否完整未知", 0.13), _line("可确认的正文", 0.45))
    preview = importer.create_preview(images=(ImageUpload(filename="crop.png", payload=_png(), input_order=0),), pasted_note=None, session_id="owner")
    assert preview.status == "boundary_uncertain"
    assert "裁切在顶部" not in str(preview.units)
    with pytest.raises(ValueError):
        importer.commit_preview(preview.preview_id, preview.preview_fingerprint, session_id="owner")
    corrected = importer.revise_preview(preview.preview_id, tuple(UnitEdit(unit_id=unit.unit_id, kind="note_passage", group_id="note-1", text=unit.text, sender_name=None, sent_at=None, order=unit.order) for unit in preview.units), session_id="owner")
    assert corrected.status == "ready"
    assert importer.commit_preview(corrected.preview_id, corrected.preview_fingerprint, session_id="owner").unit_count == 1


def test_oversized_image_and_invalid_metadata_rejected(service):
    from chat_to_howto.domain.content_imports import ImageUpload, UnitEdit

    importer, _, _ = service
    with pytest.raises(ValueError):
        importer.create_preview(images=(ImageUpload(filename="screen.png", payload=_png() + b"x" * (15 * 1024 * 1024), input_order=0),), pasted_note=None, session_id="owner")
    preview = importer.create_preview(images=None, pasted_note="正文", session_id="owner")
    unit = preview.units[0]
    with pytest.raises(ValueError):
        importer.revise_preview(preview.preview_id, (UnitEdit(unit_id=unit.unit_id, kind="note_passage", group_id="note-1", text="正文", sender_name="伪作者", sent_at=None, order=0),), session_id="owner")
    with pytest.raises(ValueError, match="content_unit_limit_exceeded"):
        importer.create_preview(images=None, pasted_note="x\n\n" * 2_001, session_id="owner")


def test_image_order_and_groups_can_be_corrected_before_commit(service):
    from chat_to_howto.domain.content_imports import ImageOrderEdit, ImageUpload, UnitEdit

    importer, repository, _ = service
    first = _png()
    second = _png() + b"second"
    preview = importer.create_preview(images=(
        ImageUpload(filename="first.png", payload=first, input_order=0),
        ImageUpload(filename="second.png", payload=second, input_order=1),
    ), pasted_note=None, session_id="owner")
    edits = tuple(UnitEdit(unit_id=unit.unit_id, kind="note_passage", group_id="one-note", text=unit.text, sender_name=None, sent_at=None, order=index) for index, unit in enumerate(preview.units))
    revised = importer.revise_preview(preview.preview_id, edits, session_id="owner", image_orders=(ImageOrderEdit(image_ref="image-0", input_order=1), ImageOrderEdit(image_ref="image-1", input_order=0)))
    batch = importer.commit_preview(revised.preview_id, revised.preview_fingerprint, session_id="owner")
    assert [image.sha256 for image in sorted(batch.images, key=lambda item: item.input_order)] == [sha256(second).hexdigest(), sha256(first).hexdigest()]
    assert {unit["group_id"] for unit in repository.list_units(batch.batch_id, session_digest=digest("owner"))} == {"one-note"}

def test_retained_same_owner_content_previews_commit_independently_and_default_replaces(service):
    importer,repository,_=service
    previews=[importer.create_preview(None,f'第{i}份笔记',session_id='owner',retain_pending=True) for i in range(2)]
    batches=[importer.commit_preview(p.preview_id,p.preview_fingerprint,session_id='owner') for p in previews]
    assert len({batch.batch_id for batch in batches})==2
    old=importer.create_preview(None,'旧笔记',session_id='owner',retain_pending=True)
    importer.create_preview(None,'替换笔记',session_id='owner')
    with pytest.raises(PermissionError):importer.commit_preview(old.preview_id,old.preview_fingerprint,session_id='owner')


def test_retained_same_owner_content_previews_count_toward_capacity(service):
    importer,_,_=service
    previews=[importer.create_preview(None,f'笔记{i}',session_id='owner',retain_pending=True) for i in range(8)]
    with pytest.raises(ValueError,match='preview_capacity_exceeded'):
        importer.create_preview(None,'第九份',session_id='owner',retain_pending=True)
    assert all(p.preview_id in importer._pending for p in previews)
    importer.create_preview(None,'替换',session_id='owner')
    assert len(importer._pending)==1


def test_retained_same_owner_content_previews_count_toward_byte_budget(service):
    from dataclasses import replace
    from types import SimpleNamespace
    importer,_,_=service
    first=importer.create_preview(None,'笔记',session_id='owner',retain_pending=True)
    class ExistingLargePayload:
        def __len__(self):return 128*1024*1024
    importer._pending[first.preview_id]=replace(importer._pending[first.preview_id],images=(SimpleNamespace(payload=ExistingLargePayload()),))
    with pytest.raises(ValueError,match='preview_capacity_exceeded'):
        importer.create_preview(None,'新笔记',session_id='owner',retain_pending=True)
    assert first.preview_id in importer._pending
