"""P0 HTTP trust boundary; all traffic is in-process ASGI."""

from http.cookies import SimpleCookie
from hashlib import sha256
import sqlite3

import pytest

from conftest import ORIGIN


def test_legacy_session_migration_does_not_extend_existing_authority(tmp_path):
    from chat_to_howto.db import Database, P0_SCHEMA_VERSION

    path = tmp_path / "legacy-session.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                csrf_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            INSERT INTO sessions(token_hash, csrf_hash, expires_at)
            VALUES ('token-digest', 'csrf-digest', '2026-09-13T12:00:00+00:00');
            PRAGMA user_version=2;
            """
        )

    database = Database(path)
    row = database.one(
        "SELECT expires_at, recovery_expires_at FROM sessions WHERE token_hash=?",
        ("token-digest",),
    )

    assert row == {
        "expires_at": "2026-09-13T12:00:00+00:00",
        "recovery_expires_at": "2026-09-13T12:00:00+00:00",
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == P0_SCHEMA_VERSION


def test_zero_byte_database_migration_is_idempotent_and_sessions_are_hash_only(p0):
    p0.start()
    response = p0.bootstrap()
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    assert len(cookie) == 1
    session = next(iter(cookie.values()))
    assert session["httponly"]
    assert session["samesite"].lower() == "strict"
    assert session["domain"] == ""
    assert session["path"] == "/"
    assert set(response.json()) <= {"csrf_token", "expires_at"}
    csrf, session_value = p0.csrf, session.value
    p0.configure()
    p0.database.migrate()
    p0.database.migrate()
    sources = p0.request("GET", "/api/sources").json()["sources"]
    assert len(sources) == 2
    assert {source["source_id"] for source in sources} == {"qq", "wechat"}
    with sqlite3.connect(p0.database_path) as connection:
        dumped = "\n".join(connection.iterdump())
    p0.privacy.inspect(dumped, public=False)
    p0.privacy.inspect(p0.database_path.read_bytes(), public=False)
    assert p0.request("POST", "/api/sources", json={}).status_code != 401


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example:8765", "127.0.0.1:9999", "localhost:8765"])
def test_host_must_exactly_match_configured_loopback_authority(p0, host):
    p0.start()
    response = p0.request("GET", "/api/session", headers={"Host": host})
    assert response.status_code in (400, 403)


@pytest.mark.parametrize("origin", ["null", "https://evil.example", "http://127.0.0.1:9999", "http://127.0.0.1:8765.evil.example"])
def test_origin_is_not_a_prefix_or_loopback_only_check(p0, origin):
    p0.start()
    response = p0.request("GET", "/api/session", headers={"Origin": origin})
    assert response.status_code in (400, 403)
    assert "access-control-allow-origin" not in response.headers


def test_forwarded_headers_cannot_repair_a_host_or_origin_rejection(p0):
    p0.start()
    response = p0.request("GET", "/api/session", headers={
        "Host": "evil.example", "Origin": "https://evil.example",
        "Forwarded": 'for=127.0.0.1;host="127.0.0.1:8765";proto=http',
        "X-Forwarded-Host": "127.0.0.1:8765", "X-Forwarded-Proto": "http",
        "X-Forwarded-For": "127.0.0.1",
    })
    assert response.status_code in (400, 403)


def test_local_requests_ignore_untrusted_forwarded_authority(p0):
    p0.start()
    response = p0.request("GET", "/api/session", headers={"X-Forwarded-Host": "evil.example"})
    assert response.status_code == 200
    assert "evil.example" not in response.text


def test_browser_gets_allow_missing_origin_but_require_session_for_business_metadata_and_never_cache(p0):
    p0.start()
    headers = {"Host": "127.0.0.1:8765"}
    unauthenticated = p0.client.get("/api/sources", headers=headers)
    assert unauthenticated.status_code == 401
    bootstrap = p0.client.get("/api/session", headers=headers)
    assert bootstrap.status_code == 200
    assert bootstrap.headers["cache-control"] == "no-store"
    p0._capture_bootstrap(bootstrap)
    authenticated = p0.client.get("/api/sources", headers=headers)
    assert authenticated.status_code == 200
    assert authenticated.headers["cache-control"] == "no-store"
    hostile = p0.client.get("/api/sources", headers={**headers, "Origin": "https://evil.example"})
    assert hostile.status_code in (400, 403)


def test_built_frontend_can_bootstrap_without_exposing_business_api(p0, tmp_path):
    frontend = tmp_path / "frontend-dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>Chat to How-To</title>", encoding="utf-8")
    p0.start(frontend_dist=frontend)

    root = p0.client.get("/", headers={"Host": "127.0.0.1:8765"})
    assert root.status_code == 200
    assert "Chat to How-To" in root.text
    assert root.headers["cache-control"] == "no-store"
    assert p0.client.get("/api/sources", headers={"Host": "127.0.0.1:8765"}).status_code == 401
    assert p0.client.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert p0.client.post(
        "/", headers={"Host": "127.0.0.1:8765", "Origin": ORIGIN}
    ).status_code == 403


@pytest.mark.parametrize("path", [
    "/api/sources", "/api/sources/qq/verification-consents",
    "/api/sources/qq/verification-jobs", "/api/sources/qq/verification-jobs/unknown/cancel",
    "/api/sources/qq/secret-consents", "/api/sources/qq/secret-operations",
])
@pytest.mark.parametrize("missing", ["session", "csrf", "mismatched_csrf", "origin"])
def test_every_mutation_requires_live_session_and_matching_csrf_and_origin(p0, path, missing):
    p0.start()
    p0.bootstrap()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": p0.csrf}
    if missing == "session":
        p0.client.cookies.clear()
    elif missing == "csrf":
        del headers["X-CSRF-Token"]
    elif missing == "mismatched_csrf":
        headers["X-CSRF-Token"] = "not-the-session-csrf"
    else:
        del headers["Origin"]
    response = p0.client.post(path, headers=headers, json={})
    p0.privacy.payloads.extend((response.text, list(response.headers.multi_items())))
    assert response.status_code in (401, 403)


def test_session_expiry_revokes_mutation_access(p0):
    p0.start()
    p0.bootstrap()
    p0.clock.advance(days=1)
    assert p0.request("POST", "/api/sources", json={}).status_code in (401, 403)


def test_cors_preflight_does_not_grant_other_origins(p0):
    p0.start()
    response = p0.request("OPTIONS", "/api/sources", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-csrf-token",
    })
    assert response.status_code >= 400
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_validation_errors_never_echo_input_values_or_exception_details(p0):
    p0.start()
    p0.bootstrap()
    response = p0.request("POST", "/api/sources", json={"source_id": p0.token, "database_key": p0.text})
    assert response.status_code == 422
    assert set(response.json()) <= {"code", "message", "error_code"}
    p0.privacy.inspect(response.text)


def test_environment_response_exposes_only_safe_inventory_fields(p0):
    p0.start()
    p0.bootstrap()
    response = p0.request("GET", "/api/environment")
    assert response.status_code == 200
    assert set(response.json()) <= {
        "os_name", "os_version", "physical_architecture", "process_architecture",
        "rosetta_translated", "applications", "tools", "issues", "target_device",
        "os",
    }
    assert str(p0.settings.data_dir) not in response.text
    assert str(p0.workspace) not in response.text


@pytest.mark.parametrize("surface", ["metadata", "config", "consent", "job", "cancel", "secret_consent", "secret_operation"])
@pytest.mark.parametrize("attack", ["host", "origin", "forwarded_repair"])
def test_adversarial_authority_rejects_otherwise_valid_authenticated_requests_without_side_effects(p0, surface, attack):
    p0.start()
    p0.bootstrap()
    p0.configure()
    selection = p0.select()
    challenge = p0.challenge(selection)
    created = p0.submit(selection, challenge)
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    next_challenge = p0.challenge(selection)
    secret_challenge = p0.secret_challenge("secret_set")
    routes = {
        "metadata": ("GET", "/api/sources/qq/conversations", None),
        "config": ("POST", "/api/sources", {"source_id": "qq", "account_id": "fixture-account", "base_url": "http://127.0.0.1:40653", **p0.factory.fingerprint.model_dump()}),
        "consent": ("POST", "/api/sources/qq/verification-consents", {"selection": selection, "timezone": "Asia/Shanghai"}),
        "job": ("POST", "/api/sources/qq/verification-jobs", {"selection": selection, "timezone": "Asia/Shanghai", "consent_challenge": next_challenge, "confirmed": True}),
        "cancel": ("POST", f"/api/sources/qq/verification-jobs/{job_id}/cancel", None),
        "secret_consent": ("POST", "/api/sources/qq/secret-consents", {"operation": "secret_set", "account_id": "fixture-account"}),
        "secret_operation": ("POST", "/api/sources/qq/secret-operations", {"operation": "secret_set", "account_id": "fixture-account", "consent_challenge": secret_challenge, "confirmed": True, "value": p0.token}),
    }

    def database_digest():
        with sqlite3.connect(p0.database_path) as connection:
            return sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    before = (database_digest(), p0.factory.enumerations, len(p0.factory.reads), p0.store.mutations)
    headers = {"Host": "evil.example"} if attack == "host" else {"Origin": "https://evil.example"}
    if attack == "forwarded_repair":
        headers.update({"Host": "evil.example", "Forwarded": 'host="127.0.0.1:8765";proto=http', "X-Forwarded-Host": "127.0.0.1:8765", "X-Forwarded-Proto": "http"})
    method, path, body = routes[surface]
    response = p0.request(method, path, headers=headers, **({"json": body} if body is not None else {}))
    assert response.status_code in (400, 403)
    after = (database_digest(), p0.factory.enumerations, len(p0.factory.reads), p0.store.mutations)
    assert before == after
    assert not list(p0.output_root.iterdir())
