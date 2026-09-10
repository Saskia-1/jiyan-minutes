"""Independent source configuration and scoped, server-resolved consent."""

import pytest

from conftest import END, END_MS, PUBLIC_JOB_FIELDS, START, START_MS


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


def test_refresh_enumerates_upstream_again_and_does_not_reuse_cached_metadata(p0):
    p0.start()
    p0.bootstrap()
    p0.configure()
    p0.select()
    p0.factory.conversation_ids = ("group:fixture-C",)
    response = p0.request("GET", "/api/sources/qq/conversations")
    assert len(response.json()["conversations"]) == 1
    assert p0.factory.enumerations == 2


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
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    p0.factory.require_secret = True
    response = p0.submit(p0.select())
    assert response.status_code == 202
    report = p0.run(response.json()["job_id"])
    assert report["state"] == "complete"
    assert p0.factory.secret_reads == 1
    access, consent = p0.factory.secret_grant
    p0.privacy.public_only_sentinels.add(access.secret_reference)
    p0.allow_successful_artifacts()
    assert p0.secret_operation("secret_delete").status_code in (200, 204)
    with pytest.raises(SecretStoreError):
        p0.store.get(access=access, consent=consent)
    response = p0.request("GET", "/api/sources")
    assert next(item for item in response.json()["sources"] if item["source_id"] == "qq")["secret_configured"] is False


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
    challenge = p0.secret_challenge("secret_get")
    first = p0.secret_operation("secret_get", challenge=challenge)
    assert first.status_code in (200, 204)
    if first.status_code != 204:
        assert set(first.json()) <= {"authorized", "operation", "expires_at"}
    assert p0.secret_operation("secret_get", challenge=challenge).status_code in (403, 409)
    p0.factory.require_secret = True
    job = p0.submit(p0.select()).json()["job_id"]
    assert p0.run(job)["state"] == "complete"
    p0.allow_successful_artifacts()
    second = p0.submit(p0.select())
    if second.status_code == 202:
        assert p0.run(second.json()["job_id"])["state"] == "failed"
    else:
        assert second.status_code in (403, 409)
    assert p0.factory.secret_reads == 1


@pytest.mark.parametrize("change", ["account", "nonce", "expired"])
def test_confirmed_secret_get_grant_is_invalidated_before_adapter_access(p0, change):
    p0.start()
    p0.bootstrap()
    p0.configure()
    assert p0.secret_operation("secret_set", value=p0.token).status_code in (200, 204)
    assert p0.secret_operation("secret_get").status_code in (200, 204)
    if change == "account":
        p0.configure(account_id="fixture-account-2")
    elif change == "nonce":
        p0.configure()
    else:
        p0.clock.advance(minutes=6)
    p0.factory.require_secret = True
    response = p0.submit(p0.select())
    if response.status_code == 202:
        assert p0.run(response.json()["job_id"])["state"] == "failed"
    else:
        assert response.status_code in (403, 409)
    assert p0.factory.secret_reads == 0
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
