"""Independent source configuration and scoped, server-resolved consent."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from hashlib import sha256
import json
import re
from threading import Barrier, Event, Lock

import pytest

from conftest import END, END_MS, PUBLIC_JOB_FIELDS, START, START_MS


def _blocked_setup_options():
    sections = {
        "source_version": "Only the reviewed source version may be used.",
        "access_impact": "Initialization is unavailable after the Gate 1 verdict.",
        "scope_overread": "No source read may start while this blocker is active.",
        "storage_cleanup": "Cleanup remains available for existing local state.",
        "permissions_network": "No blocked source process or network service may start.",
        "action_stop": "All new source access stops at the server boundary.",
    }
    return {
        "approved_output_roots": [
            {"output_root_id": "fixture-root", "label": "Fixture output"},
            {"output_root_id": "fixture-root-2", "label": "Second fixture output"},
        ],
        "source_disclosures": {
            "qq": {
                "revision": "ultra-gate-1-no-go-1",
                "sections": sections,
                "blockers": ["QCE is blocked by the Ultra Gate 1 NO-GO verdict."],
            },
            "wechat": {
                "revision": "ultra-gate-1-no-go-1",
                "sections": sections,
                "blockers": ["TraceMemo is blocked by the Ultra Gate 1 NO-GO verdict."],
            },
        },
    }


def _seed_blocked_source_and_selection(p0, source_id="qq"):
    base_url = "http://127.0.0.1:" + ("40653" if source_id == "qq" else "6131")
    p0.database.configure_source(
        {
            "source_id": source_id,
            "account_id": "fixture-account",
            "base_url": base_url,
            "client_version": "fixture-client-1",
            "tool_version": "fixture-tool-1",
            "account_version": "fixture-account-1",
            "nonce": "blocked-source-nonce",
            "status": "needs_user_action",
            "secret_ref": "blocked-source-secret-ref",
        }
    )
    session_row = p0.database.one("SELECT token_hash FROM sessions")
    p0.database.insert_selection(
        handle_hash=sha256(b"existing-blocked-selection").hexdigest(),
        session_hash=session_row["token_hash"],
        source_id=source_id,
        account_id="fixture-account",
        conversation_id="group:fixture-A",
        expires_at="2099-01-01T00:00:00+00:00",
        nonce="blocked-source-nonce",
    )


def test_gate1_blockers_report_blocked_and_reject_new_source_configuration(p0):
    p0.start(setup_options=_blocked_setup_options())
    p0.bootstrap()

    sources = p0.request("GET", "/api/sources").json()["sources"]
    response = p0.request(
        "POST",
        "/api/sources",
        json={
            "source_id": "qq",
            "account_id": "fixture-account",
            "base_url": "http://127.0.0.1:40653",
            "client_version": "fixture-client-1",
            "tool_version": "fixture-tool-1",
            "account_version": "fixture-account-1",
        },
    )

    assert {item["source_id"]: item["status"] for item in sources} == {
        "qq": "blocked",
        "wechat": "blocked",
    }
    assert response.status_code == 409
    assert p0.database.rows("SELECT source_id FROM sources") == []
    assert p0.factory.enumerations == 0


def test_gate1_blockers_deny_access_but_keep_cleanup_available(p0):
    p0.start(setup_options=_blocked_setup_options())
    p0.bootstrap()
    _seed_blocked_source_and_selection(p0)
    selection = {
        "source_id": "qq",
        "account_id": "fixture-account",
        "conversation_selection_id": "existing-blocked-selection",
        "range_start": START,
        "range_end": END,
        "output_root_id": "fixture-root",
    }
    disclosure = p0.request("GET", "/api/setup-options").json()[
        "source_disclosures"
    ]["qq"]

    assert p0.request("GET", "/api/sources/qq/conversations").status_code == 409
    assert p0.request(
        "POST",
        "/api/sources/qq/verification-consents",
        json={"selection": selection, "timezone": "Asia/Shanghai"},
    ).status_code == 409
    assert p0.request(
        "POST",
        "/api/sources/qq/verification-jobs",
        json={
            "selection": selection,
            "timezone": "Asia/Shanghai",
            "confirmed": True,
            "consent_challenge": "unissued-challenge",
            "disclosure_revision": disclosure["revision"],
            "disclosure_hash": disclosure["hash"],
        },
    ).status_code == 409
    for operation in ("secret_set", "secret_get"):
        assert p0.request(
            "POST",
            "/api/sources/qq/secret-consents",
            json={"operation": operation, "account_id": "fixture-account"},
        ).status_code == 409

    export_response = p0.request(
        "POST",
        "/api/export-jobs/consents/from-selections",
        json={
            "selection": {
                "source_id": "qq",
                "account_id": "fixture-account",
                "conversation_selection_ids": ["existing-blocked-selection"],
                "range_start": START,
                "range_end": END,
                "output_root_id": "fixture-root",
                "display_timezone": "Asia/Shanghai",
            },
            "confirmed": True,
        },
    )
    assert export_response.status_code == 403
    assert p0.factory.enumerations == 0
    assert p0.factory.reads == []
    assert p0.store.mutations == 0

    delete_challenge = p0.secret_challenge("secret_delete")
    assert p0.secret_operation(
        "secret_delete", challenge=delete_challenge
    ).status_code == 204
    assert p0.request(
        "POST", "/api/sources/qq/disconnect", json={"confirmed": True}
    ).status_code == 204



def _parallel_post(p0, path, bodies):
    """Issue two authenticated requests without mutating the shared cookie jar."""
    from fastapi.testclient import TestClient

    if isinstance(bodies, dict):
        bodies = (bodies, bodies)
    session_cookie = p0.client.cookies.get("p0_session")
    headers = {
        "Origin": "http://127.0.0.1:8765",
        "X-CSRF-Token": p0.csrf,
        "Cookie": f"p0_session={session_cookie}",
    }
    with ExitStack() as stack:
        clients = [
            stack.enter_context(
                TestClient(
                    p0.app,
                    base_url="http://127.0.0.1:8765",
                    raise_server_exceptions=False,
                )
            )
            for _ in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.post, path, headers=headers, json=body)
                for client, body in zip(clients, bodies, strict=True)
            ]
            responses = [future.result() for future in futures]
    for response in responses:
        p0.privacy.payloads.extend((response.text, list(response.headers.multi_items())))
    return responses


def test_empty_business_database_can_enumerate_duplicate_names_without_reading_messages(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    response = p0.request("GET", "/api/sources/qq/conversations")
    assert response.status_code == 200
    conversations = response.json()["conversations"]
    assert len(conversations) == 2
    assert conversations[0]["display_name"] == conversations[1]["display_name"]
    handles = [item["conversation_selection_id"] for item in conversations]
    assert len(set(handles)) == 2
    for item in conversations:
        assert set(item) <= {"conversation_selection_id", "display_name", "conversation_type", "account_id"}
    assert not any(identifier in response.text for identifier in p0.factory.conversation_ids)
    assert p0.factory.reads == []
    sources = p0.request("GET", "/api/sources").json()["sources"]
    assert all(source["status"] != "ready" for source in sources)


def test_conversation_handle_collision_is_retried_without_overwriting_a_prior_mapping(
    p0, monkeypatch
):
    import chat_to_howto.main as main_module

    p0.start()
    p0.bootstrap()
    p0.configure()
    generated = iter(("collision-safe-handle", "collision-safe-handle", "second-safe-handle"))
    monkeypatch.setattr(main_module, "opaque", lambda: next(generated))

    response = p0.request("GET", "/api/sources/qq/conversations")

    assert response.status_code == 200
    handles = [item["conversation_selection_id"] for item in response.json()["conversations"]]
    assert handles == ["selection:collision-safe-handle", "selection:second-safe-handle"]
    assert len(p0.database.rows("SELECT handle_hash FROM selections")) == 2


def test_conversation_handles_always_match_downstream_opaque_contract(p0, monkeypatch):
    import chat_to_howto.main as main_module

    p0.start()
    p0.bootstrap()
    p0.configure()
    generated = iter(("_leading-underscore", "-leading-hyphen"))
    monkeypatch.setattr(main_module, "opaque", lambda: next(generated))

    response = p0.request("GET", "/api/sources/qq/conversations")

    assert response.status_code == 200
    handles = [item["conversation_selection_id"] for item in response.json()["conversations"]]
    assert handles == ["selection:_leading-underscore", "selection:-leading-hyphen"]
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item) for item in handles)


def test_refresh_enumerates_upstream_again_and_does_not_reuse_cached_metadata(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    p0.select()
    p0.factory.conversation_ids = ("group:fixture-C",)
    response = p0.request("GET", "/api/sources/qq/conversations")
    assert len(response.json()["conversations"]) == 1
    assert p0.factory.enumerations == 2


def test_configured_secret_conversation_list_requires_and_consumes_one_get_grant(p0):
    """Removing the server-side grant claim must expose or bypass the source token."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    p0.factory.require_secret = True

    denied = p0.request("GET", "/api/sources/qq/conversations")
    assert denied.status_code in (403, 409)
    assert p0.factory.secret_reads == 0
    assert p0.factory.enumerations == 0

    assert p0.secret_operation("secret_get").status_code == 204
    allowed = p0.request("GET", "/api/sources/qq/conversations")
    assert allowed.status_code == 200
    assert len(allowed.json()["conversations"]) == 2
    assert p0.factory.secret_reads == 1
    assert p0.factory.enumerations == 1

    replay = p0.request("GET", "/api/sources/qq/conversations")
    assert replay.status_code in (403, 409)
    assert p0.factory.secret_reads == 1
    assert p0.factory.enumerations == 1


@pytest.mark.parametrize(
    "invalid",
    ("expired", "wrong_session", "wrong_source", "wrong_account", "configuration", "csrf", "origin"),
)
def test_conversation_secret_grant_rejects_wrong_authority_before_adapter_access(
    p0, invalid
):
    """A grant mutation that crosses any authority field must never reach the adapter."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    assert p0.secret_operation("secret_get").status_code == 204
    p0.factory.require_secret = True
    path = "/api/sources/qq/conversations"
    arguments = {}

    if invalid == "expired":
        p0.clock.advance(minutes=6)
    elif invalid == "wrong_session":
        p0.client.cookies.clear()
        p0.bootstrap()
    elif invalid == "wrong_source":
        p0.configure("wechat")
        assert p0.secret_operation("secret_set", source_id="wechat", value=p0.token).status_code == 204
        path = "/api/sources/wechat/conversations"
    elif invalid == "wrong_account":
        arguments["params"] = {"account_id": "fixture-account-2"}
    elif invalid == "configuration":
        p0.configure(tool_version="fixture-tool-changed")
    elif invalid == "csrf":
        arguments["csrf"] = False
    else:
        arguments["headers"] = {"Origin": "https://evil.example"}

    response = p0.request("GET", path, **arguments)
    assert response.status_code in (403, 409)
    assert p0.factory.secret_reads == 0
    assert p0.factory.enumerations == 0


def test_configuring_qq_does_not_configure_wechat_and_responses_omit_private_metadata(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    response = p0.request("GET", "/api/sources")
    sources = {source["source_id"]: source for source in response.json()["sources"]}
    assert sources["wechat"]["status"] == "unconfigured"
    before_qq = sources["qq"]
    p0.configure("wechat")
    sources = {source["source_id"]: source for source in p0.request("GET", "/api/sources").json()["sources"]}
    assert sources["qq"] == before_qq
    for source in sources.values():
        assert set(source) <= {
            "source_id", "status", "account_id", "base_url", "client_version",
            "tool_version", "account_version", "error_code", "secret_configured",
        }
    assert "configuration_nonce" not in response.text
    assert "secret_reference" not in response.text


@pytest.mark.parametrize("field", [
    "database_key", "wechat_database_key", "upstream_path", "upstream_method",
    "api_token", "secret_reference", "memory_scan", "login", "source_ai", "monitor",
])
def test_ordinary_source_config_rejects_secret_and_generic_upstream_controls(p0, field):
    p0.start()
    p0.bootstrap()
    body = {
        "source_id": "qq", "account_id": "fixture-account",
        "base_url": "http://127.0.0.1:40653", **p0.factory.fingerprint.model_dump(),
        field: p0.token,
    }
    response = p0.request("POST", "/api/sources", json=body)
    assert response.status_code == 422
    assert p0.factory.reads == []


@pytest.mark.parametrize("base_url", [
    "https://evil.example", "http://user:token@127.0.0.1:40653",
    "http://127.0.0.1:40653/private", "http://127.0.0.1:40653?token=secret",
    "http://127.0.0.1:40653#token", "http://127.0.0.1:6131",
])
def test_source_configuration_accepts_only_its_settings_bound_literal_loopback_url(p0, base_url):
    p0.start()
    p0.bootstrap()
    body = {"source_id": "qq", "account_id": "fixture-account", "base_url": base_url, **p0.factory.fingerprint.model_dump()}
    response = p0.request("POST", "/api/sources", json=body)
    assert response.status_code == 422
    assert base_url not in response.text
    assert p0.database.one("SELECT * FROM sources WHERE source_id='qq'") is None


def test_reconfiguration_rotates_secret_identity_and_invalidates_all_stale_source_authority(p0):
    """Reusing a prior secret/handle/grant/job after any fresh nonce would cross configurations."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    assert p0.secret_operation("secret_get").status_code == 204
    selection = p0.select()
    unused_challenge = p0.challenge(selection)
    assert p0.secret_operation("secret_get").status_code == 204
    created = p0.submit(selection, p0.challenge(selection))
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    before = p0.database.one("SELECT * FROM sources WHERE source_id='qq'")
    assert before["secret_configured"] == 1

    p0.configure(tool_version="fixture-tool-2")

    after = p0.database.one("SELECT * FROM sources WHERE source_id='qq'")
    assert after["nonce"] != before["nonce"]
    assert after["secret_ref"] != before["secret_ref"]
    assert after["secret_configured"] == 0
    assert p0.database.rows("SELECT * FROM selections WHERE source_id='qq'") == []
    assert p0.database.rows("SELECT * FROM challenges WHERE source_id='qq'") == []
    assert p0.database.rows("SELECT * FROM grants WHERE source_id='qq'") == []
    job = p0.database.one("SELECT state,error_code FROM jobs WHERE job_id=?", (job_id,))
    assert job == {"state": "failed", "error_code": "source_reconfigured"}
    assert p0.submit(selection, unused_challenge).status_code in (403, 409)


def test_disconnect_clears_only_local_source_authority_without_contacting_upstream(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    p0.challenge(selection)
    enumerations = p0.factory.enumerations
    reads = tuple(p0.factory.reads)

    response = p0.request(
        "POST", "/api/sources/qq/disconnect", json={"confirmed": True}
    )

    assert response.status_code == 204
    assert p0.database.one("SELECT * FROM sources WHERE source_id='qq'") is None
    assert p0.database.rows("SELECT * FROM selections WHERE source_id='qq'") == []
    assert p0.database.rows("SELECT * FROM challenges WHERE source_id='qq'") == []
    assert p0.database.rows("SELECT * FROM grants WHERE source_id='qq'") == []
    assert p0.factory.enumerations == enumerations
    assert tuple(p0.factory.reads) == reads
    source = {item["source_id"]: item for item in p0.request("GET", "/api/sources").json()["sources"]}["qq"]
    assert source == {"source_id": "qq", "status": "unconfigured", "secret_configured": False}


def test_disconnect_requires_secret_deletion_through_the_existing_consent_flow(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204

    blocked = p0.request("POST", "/api/sources/qq/disconnect", json={"confirmed": True})
    assert blocked.status_code == 409
    assert p0.database.one("SELECT * FROM sources WHERE source_id='qq'") is not None

    assert p0.secret_operation("secret_delete").status_code == 204
    assert p0.request("POST", "/api/sources/qq/disconnect", json={"confirmed": True}).status_code == 204


def test_disconnect_rejects_missing_confirmation_and_active_verification_job(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.request("POST", "/api/sources/qq/disconnect", json={"confirmed": False}).status_code == 403
    selection = p0.select()
    assert p0.submit(selection, p0.challenge(selection)).status_code == 202

    response = p0.request("POST", "/api/sources/qq/disconnect", json={"confirmed": True})

    assert response.status_code == 409
    assert p0.database.one("SELECT * FROM sources WHERE source_id='qq'") is not None


@pytest.mark.parametrize("missing", [
    "source_id", "account_id", "conversation_selection_id", "range_start", "range_end", "output_root_id",
])
def test_each_of_six_visible_selection_fields_is_required(p0, missing):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    del selection[missing]
    response = p0.request("POST", "/api/sources/qq/verification-consents", json={"selection": selection, "timezone": "Asia/Shanghai"})
    assert response.status_code == 422
    assert p0.factory.reads == []


@pytest.mark.parametrize("changes", [
    {"conversation_id": "group:fixture-A"}, {"output_path": "/tmp/unapproved"},
    {"output_root_id": "unapproved-root"}, {"range_start": END},
    {"range_end": START}, {"range_start": "not-a-date"},
    {"conversation_selection_id": ["one", "two"]},
])
def test_consent_rejects_raw_ids_paths_empty_ranges_and_multiple_conversations(p0, changes):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = {**p0.select(), **changes}
    response = p0.request("POST", "/api/sources/qq/verification-consents", json={"selection": selection, "timezone": "Asia/Shanghai"})
    assert response.status_code in (400, 403, 422)
    assert p0.factory.reads == []


def test_unknown_timezone_is_refused_before_upstream_read(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    response = p0.request("POST", "/api/sources/qq/verification-consents", json={
        "selection": p0.select(), "timezone": "Mars/Olympus",
    })
    assert response.status_code in (400, 422)
    assert p0.factory.reads == []


@pytest.mark.parametrize("change", ["source", "account", "conversation", "range", "timezone", "output_root"])
def test_consent_challenge_cannot_authorize_a_different_scope(p0, change):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    challenge = p0.challenge(selection)
    changes = {}
    if change == "source":
        p0.configure("wechat")
        selection = p0.select("wechat")
    elif change == "account":
        # Both accounts are valid; failure must be scope mismatch, not an unknown account.
        selection = p0.select(account_id="fixture-account-2")
    elif change == "conversation":
        selection = p0.select(index=1)
    elif change == "range":
        selection["range_end"] = "2026-09-01T00:00:01+00:00"
    elif change == "timezone":
        changes["timezone"] = "UTC"
    else:
        selection["output_root_id"] = "fixture-root-2"
    response = p0.submit(selection, challenge, **changes)
    assert response.status_code in (400, 403, 409, 422)
    assert p0.factory.reads == []


def test_verification_challenge_is_transactionally_single_use_under_concurrency(p0, monkeypatch):
    """A read-then-update implementation can create two jobs from one consent challenge."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    challenge = p0.challenge(selection)
    original_one = p0.database.one
    rendezvous = Barrier(2)

    def synchronize_stale_challenge_reads(query, values=()):
        row = original_one(query, values)
        if query.startswith("SELECT * FROM challenges WHERE challenge_hash="):
            rendezvous.wait(timeout=2)
        return row

    monkeypatch.setattr(p0.database, "one", synchronize_stale_challenge_reads)
    body = {
        "selection": selection,
        "timezone": "Asia/Shanghai",
        "consent_challenge": challenge,
        "confirmed": True,
        **p0.confirmation(challenge),
    }
    responses = _parallel_post(p0, "/api/sources/qq/verification-jobs", body)
    assert sorted(response.status_code for response in responses).count(202) == 1
    assert len(p0.database.rows("SELECT job_id FROM jobs")) == 1


@pytest.mark.parametrize("version", ["client_version", "tool_version", "account_version", "configuration_nonce"])
def test_configuration_or_version_change_invalidates_previous_handle_and_consent(p0, version):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    challenge = p0.challenge(selection)
    p0.configure(**({} if version == "configuration_nonce" else {version: "changed-version"}))
    response = p0.submit(selection, challenge)
    assert response.status_code in (403, 409, 422)
    assert p0.factory.reads == []


@pytest.mark.parametrize("rejection", ["declined", "expired", "other_session", "wrong_operation"])
def test_unconfirmed_expired_or_cross_session_challenge_cannot_create_a_job(p0, rejection):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    challenge = p0.challenge(selection)
    changes = {}
    if rejection == "declined":
        changes["confirmed"] = False
    elif rejection == "expired":
        p0.clock.advance(hours=1)
    elif rejection == "other_session":
        p0.client.cookies.clear()
        p0.bootstrap()
    else:
        changes["operation"] = "secret_get"
    response = p0.submit(selection, challenge, **changes)
    assert response.status_code in (400, 401, 403, 409, 422)
    assert p0.factory.reads == []


def test_selection_handle_cannot_be_replayed_by_another_session(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    p0.client.cookies.clear()
    p0.bootstrap()
    response = p0.request("POST", "/api/sources/qq/verification-consents", json={"selection": selection, "timezone": "Asia/Shanghai"})
    assert response.status_code in (403, 409, 422)


def test_selection_handle_expires_while_session_is_still_valid(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    # Configured factory defaults: selection TTL 5 min, session TTL 30 min.
    p0.clock.advance(minutes=6)
    assert p0.request("GET", "/api/sources").status_code == 200
    response = p0.request("POST", "/api/sources/qq/verification-consents", json={"selection": selection, "timezone": "Asia/Shanghai"})
    assert response.status_code in (403, 409)
    assert p0.factory.reads == []


def test_scoped_secret_set_get_and_delete_keep_value_server_side(p0):
    from chat_to_howto.security.secrets import SecretStoreError

    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code in (200, 204)
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    response = p0.submit(selection)
    assert response.status_code == 202
    report = p0.run(response.json()["job_id"])
    assert report["state"] == "complete"
    assert p0.factory.secret_reads == 2
    access, consent = p0.factory.secret_grant
    p0.privacy.public_only_sentinels.add(access.secret_reference)
    p0.allow_successful_artifacts()
    assert p0.secret_operation("secret_delete").status_code in (200, 204)
    with pytest.raises(SecretStoreError):
        p0.store.get(access=access, consent=consent)
    response = p0.request("GET", "/api/sources")
    assert next(item for item in response.json()["sources"] if item["source_id"] == "qq")["secret_configured"] is False


def test_secret_access_scope_uses_the_injected_store_backend_not_a_literal(p0):
    """Hard-coding memory denies a Keychain-like injected backend before it can authorize."""
    p0.store.backend = "fake-keychain"
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code == 204
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code == 204
    created = p0.submit(selection)
    assert created.status_code == 202
    report = p0.run(created.json()["job_id"])
    p0.allow_single_private_artifact_if_present()
    assert report["state"] == "complete"
    access, _ = p0.factory.secret_grant
    assert access.secret_backend == "fake-keychain"
    p0.privacy.public_only_sentinels.add(access.secret_reference)
    p0.allow_successful_artifacts()


def test_secret_challenge_is_claimed_before_a_failing_side_effect(p0, monkeypatch):
    """A storage failure must not leave the already-attempted authority replayable."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    challenge = p0.secret_challenge("secret_set")
    original_set = p0.store.set

    def fail_once(**_kwargs):
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(p0.store, "set", fail_once)
    assert p0.secret_operation("secret_set", challenge=challenge, value=p0.token).status_code == 403
    monkeypatch.setattr(p0.store, "set", original_set)
    assert p0.secret_operation("secret_set", challenge=challenge, value=p0.token).status_code in (403, 409)
    assert p0.store.mutations == 0


def test_secret_challenge_is_transactionally_single_use_under_concurrency(p0, monkeypatch):
    """Only one caller may cross the side-effect boundary for one secret challenge."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    challenge = p0.secret_challenge("secret_set")
    original_set = p0.store.set
    release = Event()
    lock = Lock()
    entrants = 0

    def slow_set(**kwargs):
        nonlocal entrants
        with lock:
            entrants += 1
            if entrants == 2:
                release.set()
        release.wait(timeout=0.25)
        original_set(**kwargs)

    monkeypatch.setattr(p0.store, "set", slow_set)
    body = {
        "operation": "secret_set",
        "account_id": "fixture-account",
        "consent_challenge": challenge,
        "confirmed": True,
        "value": p0.token,
        **p0.confirmation(challenge),
    }
    responses = _parallel_post(p0, "/api/sources/qq/secret-operations", body)
    assert sorted(response.status_code for response in responses) == [204, 403]
    assert p0.store.mutations == 1


def test_secret_get_grant_cannot_cross_the_authorizing_browser_session(p0):
    """A server-side token grant belongs to the local session that confirmed it."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code == 204
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code == 204
    challenge = p0.challenge(selection)
    p0.client.cookies.clear()
    p0.bootstrap()
    created = p0.submit(selection, challenge)
    if created.status_code == 202:
        report = p0.run(created.json()["job_id"])
        p0.allow_single_private_artifact_if_present()
        assert report["state"] == "failed"
    else:
        assert created.status_code in (403, 409)
    assert p0.factory.secret_reads == 1


def test_one_secret_get_grant_binds_atomically_to_only_one_job(p0):
    """Two fresh export consents still cannot fan one secret grant out to two jobs."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code == 204
    response = p0.request("GET", "/api/sources/qq/conversations")
    assert response.status_code == 200
    conversations = response.json()["conversations"]
    first_selection = {
        "source_id": "qq", "account_id": "fixture-account",
        "conversation_selection_id": conversations[0]["conversation_selection_id"],
        "range_start": START, "range_end": END, "output_root_id": "fixture-root",
    }
    second_selection = {
        **first_selection,
        "conversation_selection_id": conversations[1]["conversation_selection_id"],
    }
    assert p0.secret_operation("secret_get").status_code == 204
    payloads = [
        (lambda challenge: {
            "selection": selection,
            "timezone": "Asia/Shanghai",
            "consent_challenge": challenge,
            "confirmed": True,
            **p0.confirmation(challenge),
        })(p0.challenge(selection))
        for selection in (first_selection, second_selection)
    ]
    responses = _parallel_post(p0, "/api/sources/qq/verification-jobs", payloads)
    assert sorted(response.status_code for response in responses).count(202) == 1
    assert len(p0.database.rows("SELECT job_id FROM jobs")) == 1


@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
def test_job_bound_secret_grant_is_never_reusable_after_failure_or_cancellation(p0, terminal):
    """Binding, not successful completion, is the one-use point for adapter authority."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code == 204
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code == 204
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code == 204
    first = p0.submit(selection)
    assert first.status_code == 202
    first_job = first.json()["job_id"]
    if terminal == "cancelled":
        assert p0.request("POST", f"/api/sources/qq/verification-jobs/{first_job}/cancel").status_code == 200
    else:
        p0.factory.failure = "synthetic failure"
        assert p0.run(first_job)["state"] == "failed"
        p0.factory.failure = None

    second = p0.submit(selection)
    if second.status_code == 202:
        report = p0.run(second.json()["job_id"])
        p0.allow_single_private_artifact_if_present()
        assert report["state"] == "failed"
    else:
        assert second.status_code in (403, 409)
    assert not list(p0.output_root.rglob("*.jsonl"))


@pytest.mark.parametrize("operation", ["secret_set", "secret_get", "secret_delete"])
@pytest.mark.parametrize("invalid", ["missing", "expired", "wrong_operation", "declined", "client_version", "tool_version", "account_version", "account", "nonce"])
def test_secret_operations_require_matching_live_operation_and_configuration_consent(p0, operation, invalid):
    p0.start()
    p0.bootstrap()
    p0.configure()
    # Deletion and GET must have a real stored synthetic value to guard.
    assert p0.secret_operation("secret_set", value=p0.token).status_code in (200, 204)
    challenge = p0.secret_challenge(operation)
    mutations_before = p0.store.mutations
    confirmed = True
    if invalid == "missing":
        challenge = ""
    elif invalid == "expired":
        p0.clock.advance(minutes=6)
    elif invalid == "wrong_operation":
        challenge = p0.secret_challenge("secret_delete" if operation != "secret_delete" else "secret_get")
    elif invalid == "declined":
        confirmed = False
    elif invalid == "account":
        p0.configure(account_id="fixture-account-2")
    elif invalid == "nonce":
        p0.configure()
    else:
        p0.configure(**{invalid: "fixture-changed"})
    response = p0.secret_operation(operation, challenge=challenge, confirmed=confirmed, value=p0.token if operation == "secret_set" else None)
    assert response.status_code in (400, 403, 409, 422)
    assert p0.factory.secret_reads == 0
    assert p0.store.mutations == mutations_before


def test_secret_get_grant_is_single_use_and_not_returned_to_browser(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code in (200, 204)
    p0.factory.require_secret = True
    challenge = p0.secret_challenge("secret_get")
    first = p0.secret_operation("secret_get", challenge=challenge)
    assert first.status_code in (200, 204)
    if first.status_code != 204:
        assert set(first.json()) <= {"authorized", "operation", "expires_at"}
    assert p0.secret_operation("secret_get", challenge=challenge).status_code in (403, 409)
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code == 204
    job = p0.submit(selection).json()["job_id"]
    assert p0.run(job)["state"] == "complete"
    p0.allow_successful_artifacts()
    second = p0.submit(selection)
    if second.status_code == 202:
        assert p0.run(second.json()["job_id"])["state"] == "failed"
    else:
        assert second.status_code in (403, 409)
    assert p0.factory.secret_reads == 2


@pytest.mark.parametrize("change", ["account", "nonce", "expired"])
def test_confirmed_secret_get_grant_is_invalidated_before_adapter_access(p0, change):
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code in (200, 204)
    p0.factory.require_secret = True
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    selection = p0.select()
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    challenge = p0.challenge(selection)
    if change == "account":
        p0.configure(account_id="fixture-account-2")
    elif change == "nonce":
        p0.configure()
    else:
        p0.clock.advance(minutes=6)
    response = p0.submit(selection, challenge)
    if response.status_code == 202:
        assert p0.run(response.json()["job_id"])["state"] == "failed"
    else:
        assert response.status_code in (403, 409)
    assert p0.factory.secret_reads == 1
    assert not list(p0.output_root.rglob("*.jsonl"))


def test_duplicate_name_second_selection_reads_only_approved_stable_id_and_range(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select(index=1)
    response = p0.submit(selection)
    assert response.status_code == 202
    report = p0.run(response.json()["job_id"])
    assert p0.factory.reads == [("qq", "fixture-account", "group:fixture-B", START_MS, END_MS)]
    assert report["state"] == "complete"
    assert set(report) <= PUBLIC_JOB_FIELDS
    assert not any(identifier in str(report) for identifier in p0.factory.conversation_ids)
    assert "同名合成群" not in str(report)
    assert str(p0.output_root) not in str(report)
    p0.allow_successful_artifacts()


def test_public_job_lookup_cannot_cross_sources(p0):
    p0.start()
    job_id = p0.new_job()
    response = p0.request("GET", f"/api/sources/wechat/verification-jobs/{job_id}")
    assert response.status_code in (403, 404)


def _assert_sealed_disclosure(disclosure):
    asserted = dict(disclosure)
    observed_hash = asserted.pop("hash")
    encoded = json.dumps(
        asserted, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert observed_hash == sha256(encoded).hexdigest()
    assert len(observed_hash) == 64


def test_setup_disclosures_are_source_specific_and_cryptographically_sealed(p0):
    """A generic disclosure hides the materially different QCE and TraceMemo access risks."""
    p0.start()
    p0.bootstrap()

    response = p0.request("GET", "/api/setup-options")
    disclosures = response.json()["source_disclosures"]
    qq = disclosures["qq"]
    wechat = disclosures["wechat"]

    _assert_sealed_disclosure(qq)
    _assert_sealed_disclosure(wechat)
    assert qq["source_id"] == "qq" and qq["tool"] == "QCE"
    assert wechat["source_id"] == "wechat" and wechat["tool"] == "TraceMemo"
    assert "qce_local_history_may_be_incomplete" in qq["risks"]
    assert "tracememo_initialization_may_read_broader_database" in wechat["risks"]
    assert "tracememo_system_protection_change_is_blocked" in wechat["risks"]
    assert qq["hash"] != wechat["hash"]


def test_secret_consent_discloses_backend_scope_cleanup_and_source_specific_risk(p0):
    """A generic secret label cannot support informed approval of server-side token use."""
    p0.start()
    p0.bootstrap()
    p0.configure("wechat")

    response = p0.request(
        "POST",
        "/api/sources/wechat/secret-consents",
        json={"operation": "secret_set", "account_id": "fixture-account"},
    )
    disclosure = response.json()["disclosure"]

    _assert_sealed_disclosure(disclosure)
    assert disclosure["source_id"] == "wechat"
    assert disclosure["operation"] == "secret_set"
    assert disclosure["secret_backend"] == p0.store.backend
    assert set(disclosure["sections"]) == {"storage", "scope", "cleanup"}
    assert "source_api_token_stays_server_side" in disclosure["risks"]
    assert "wechat_database_key_is_not_stored_by_platform" in disclosure["risks"]
    assert p0.token not in response.text


def test_verification_confirmation_is_bound_to_returned_disclosure_revision_and_hash(p0):
    """Confirming a different disclosure revision must not consume export authority."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    issued = p0.request(
        "POST",
        "/api/sources/qq/verification-consents",
        json={"selection": selection, "timezone": "Asia/Shanghai"},
    ).json()

    response = p0.request(
        "POST",
        "/api/sources/qq/verification-jobs",
        json={
            "selection": selection,
            "timezone": "Asia/Shanghai",
            "consent_challenge": issued["consent_challenge"],
            "confirmed": True,
            "disclosure_revision": issued["disclosure"]["revision"],
            "disclosure_hash": "0" * 64,
        },
    )

    assert response.status_code == 403
    assert p0.database.rows("SELECT job_id FROM jobs") == []
    assert p0.factory.reads == []


def test_secret_confirmation_is_bound_to_returned_disclosure_revision_and_hash(p0):
    """A secret challenge cannot authorize a materially different storage disclosure."""
    p0.start()
    p0.bootstrap()
    p0.configure()
    issued = p0.request(
        "POST",
        "/api/sources/qq/secret-consents",
        json={"operation": "secret_set", "account_id": "fixture-account"},
    ).json()

    response = p0.request(
        "POST",
        "/api/sources/qq/secret-operations",
        json={
            "operation": "secret_set",
            "account_id": "fixture-account",
            "consent_challenge": issued["consent_challenge"],
            "confirmed": True,
            "value": p0.token,
            "disclosure_revision": issued["disclosure"]["revision"],
            "disclosure_hash": "f" * 64,
        },
    )

    assert response.status_code == 403
    assert p0.store.mutations == 0
