"""HTTP preview and commit contract for local user-supplied chat data."""

from __future__ import annotations

from base64 import b64encode

import pytest


def _request_body() -> dict[str, object]:
    payload = (
        "conversation,time,sender,text,id\n"
        "project,2026-09-13 10:20,A,import API searchable,m1\n"
    ).encode()
    return {
        "origin": "imported_file",
        "platform_hint": "qq",
        "filename": "chat.csv",
        "payload_base64": b64encode(payload).decode(),
        "options": {"timezone": "Asia/Shanghai"},
    }


def test_import_requires_preview_confirmation_then_uses_existing_search_flow(p0) -> None:
    p0.start()
    p0.bootstrap()

    preview = p0.request("POST", "/api/imports/previews", json=_request_body())

    assert preview.status_code == 201, preview.json()
    view = preview.json()
    assert view["ready_to_commit"] is True
    assert view["analysis"]["parsed_count"] == 1
    assert p0.app.state.chat_repository.message_count() == 0

    rejected = p0.request(
        "POST",
        f"/api/imports/previews/{view['preview_id']}/commit",
        json={"preview_fingerprint": "f" * 64, "confirmed": True},
    )
    assert rejected.status_code == 403
    assert p0.app.state.chat_repository.message_count() == 0

    committed = p0.request(
        "POST",
        f"/api/imports/previews/{view['preview_id']}/commit",
        json={
            "preview_fingerprint": view["preview_fingerprint"],
            "confirmed": True,
        },
    )
    assert committed.status_code == 201, committed.json()
    imported = committed.json()
    assert imported["source_id"] == "manual"
    assert p0.app.state.chat_repository.message_count() == 1

    searched = p0.request(
        "POST",
        "/api/search/from-selections",
        json={
            "source_id": imported["source_id"],
            "account_id": imported["account_id"],
            "query": "searchable",
            "conversation_selection_ids": [
                imported["conversations"][0]["conversation_selection_id"]
            ],
            "start_time": imported["range_start"],
            "end_time": imported["range_end"],
            "limit": 25,
        },
    )
    assert searched.status_code == 200, searched.json()
    assert len(searched.json()["results"]) == 1
    assert searched.json()["results"][0]["snapshot_id"] == imported["snapshot_id"]


def test_import_endpoint_rejects_missing_csrf_and_malformed_base64(p0) -> None:
    p0.start()
    p0.bootstrap()

    assert p0.request(
        "POST", "/api/imports/previews", csrf=False, json=_request_body()
    ).status_code == 401
    malformed = _request_body() | {"payload_base64": "not base64!"}
    response = p0.request("POST", "/api/imports/previews", json=malformed)

    assert response.status_code == 422
    assert p0.app.state.chat_repository.message_count() == 0


@pytest.mark.parametrize(
    "filename,payload,options,expected",
    [
        ("chat.exe", b"not executable", {"timezone": "UTC"}, "unsupported_format"),
        ("chat.csv", b"\xff", {"timezone": "UTC"}, "invalid_encoding"),
        ("chat.xlsx", b"not-a-zip", {"timezone": "UTC"}, "corrupt_container"),
        (
            "chat.csv", b"time,text\n2026-09-13 10:20,step\n",
            {"timezone": "UTC", "mapping": {"sent_at": "missing"}},
            "mapping_invalid",
        ),
        (
            "chat.txt", b"Alice: 09-09 21:59:54\nhello\n",
            {"timezone": "Asia/Shanghai", "default_conversation_name": "project"},
            "missing_default_year",
        ),
    ],
)
def test_preview_errors_are_allowlisted_and_actionable_without_echoing_input(
    p0, filename, payload, options, expected
) -> None:
    p0.start()
    p0.bootstrap()
    response = p0.request(
        "POST", "/api/imports/previews",
        json={
            "origin": "imported_file", "platform_hint": "generic",
            "filename": filename, "payload_base64": b64encode(payload).decode(),
            "options": options,
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": expected}}
    decoded = payload.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in response.text


def test_import_request_body_is_bounded_before_validation(p0) -> None:
    p0.start(import_request_max_bytes=512)
    p0.bootstrap()

    response = p0.request(
        "POST",
        "/api/imports/previews",
        json={
            "origin": "paste",
            "platform_hint": "generic",
            "pasted_text": "x" * 1_024,
            "options": {"timezone": "UTC"},
        },
    )

    assert response.status_code == 413
    assert p0.app.state.chat_repository.message_count() == 0


def test_same_file_with_different_options_stays_isolated_in_search(p0) -> None:
    p0.start()
    p0.bootstrap()

    committed = []
    for timezone_name in ("UTC", "Asia/Shanghai"):
        body = _request_body()
        body["options"] = {"timezone": timezone_name}
        preview = p0.request("POST", "/api/imports/previews", json=body)
        assert preview.status_code == 201, preview.json()
        view = preview.json()
        response = p0.request(
            "POST",
            f"/api/imports/previews/{view['preview_id']}/commit",
            json={"preview_fingerprint": view["preview_fingerprint"], "confirmed": True},
        )
        assert response.status_code == 201, response.json()
        committed.append(response.json())

    first = committed[0]
    searched = p0.request(
        "POST",
        "/api/search/from-selections",
        json={
            "source_id": "manual",
            "account_id": first["account_id"],
            "query": "searchable",
            "conversation_selection_ids": [
                first["conversations"][0]["conversation_selection_id"]
            ],
            "start_time": "2026-09-13T00:00:00Z",
            "end_time": "2026-09-13T12:00:00Z",
            "limit": 25,
        },
    )

    assert searched.status_code == 200, searched.json()
    assert len(searched.json()["results"]) == 1
    assert searched.json()["results"][0]["snapshot_id"] == first["snapshot_id"]


def test_identical_reimport_search_is_bound_to_the_selected_import_snapshot(p0) -> None:
    p0.start()
    p0.bootstrap()
    committed = []
    for _ in range(2):
        preview = p0.request("POST", "/api/imports/previews", json=_request_body()).json()
        response = p0.request(
            "POST", f"/api/imports/previews/{preview['preview_id']}/commit",
            json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True},
        )
        assert response.status_code == 201, response.json()
        committed.append(response.json())

    first = committed[0]
    searched = p0.request(
        "POST", "/api/search/from-selections",
        json={
            "source_id": "manual", "account_id": first["account_id"],
            "query": "searchable",
            "conversation_selection_ids": [first["conversations"][0]["conversation_selection_id"]],
            "start_time": first["range_start"], "end_time": first["range_end"],
            "limit": 25,
        },
    )

    assert searched.status_code == 200, searched.json()
    assert searched.json()["results"][0]["snapshot_id"] == first["snapshot_id"]
    assert searched.json()["results"][0]["batch_id"] == first["batch_id"]


def test_reload_recovers_paginated_imports_and_both_batches_can_be_deleted(p0) -> None:
    p0.start()
    p0.bootstrap()
    session_cookie = p0.client.cookies.get("p0_session")
    committed_ids = []
    for _ in range(2):
        preview = p0.request("POST", "/api/imports/previews", json=_request_body()).json()
        committed = p0.request(
            "POST", f"/api/imports/previews/{preview['preview_id']}/commit",
            json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True},
        )
        assert committed.status_code == 201, committed.json()
        committed_ids.append(committed.json()["import_id"])

    p0.clock.advance(minutes=31)
    p0.bootstrap()
    assert p0.client.cookies.get("p0_session") == session_cookie
    first = p0.request(
        "POST", "/api/imports/recovery", json={"cursor": None, "limit": 1}
    )
    assert first.status_code == 200, first.json()
    first_item = first.json()["imports"][0]
    first_plan = p0.request(
        "POST", "/api/deletion-plans",
        json={"target_kind": "batch", "target_id": first_item["batch_id"], "include_managed_files": False},
    )
    assert first_plan.status_code == 201, first_plan.json()
    first_deleted = p0.request(
        "POST", f"/api/deletion-plans/{first_plan.json()['plan_id']}/confirm",
        json={"fingerprint": first_plan.json()["fingerprint"], "confirmed": True},
    )
    assert first_deleted.status_code == 200, first_deleted.json()
    second = p0.request(
        "POST", "/api/imports/recovery",
        json={"cursor": first.json()["next_cursor"], "limit": 1},
    )
    assert second.status_code == 200, second.json()
    recovered = first.json()["imports"] + second.json()["imports"]
    assert {item["import_id"] for item in recovered} == set(committed_ids)

    for item in second.json()["imports"]:
        plan = p0.request(
            "POST", "/api/deletion-plans",
            json={"target_kind": "batch", "target_id": item["batch_id"], "include_managed_files": False},
        )
        assert plan.status_code == 201, plan.json()
        deleted = p0.request(
            "POST", f"/api/deletion-plans/{plan.json()['plan_id']}/confirm",
            json={"fingerprint": plan.json()["fingerprint"], "confirmed": True},
        )
        assert deleted.status_code == 200, deleted.json()
    assert p0.app.state.chat_repository.message_count() == 0


def test_expired_absolute_recovery_window_cannot_revive_old_import_authority(p0) -> None:
    p0.start(session_recovery_ttl_seconds=3600)
    p0.bootstrap()
    original_cookie = p0.client.cookies.get("p0_session")
    preview = p0.request("POST", "/api/imports/previews", json=_request_body()).json()
    committed = p0.request(
        "POST", f"/api/imports/previews/{preview['preview_id']}/commit",
        json={"preview_fingerprint": preview["preview_fingerprint"], "confirmed": True},
    )
    assert committed.status_code == 201, committed.json()

    p0.clock.advance(minutes=61)
    p0.bootstrap()

    assert p0.client.cookies.get("p0_session") != original_cookie
    recovered = p0.request(
        "POST", "/api/imports/recovery", json={"cursor": None, "limit": 20}
    )
    assert recovered.status_code == 200, recovered.json()
    assert recovered.json() == {"imports": [], "next_cursor": None}
