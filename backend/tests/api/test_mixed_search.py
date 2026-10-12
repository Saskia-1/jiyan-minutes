"""The new mixed search route does not weaken existing import ownership."""


def test_explicit_content_batch_is_searchable_only_by_owner(p0):
    p0.start()
    p0.bootstrap()
    preview = p0.request("POST", "/api/content-imports/previews", json={"pasted_note": "音乐方案\n\n个人笔记"}).json()
    batch = p0.request("POST", f"/api/content-imports/previews/{preview['preview_id']}/commit", json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}).json()
    query = {"source_refs": [{"kind": "content_batch", "id": batch["batch_id"]}], "include_undated": True, "terms": ["音乐", "方案"]}
    assert p0.request("POST", "/api/mixed-search", json=query, csrf=False).status_code == 401
    response = p0.request("POST", "/api/mixed-search", json=query)
    assert response.status_code == 200, response.text
    assert len(response.json()["hits"]) == 1
    assert response.json()["hits"][0]["source_kind"] == "note_passage"
    assert p0.request("POST", "/api/mixed-search", json=query | {"include_undated": False}).json()["hits"] == []
    assert p0.request("POST", "/api/mixed-search", json=query | {"source_refs": [{"kind": "content_batch", "id": "missing"}]}).status_code == 403
