from .test_imports import _request_body


def test_expired_raw_is_cleaned_before_recovery_with_a_new_browser_session(p0):
    p0.start()
    p0.bootstrap()
    preview = p0.request("POST", "/api/imports/previews", json=_request_body()).json()
    committed = p0.request("POST", f"/api/imports/previews/{preview['preview_id']}/commit",
        json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True})
    assert committed.status_code == 201
    assert committed.json()["raw_expires_at_utc"]
    p0.clock.advance(days=7)
    p0.bootstrap()
    page = p0.request("POST", "/api/imports/recovery", json={"cursor": None, "limit": 20})
    assert page.status_code == 200 and page.json()["imports"] == []
    status = p0.request("GET", "/api/retention").json()
    assert status["cleaned_batches"] == 1 and status["pending_cleanups"] == 0
    assert p0.app.state.chat_repository.message_count() == 0
