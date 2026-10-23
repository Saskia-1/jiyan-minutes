"""Browser-facing content imports use the existing session and CSRF gate."""

from base64 import b64encode


def test_pasted_note_preview_edit_commit_and_recovery(p0):
    p0.start()
    p0.bootstrap()
    body = {"pasted_note": "第一段\n\n第二段"}
    assert p0.request("POST", "/api/content-imports/previews", csrf=False, json=body).status_code == 401
    preview_response = p0.request("POST", "/api/content-imports/previews", json=body)
    assert preview_response.status_code == 201, preview_response.text
    preview = preview_response.json()
    assert len(preview["units"]) == 2
    assert preview["suggested_kind"] == "note"

    first = preview["units"][0]
    edits = [
        {"unit_id": item["unit_id"], "kind": "note_passage", "group_id": "note-1",
         "text": item["text"] + " 校对", "sender_name": None, "sent_at": None, "order": index}
        for index, item in enumerate(preview["units"])
    ]
    updated = p0.request("PATCH", f"/api/content-imports/previews/{preview['preview_id']}", json={"edits": edits})
    assert updated.status_code == 200, updated.text
    assert updated.json()["preview_fingerprint"] != preview["preview_fingerprint"]
    stale = p0.request("POST", f"/api/content-imports/previews/{preview['preview_id']}/commit", json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True})
    assert stale.status_code == 403
    committed = p0.request("POST", f"/api/content-imports/previews/{preview['preview_id']}/commit", json={"preview_fingerprint": updated.json()["preview_fingerprint"], "confirmed": True})
    assert committed.status_code == 201, committed.text
    assert committed.json()["unit_count"] == 2
    recovered = p0.request("POST", "/api/content-imports/recovery", json={"limit": 10})
    assert recovered.status_code == 200
    assert recovered.json()["items"][0]["batch_id"] == committed.json()["batch_id"]
    assert first["text"] not in committed.text


def test_image_preview_only_exposes_body_and_image_access_is_owned(p0):
    from chat_to_howto.services.image_ocr import OcrLine

    p0.start()
    p0.bootstrap()
    p0.app.state.content_imports._ocr = lambda _: (
        OcrLine("关注", (0.1, 0.1, 0.4, 0.04), 0.9),
        OcrLine("保留正文", (0.1, 0.35, 0.6, 0.04), 0.9),
        OcrLine("第二行", (0.1, 0.48, 0.6, 0.04), 0.9),
        OcrLine("点赞 85", (0.1, 0.94, 0.4, 0.04), 0.9),
    )
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (200).to_bytes(4, "big") + (200).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00"
    preview = p0.request("POST", "/api/content-imports/previews", json={"images": [{"filename": "screenshot.png", "payload_base64": b64encode(payload).decode(), "input_order": 0}]})
    assert preview.status_code == 201, preview.text
    assert "关注" not in preview.text and "点赞" not in preview.text
    data = preview.json()
    committed = p0.request("POST", f"/api/content-imports/previews/{data['preview_id']}/commit", json={"preview_fingerprint": data["preview_fingerprint"], "confirmed": True})
    assert committed.status_code == 201, committed.text
    image_id = committed.json()["images"][0]["image_id"]
    fetched = p0.request("GET", f"/api/content-imports/images/{image_id}")
    assert fetched.status_code == 200 and fetched.content == payload
    assert p0.request("GET", "/api/content-imports/images/unknown").status_code == 404


def test_chat_correction_accepts_explicit_timestamp_without_fabricating_missing_one(p0):
    p0.start()
    p0.bootstrap()
    preview = p0.request("POST", "/api/content-imports/previews", json={"pasted_note": "第一段\n\n第二段"}).json()
    edits = [
        {"unit_id": item["unit_id"], "kind": "chat_message", "group_id": "chat-1",
         "text": item["text"], "sender_name": "甲" if index == 0 else None,
         "sent_at": "2026-10-12T10:00:00+08:00" if index == 0 else None, "order": index}
        for index, item in enumerate(preview["units"])
    ]
    revised = p0.request("PATCH", f"/api/content-imports/previews/{preview['preview_id']}", json={"edits": edits})
    assert revised.status_code == 200, revised.text
    assert revised.json()["units"][0]["sent_at"] == "2026-10-12T10:00:00+08:00"
    assert revised.json()["units"][1]["sent_at"] is None

def test_retain_pending_content_previews_commit_independently_and_flag_is_strict_bool(p0):
    p0.start();p0.bootstrap()
    for invalid in ['true',1,None]:
        response=p0.request('POST','/api/content-imports/previews',json={'pasted_note':'笔记','retain_pending':invalid})
        assert response.status_code==422
    previews=[]
    for index in range(2):
        response=p0.request('POST','/api/content-imports/previews',json={'pasted_note':f'第{index}份笔记','retain_pending':True})
        assert response.status_code==201,response.text
        previews.append(response.json())
    batches=[]
    for preview in previews:
        committed=p0.request('POST',f"/api/content-imports/previews/{preview['preview_id']}/commit",json={'preview_fingerprint':preview['preview_fingerprint'],'confirmed':True})
        assert committed.status_code==201,committed.text
        batches.append(committed.json()['batch_id'])
    assert len(set(batches))==2
