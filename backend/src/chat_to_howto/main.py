"""Loopback-only P0 application factory with injected, synthetic-safe adapters."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from chat_to_howto.api.documents import create_documents_router
from chat_to_howto.api.document_exports import create_document_exports_router
from chat_to_howto.api.deletion import create_deletion_router
from chat_to_howto.api.export_batches import create_export_batches_router
from chat_to_howto.api.export_jobs import (
    SelectionExportScopeRequest,
    create_export_jobs_router,
)
from chat_to_howto.api.generation import (
    SelectionGenerationRequest,
    create_generation_router,
)
from chat_to_howto.api.imports import create_imports_router
from chat_to_howto.api.content_imports import create_content_imports_router
from chat_to_howto.api.search import SelectionSearchRequest, create_search_router
from chat_to_howto.api.session import digest, opaque
from chat_to_howto.db import Database, P0_SCHEMA_VERSION
from chat_to_howto.domain.consent import ConsentRecord, ConsentScope, SecretAccessScope, SecretOperation
from chat_to_howto.domain.jobs import ExportConversation, ExportScope
from chat_to_howto.domain.generation import GenerationBudget
from chat_to_howto.domain.search import SearchRequest
from chat_to_howto.environment import KNOWN_APPLICATION_PATHS, collect_environment
from chat_to_howto.repositories.chat import ChatRepository, SnapshotRef
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.services.artifacts import ApprovedOutputRoot, ArtifactWriter
from chat_to_howto.services.documents import DocumentService
from chat_to_howto.services.document_export import DocumentExportService
from chat_to_howto.services.deletion import DeletionService
from chat_to_howto.services.export_jobs import (
    AuthorizationError,
    ExportJobOrchestrator,
    SourceBinding,
)
from chat_to_howto.services.importer import BatchImporter
from chat_to_howto.services.import_sessions import ChatImportService
from chat_to_howto.services.content_imports import ContentImportService
from chat_to_howto.services.image_ocr import recognize_image
from chat_to_howto.services.generation_cache import GenerationCacheSettings
from chat_to_howto.services.generation_jobs import (
    GenerationAuthorizationError,
    GenerationJobOrchestrator,
)
from chat_to_howto.services.generation_planning import (
    GenerationSelection,
)
from chat_to_howto.services.generation_prompts import PROMPT_VERSION
from chat_to_howto.services.p0_verifier import P0Verifier
from chat_to_howto.services.search import ChatSearchService
from chat_to_howto.services.topic_expansion import TopicExpansionService


_DISCLOSURE_SECTIONS = (
    "source_version", "access_impact", "scope_overread", "storage_cleanup",
    "permissions_network", "action_stop",
)
_DEFAULT_DISCLOSURE = {
    "revision": "p0-local-contract-1",
    "sections": {
        "source_version": "This source remains unverified until a real-device verification completes.",
        "access_impact": "Only the injected local adapter is used for this request.",
        "scope_overread": "The selected account, conversation, range, and approved output root bound this request.",
        "storage_cleanup": "Secrets stay server-side and verification artifacts remain private to the approved root.",
        "permissions_network": "No external service is contacted by this local setup endpoint.",
        "action_stop": "Decline, cancellation, or a verification error stops this request.",
    },
    "blockers": [],
}
_SOURCE_DISCLOSURE = {
    "qq": {
        "tool": "QCE",
        "risks": [
            "qce_local_history_may_be_incomplete",
            "qce_loopback_service_reads_selected_local_history",
        ],
    },
    "wechat": {
        "tool": "TraceMemo",
        "risks": [
            "tracememo_initialization_may_read_broader_database",
            "tracememo_system_protection_change_is_blocked",
            "tracememo_loopback_service_reads_selected_local_history",
        ],
    },
}
_SECRET_DISCLOSURE_REVISION = "p0-server-secret-contract-1"
_SECRET_DISCLOSURE_SECTIONS = {
    "storage": "The source credential stays in the configured server-side secret backend and is never returned to the browser.",
    "scope": "This approval is bound to one source, account, configuration, operation, browser session, and expiry.",
    "cleanup": "Delete revokes active grants and removes the credential through the configured secret backend.",
}


def create_app(*, settings, database: Database, adapter_factory, secret_store, clock,
               output_roots, source_root: Path, origin: str, run_jobs: bool = False,
               session_ttl_seconds: int = 1800, selection_ttl_seconds: int = 300,
               session_recovery_ttl_seconds: int = 7 * 24 * 60 * 60,
               consent_ttl_seconds: int = 300, setup_options=None,
               generation_provider_factory=None, frontend_dist: Path | None = None,
               import_request_max_bytes: int = 91_000_000) -> FastAPI:
    if import_request_max_bytes < 1:
        raise ValueError("import request byte limit must be positive")
    if (
        not isinstance(session_ttl_seconds, int)
        or isinstance(session_ttl_seconds, bool)
        or session_ttl_seconds < 1
        or not isinstance(session_recovery_ttl_seconds, int)
        or isinstance(session_recovery_ttl_seconds, bool)
        or session_recovery_ttl_seconds < session_ttl_seconds
    ):
        raise ValueError("session recovery lifetime must cover the positive session lifetime")
    root = Path(source_root).resolve()
    roots = {name: Path(path) for name, path in output_roots.items()}
    approved_base_urls = {"qq": settings.qce_base_url, "wechat": settings.tracememo_base_url}
    secret_backend = getattr(secret_store, "backend", None)
    if not isinstance(secret_backend, str) or not secret_backend:
        raise ValueError("secret store must identify its backend")
    for path in roots.values():
        if path.is_symlink() or not path.is_dir() or path.resolve().is_relative_to(root):
            raise ValueError("unsafe output root")
    setup_public = _public_setup_options(roots, setup_options)

    def source_is_blocked(source_id: str) -> bool:
        disclosure = setup_public["source_disclosures"].get(source_id)
        return bool(disclosure and disclosure["blockers"])

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.verifier = P0Verifier(database=database, adapter_factory=adapter_factory, output_roots=roots, clock=clock)
    app.router.add_event_handler("shutdown", app.state.verifier.close)

    chat_repository = ChatRepository(
        database.path.with_name(f"{database.path.stem}.chat.sqlite3")
    )
    approved_p1_roots = {
        root_id: ApprovedOutputRoot.approve(
            path, root_id=root_id, repository_root=root
        )
        for root_id, path in roots.items()
    }

    def p1_source_binding(scope, *, session_digest: str):
        if source_is_blocked(scope.source_id):
            raise PermissionError("source is blocked")
        source = database.one(
            "SELECT * FROM sources WHERE source_id=?", (scope.source_id,)
        )
        if source is None or any(
            source[field] != expected
            for field, expected in (
                ("account_id", scope.account_id),
                ("client_version", scope.client_version),
                ("tool_version", scope.tool_version),
                ("account_version", scope.account_version),
                ("nonce", scope.configuration_nonce),
            )
        ):
            raise PermissionError("source binding changed")
        adapter_grant = {}
        if source["secret_configured"]:
            access = _secret_access(source, secret_backend)
            current = clock().astimezone(timezone.utc)
            grant = database.claim_secret_grant_for_access(
                source=source,
                session_hash=session_digest,
                secret_backend=secret_backend,
                scope_fingerprint=access.fingerprint(),
                now=current,
            )
            if grant is None:
                raise PermissionError("source authorization is unavailable")
            consent = ConsentRecord(
                scope_fingerprint=grant["scope_fingerprint"],
                granted=True,
                recorded_at=datetime.fromisoformat(grant["recorded_at"]),
                expires_at=datetime.fromisoformat(grant["expires_at"]),
            )
            if not consent.matches(access, now=current):
                raise PermissionError("source authorization is unavailable")
            adapter_grant = {"token_access": access, "token_consent": consent}
        adapter = adapter_factory(scope.source_id, scope.account_id, **adapter_grant)
        source_type = (
            "synthetic"
            if getattr(adapter, "evidence_class", None) == "synthetic"
            else scope.source_id
        )
        if source_type not in {"qq", "wechat", "synthetic"}:
            raise ValueError("unsupported source type")
        return SourceBinding(
            adapter=adapter,
            source_id=scope.source_id,
            account_id=scope.account_id,
            source_account_namespace=scope.source_account_namespace,
            source_type=source_type,
            client_version=source["client_version"],
            tool_version=source["tool_version"],
            account_version=source["account_version"],
            configuration_nonce=source["nonce"],
        )

    def approved_p1_root(scope):
        approved = approved_p1_roots.get(scope.output_root_id)
        if approved is None:
            raise PermissionError("output root is not approved")
        return approved

    def index_snapshot(snapshot: SnapshotRef) -> None:
        chat_repository.snapshot_message_ids(snapshot.snapshot_id)

    export_orchestrator = ExportJobOrchestrator(
        repository=chat_repository,
        source_binding_factory=p1_source_binding,
        artifact_writer_factory=lambda scope: ArtifactWriter(approved_p1_root(scope)),
        importer_factory=lambda scope: BatchImporter(
            chat_repository, approved_p1_root(scope)
        ),
        indexer=index_snapshot,
        clock=clock,
    )
    export_orchestrator.reconcile_interrupted()
    chat_search = ChatSearchService(chat_repository)
    chat_imports = ChatImportService(
        repository=chat_repository,
        clock=clock,
        preview_ttl_seconds=selection_ttl_seconds,
        selection_ttl_seconds=session_ttl_seconds,
    )
    ocr_executable = Path(__file__).resolve().parents[3] / "backend/native/ocr/build/chat-image-ocr"
    content_imports = ContentImportService(
        repository=ContentRepository(chat_repository),
        image_root=database.path.with_name(f"{database.path.stem}.content-images"),
        clock=clock,
        ocr=lambda image: recognize_image(image, executable=ocr_executable, timeout_seconds=20),
        preview_ttl_seconds=selection_ttl_seconds,
    )
    def default_generation_provider(_provider_name: str):
        raise ValueError("generation provider is not configured")

    selected_generation_provider_factory = (
        generation_provider_factory or default_generation_provider
    )
    generation_cache_settings = GenerationCacheSettings(
        prompt_version=PROMPT_VERSION,
        output_schema_version="generation-envelope-v4-extensions",
        chunk_strategy_version="message-boundary-synthesis-v3-short-references",
        max_chunk_input_tokens=8_000,
        provider_behavior_revision=(
            f"{settings.llm_provider}-json-thinking-{settings.llm_reasoning_effort}-v1"
            if settings.llm_thinking == "enabled" else
            f"{settings.llm_provider}-json-nonthinking-t0-v3"
        ),
    )
    generation_orchestrator = GenerationJobOrchestrator(
        repository=chat_repository,
        provider_factory=selected_generation_provider_factory,
        cache_settings=generation_cache_settings,
        clock=clock,
        max_daily_provider_requests=settings.llm_daily_request_limit,
    )
    topic_expansion_service = TopicExpansionService(
        provider_factory=selected_generation_provider_factory,
        provider=settings.llm_provider,
        model=settings.llm_model,
    )
    generation_orchestrator.reconcile_interrupted()
    document_service = DocumentService(repository=chat_repository, clock=clock)
    document_export_service = DocumentExportService(
        document_service=document_service,
        repository=chat_repository,
        approved_roots=approved_p1_roots,
        clock=clock,
    )
    deletion_service = DeletionService(
        repository=chat_repository, approved_roots=approved_p1_roots, clock=clock
    )
    deletion_service.reconcile_cleanup()
    app.state.chat_repository = chat_repository
    app.state.export_orchestrator = export_orchestrator
    app.state.chat_search = chat_search
    app.state.chat_imports = chat_imports
    app.state.content_imports = content_imports
    app.state.generation_orchestrator = generation_orchestrator
    app.state.document_service = document_service
    app.state.document_export_service = document_export_service
    app.state.deletion_service = deletion_service
    app.router.add_event_handler("shutdown", chat_imports.close)
    app.router.add_event_handler("shutdown", content_imports.close)
    app.router.add_event_handler("shutdown", chat_repository.close)

    def now(): return clock().astimezone(timezone.utc)
    def error(status=403):
        response = JSONResponse(status_code=status, content={"code": "request_rejected", "message": "The request could not be accepted."})
        response.headers["Cache-Control"] = "no-store"
        return response
    def has_exact_host(request: Request) -> bool:
        return request.headers.get("host") == origin.removeprefix("http://")
    def session(request: Request, mutation=False):
        token = request.cookies.get("p0_session")
        if not token: return None
        row = database.one("SELECT * FROM sessions WHERE token_hash=?", (digest(token),))
        if not row or datetime.fromisoformat(row["expires_at"]) <= now(): return None
        if mutation:
            csrf = request.headers.get("x-csrf-token")
            if not csrf or not secrets_compare(digest(csrf), row["csrf_hash"]): return None
        return (digest(token), row)
    @app.middleware("http")
    async def gate(request: Request, call_next):
        request_origin = request.headers.get("origin")
        if not has_exact_host(request):
            return error()
        is_api = request.url.path == "/api" or request.url.path.startswith("/api/")
        if not is_api:
            if request.method not in {"GET", "HEAD"} or request_origin not in (None, "", origin):
                return error()
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        if request.method in {"GET", "HEAD"}:
            if request_origin not in (None, "", origin):
                return error()
            if request.url.path != "/api/session" and session(request) is None:
                return error(401)
        elif request_origin != origin:
            return error()
        elif session(request, mutation=True) is None:
            return error(401)
        if (
            request.method == "POST"
            and request.url.path in {"/api/imports/previews", "/api/content-imports/previews"}
        ):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > import_request_max_bytes:
                        return error(413)
                except ValueError:
                    return error(400)
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > import_request_max_bytes:
                    return error(413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
    @app.exception_handler(Exception)
    async def internal(_request, _exc): return error(400)

    @app.get("/api/session")
    async def bootstrap(request: Request):
        token = request.cookies.get("p0_session")
        existing = (
            database.one("SELECT * FROM sessions WHERE token_hash=?", (digest(token),))
            if token else None
        )
        current = now()
        recovery_expiry = None
        if existing is not None:
            try:
                recovery_expiry = datetime.fromisoformat(
                    existing["recovery_expires_at"]
                ).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                recovery_expiry = None
        if recovery_expiry is None or recovery_expiry <= current:
            if existing is not None:
                database.execute(
                    "DELETE FROM sessions WHERE token_hash=?", (digest(token),)
                )
            token = opaque()
            recovery_expiry = current + timedelta(
                seconds=session_recovery_ttl_seconds
            )
        # The cookie may renew the short active lease only until this absolute
        # recovery deadline. A captured cookie therefore cannot revive forever.
        csrf = opaque()
        expiry = min(
            current + timedelta(seconds=session_ttl_seconds), recovery_expiry
        )
        database.execute(
            "INSERT OR REPLACE INTO sessions("
            "token_hash,csrf_hash,expires_at,recovery_expires_at"
            ") VALUES(?,?,?,?)",
            (
                digest(token),
                digest(csrf),
                Database.now_text(expiry),
                Database.now_text(recovery_expiry),
            ),
        )
        response = JSONResponse({"csrf_token": csrf, "expires_at": Database.now_text(expiry)})
        response.set_cookie("p0_session", token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/environment")
    async def environment():
        report = collect_environment(KNOWN_APPLICATION_PATHS).public_dict()
        return {"os": report["os"], "applications": report["applications"], "tools": report["tools"], "issues": report["issues"]}

    @app.get("/api/setup-options")
    async def setup_options_endpoint():
        return setup_public

    @app.get("/api/sources")
    async def list_sources():
        result=[]
        for source_id in ("qq","wechat"):
            row=database.one("SELECT * FROM sources WHERE source_id=?", (source_id,))
            if row:
                item = {key: row[key] for key in ("source_id","status","account_id","base_url","client_version","tool_version","account_version") } | {"secret_configured": bool(row["secret_configured"])}
                if source_is_blocked(source_id):
                    item["status"] = "blocked"
                result.append(item)
            else:
                result.append({"source_id":source_id,"status":"blocked" if source_is_blocked(source_id) else "unconfigured","secret_configured":False})
        return {"sources":result}

    @app.post("/api/sources")
    async def configure(request: Request):
        body=await safe_json(request); allowed={"source_id","account_id","base_url","client_version","tool_version","account_version"}
        if not valid_keys(body, allowed) or body.get("source_id") not in {"qq","wechat"} or not all(isinstance(body.get(k),str) and body[k] for k in allowed): return error(422)
        if source_is_blocked(body["source_id"]): return error(409)
        if body["base_url"] != approved_base_urls[body["source_id"]]:
            return error(422)
        database.configure_source({
            "source_id": body["source_id"],
            "account_id": body["account_id"],
            "base_url": body["base_url"],
            "client_version": body["client_version"],
            "tool_version": body["tool_version"],
            "account_version": body["account_version"],
            "nonce": opaque(),
            "status": "needs_user_action",
            "secret_ref": uuid4().hex,
        })
        return JSONResponse({"source_id":body["source_id"],"status":"needs_user_action"}, status_code=201)

    @app.post("/api/sources/{source_id}/disconnect")
    async def disconnect_source(source_id: str, request: Request):
        body = await safe_json(request)
        if source_id not in {"qq", "wechat"} or not valid_keys(body, {"confirmed"}):
            return error(422)
        if body.get("confirmed") is not True:
            return error(403)
        if chat_repository.has_active_source_jobs(source_id):
            return error(409)
        outcome = database.disconnect_source(source_id)
        if outcome == "disconnected":
            return Response(status_code=204)
        if outcome == "not_found":
            return error(404)
        return error(409)

    @app.get("/api/sources/{source_id}/conversations")
    async def conversations(source_id: str, request: Request, account_id: str | None = None):
        if source_is_blocked(source_id): return error(409)
        sess=session(request, True); source=database.one("SELECT * FROM sources WHERE source_id=?", (source_id,))
        if not sess or not source: return error(403)
        selected_account=account_id or source["account_id"]
        adapter_grant = {}
        if source["secret_configured"]:
            if selected_account != source["account_id"]: return error(403)
            try:
                access = _secret_access(source, secret_backend)
            except (TypeError, ValueError):
                return error(409)
            grant = database.claim_secret_grant_for_access(
                source=source,
                session_hash=sess[0],
                secret_backend=secret_backend,
                scope_fingerprint=access.fingerprint(),
                now=now(),
            )
            if grant is None: return error(409)
            try:
                consent = ConsentRecord(
                    scope_fingerprint=grant["scope_fingerprint"],
                    granted=True,
                    recorded_at=datetime.fromisoformat(grant["recorded_at"]),
                    expires_at=datetime.fromisoformat(grant["expires_at"]),
                )
            except (TypeError, ValueError):
                return error(409)
            if not consent.matches(access, now=now()): return error(409)
            adapter_grant = {"token_access": access, "token_consent": consent}
        try:
            adapter=adapter_factory(source_id, selected_account, **adapter_grant)
            if selected_account not in adapter.list_accounts(): return error(403)
            enumerated = adapter.list_conversations()
        except Exception:
            return error(403)
        values=[]; expiry=now()+timedelta(seconds=selection_ttl_seconds)
        for conversation in enumerated:
            handle = ""
            for _attempt in range(8):
                candidate = f"selection:{opaque()}"
                if database.insert_selection(
                    handle_hash=digest(candidate),
                    session_hash=sess[0],
                    source_id=source_id,
                    account_id=selected_account,
                    conversation_id=conversation.conversation_id,
                    expires_at=Database.now_text(expiry),
                    nonce=source["nonce"],
                ):
                    handle = candidate
                    break
            if not handle:
                return error(503)
            values.append({"conversation_selection_id":handle,"display_name":conversation.display_name,"conversation_type":conversation.conversation_type,"account_id":conversation.account_id})
        return {"conversations":values}

    def resolve_selection(sess_hash, source_id, selection, tz):
        required={"source_id","account_id","conversation_selection_id","range_start","range_end","output_root_id"}
        if not isinstance(selection,dict) or set(selection)!=required or selection["source_id"] != source_id or selection["output_root_id"] not in roots: raise ValueError
        ZoneInfo(tz); start=parse_time(selection["range_start"]); end=parse_time(selection["range_end"])
        if end <= start: raise ValueError
        row=database.one("SELECT * FROM selections WHERE handle_hash=?",(digest(selection["conversation_selection_id"]),)); source=database.one("SELECT * FROM sources WHERE source_id=?",(source_id,))
        if not row or not source or row["session_hash"] != sess_hash or row["account_id"] != selection["account_id"] or row["nonce"] != source["nonce"] or datetime.fromisoformat(row["expires_at"]) <= now(): raise PermissionError
        return row, source, start, end

    @app.post("/api/sources/{source_id}/verification-consents")
    async def verification_consent(source_id: str, request: Request):
        if source_is_blocked(source_id): return error(409)
        sess=session(request, True); body=await safe_json(request)
        try: row, source, start, end=resolve_selection(sess[0],source_id,body.get("selection"),body.get("timezone")); tz=body["timezone"]
        except PermissionError: return error(403)
        except Exception: return error(422)
        scope=ConsentScope(source_id=source_id,account_id=row["account_id"],conversation_id=row["conversation_id"],range_start=body["selection"]["range_start"],range_end=body["selection"]["range_end"],output_path=body["selection"]["output_root_id"],operation="export_verification",client_version=source["client_version"],tool_version=source["tool_version"],account_version=source["account_version"],configuration_nonce=source["nonce"])
        disclosure = setup_public["source_disclosures"][source_id]
        challenge=opaque(); expiry=now()+timedelta(seconds=consent_ttl_seconds)
        payload=Database.dump(_verification_snapshot(body["selection"], tz, start, end, disclosure))
        scope_fp=_verification_scope_fingerprint(scope, tz, disclosure)
        database.execute(
            """
            INSERT INTO challenges(
                challenge_hash,session_hash,kind,source_id,account_id,
                scope_fingerprint,payload,expires_at,used
            ) VALUES(?,?,?,?,?,?,?,?,0)
            """,
            (digest(challenge),sess[0],"verification",source_id,row["account_id"],scope_fp,payload,Database.now_text(expiry)),
        )
        return {"consent_challenge":challenge,"disclosure":disclosure,"expires_at":Database.now_text(expiry),"scope_fingerprint":scope_fp}

    @app.post("/api/sources/{source_id}/verification-jobs")
    async def create_job(source_id: str, request: Request):
        if source_is_blocked(source_id): return error(409)
        sess=session(request, True); body=await safe_json(request)
        if body.get("confirmed") is not True: return error(403)
        if body.get("operation") not in (None,"export_verification"): return error(422)
        disclosure = setup_public["source_disclosures"].get(source_id)
        if disclosure is None or not _confirmation_matches(body, disclosure): return error(403)
        try: row, source, start, end=resolve_selection(sess[0],source_id,body.get("selection"),body.get("timezone"))
        except PermissionError: return error(403)
        except Exception: return error(422)
        scope=ConsentScope(source_id=source_id,account_id=row["account_id"],conversation_id=row["conversation_id"],range_start=body["selection"]["range_start"],range_end=body["selection"]["range_end"],output_path=body["selection"]["output_root_id"],operation="export_verification",client_version=source["client_version"],tool_version=source["tool_version"],account_version=source["account_version"],configuration_nonce=source["nonce"])
        job_id=uuid4().hex
        start_ms=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000)
        challenge_payload=Database.dump(_verification_snapshot(body["selection"], body["timezone"], start, end, disclosure))
        outcome=database.create_verification_job(
            job={
                "job_id": job_id,
                "session_hash": sess[0],
                "source_id": source_id,
                "account_id": row["account_id"],
                "base_url": source["base_url"],
                "conversation_id": row["conversation_id"],
                "output_root_id": body["selection"]["output_root_id"],
                "client_version": source["client_version"],
                "tool_version": source["tool_version"],
                "account_version": source["account_version"],
                "nonce": source["nonce"],
                "secret_ref": source["secret_ref"],
                "secret_backend": secret_backend,
                "range_start": body["selection"]["range_start"],
                "range_end": body["selection"]["range_end"],
                "start_ms": start_ms,
                "end_ms": end_ms,
                "timezone": body["timezone"],
                "scope_fingerprint": scope.fingerprint(),
                "disclosure_revision": disclosure["revision"],
                "disclosure_hash": disclosure["hash"],
            },
            challenge_hash=digest(body.get("consent_challenge", "")),
            challenge_scope_fingerprint=_verification_scope_fingerprint(scope, body["timezone"], disclosure),
            challenge_payload=challenge_payload,
            now=now(),
        )
        if outcome == "challenge_rejected": return error(403)
        if outcome == "source_configuration_changed": return error(409)
        if outcome == "secret_authorization_missing": return error(409)
        if outcome != "created": return error(403)
        if run_jobs:
            Thread(target=app.state.verifier.run, args=(job_id,), daemon=True).start()
        return JSONResponse({"job_id":job_id,"state":"pending_authorization"},status_code=202)

    @app.get("/api/sources/{source_id}/verification-jobs/{job_id}")
    async def job(source_id: str, job_id: str):
        row=database.one("SELECT * FROM jobs WHERE job_id=? AND source_id=?",(job_id,source_id))
        if not row:return error(404)
        return public_job(row)

    @app.post("/api/sources/{source_id}/verification-jobs/{job_id}/cancel")
    async def cancel(source_id: str, job_id: str):
        row=database.one("SELECT job_id FROM jobs WHERE job_id=? AND source_id=?",(job_id,source_id))
        if not row:return error(404)
        state = app.state.verifier.cancel(job_id)
        if state is None:return error(404)
        return {"job_id":job_id,"state":state}

    @app.post("/api/sources/{source_id}/secret-consents")
    async def secret_consent(source_id: str, request: Request):
        sess=session(request,True); body=await safe_json(request); source=database.one("SELECT * FROM sources WHERE source_id=?",(source_id,))
        if not source or body.get("account_id")!=source["account_id"] or body.get("operation") not in {"secret_set","secret_get","secret_delete"}:return error(422)
        operation = body["operation"]
        if source_is_blocked(source_id) and operation != "secret_delete": return error(409)
        disclosure = _secret_disclosure(source_id, operation, secret_backend)
        challenge=opaque(); expiry=now()+timedelta(seconds=consent_ttl_seconds)
        snapshot=_secret_snapshot(source, secret_backend, disclosure)
        scope_fingerprint = _secret_scope_fingerprint(snapshot, operation)
        database.execute(
            """
            INSERT INTO challenges(
                challenge_hash,session_hash,kind,source_id,account_id,
                scope_fingerprint,payload,expires_at,used
            ) VALUES(?,?,?,?,?,?,?,?,0)
            """,
            (digest(challenge),sess[0],operation,source_id,source["account_id"],scope_fingerprint,Database.dump(snapshot),Database.now_text(expiry)),
        )
        return {"consent_challenge":challenge,"disclosure":disclosure,"expires_at":Database.now_text(expiry)}

    @app.post("/api/sources/{source_id}/secret-operations")
    async def secret_operation(source_id: str, request: Request):
        sess=session(request,True); body=await safe_json(request); source=database.one("SELECT * FROM sources WHERE source_id=?",(source_id,))
        operation=body.get("operation")
        mapping={"secret_set":SecretOperation.SET,"secret_get":SecretOperation.GET,"secret_delete":SecretOperation.DELETE}
        if operation not in mapping or not source or body.get("account_id") != source["account_id"]:return error(422)
        if source_is_blocked(source_id) and operation != "secret_delete": return error(409)
        if body.get("confirmed") is not True:return error(403)
        if operation == "secret_set" and (not isinstance(body.get("value"),str) or not body["value"]):return error(422)
        disclosure = _secret_disclosure(source_id, operation, secret_backend)
        if not _confirmation_matches(body, disclosure):return error(403)
        snapshot=_secret_snapshot(source, secret_backend, disclosure)
        challenge=database.claim_challenge(
            challenge_hash=digest(body.get("consent_challenge", "")),
            session_hash=sess[0],
            kind=operation,
            source_id=source_id,
            account_id=source["account_id"],
            scope_fingerprint=_secret_scope_fingerprint(snapshot, operation),
            payload=Database.dump(snapshot),
            now=now(),
        )
        if challenge is None:return error(403)
        recorded_at=now(); expires_at=datetime.fromisoformat(challenge["expires_at"])
        access=SecretAccessScope(operation=mapping[operation],secret_reference=source["secret_ref"],secret_backend=secret_backend,source_id=source_id,account_id=source["account_id"],client_version=source["client_version"],tool_version=source["tool_version"],account_version=source["account_version"],configuration_nonce=source["nonce"]); consent=ConsentRecord.grant(access,expires_at=expires_at,now=recorded_at)
        try:
            if operation=="secret_set":
                secret_store.set(access=access,consent=consent,value=body["value"])
                if not database.set_secret_configured(source, configured=True): return error(409)
            elif operation=="secret_get":
                secret_store.get(access=access,consent=consent)
                if not database.store_secret_grant(
                    source=source,
                    session_hash=sess[0],
                    secret_backend=secret_backend,
                    scope_fingerprint=access.fingerprint(),
                    recorded_at=consent.recorded_at,
                    expires_at=consent.expires_at,
                ): return error(409)
            else:
                secret_store.delete(access=access,consent=consent)
                if not database.set_secret_configured(source, configured=False, clear_grants=True): return error(409)
        except Exception:return error(403)
        return Response(status_code=204)

    def require_p1_session(request: Request) -> str:
        if session(request) is None:
            raise HTTPException(status_code=401, detail="local session required")
        token = request.cookies.get("p0_session")
        if not token:
            raise HTTPException(status_code=401, detail="local session required")
        return token

    def require_p1_csrf(request: Request) -> None:
        if session(request, True) is None:
            raise HTTPException(status_code=403, detail="csrf check failed")

    def resolve_p1_conversations(
        *, source_id: str, account_id: str, handles: tuple[str, ...], session_id: str
    ):
        source = database.one(
            "SELECT * FROM sources WHERE source_id = ?", (source_id,)
        )
        if (
            source is None
            or source["account_id"] != account_id
        ):
            raise AuthorizationError("export selection is unavailable")
        session_hash = digest(session_id)
        conversations: list[ExportConversation] = []
        for handle in handles:
            row = database.one(
                "SELECT * FROM selections WHERE handle_hash = ?", (digest(handle),)
            )
            if (
                row is None
                or row["session_hash"] != session_hash
                or row["source_id"] != source_id
                or row["account_id"] != account_id
                or row["nonce"] != source["nonce"]
                or datetime.fromisoformat(row["expires_at"]) <= now()
            ):
                raise AuthorizationError("export selection is unavailable")
            conversations.append(
                ExportConversation(conversation_id=str(row["conversation_id"]))
            )
        return source, tuple(conversations)

    def resolve_p1_scope(
        selection: SelectionExportScopeRequest, session_id: str
    ) -> ExportScope:
        if source_is_blocked(selection.source_id):
            raise AuthorizationError("source export is blocked")
        if selection.output_root_id not in approved_p1_roots:
            raise AuthorizationError("export selection is unavailable")
        source, conversations = resolve_p1_conversations(
            source_id=selection.source_id,
            account_id=selection.account_id,
            handles=selection.conversation_selection_ids,
            session_id=session_id,
        )
        return ExportScope(
            source_id=selection.source_id,
            account_id=selection.account_id,
            conversations=conversations,
            range_start=selection.range_start,
            range_end=selection.range_end,
            output_root_id=selection.output_root_id,
            configuration_nonce=str(source["nonce"]),
            display_timezone=selection.display_timezone,
            client_version=source["client_version"],
            tool_version=source["tool_version"],
            account_version=source["account_version"],
        )

    def resolve_p1_search(
        selection: SelectionSearchRequest, session_id: str
    ) -> SearchRequest:
        if selection.source_id == "manual":
            rows = chat_repository.resolve_manual_import_selections(
                session_digest=digest(session_id),
                source_id=selection.source_id,
                account_id=selection.account_id,
                handle_digests=tuple(digest(item) for item in selection.conversation_selection_ids),
                now_utc=now().isoformat(),
            )
            conversations = tuple(ExportConversation(
                conversation_id=str(row["conversation_id"]),
                display_name=row["conversation_name"],
                conversation_type=row["conversation_type"],
            ) for row in rows)
            snapshot_id = str(rows[0]["snapshot_id"])
        else:
            _source, conversations = resolve_p1_conversations(
                source_id=selection.source_id,
                account_id=selection.account_id,
                handles=selection.conversation_selection_ids,
                session_id=session_id,
            )
            snapshot_id = None
        return SearchRequest(
            query=selection.query,
            conversation_ids=tuple(
                conversation.conversation_id for conversation in conversations
            ),
            snapshot_id=snapshot_id,
            start_time=selection.start_time,
            end_time=selection.end_time,
            include_undated=selection.include_undated,
            limit=selection.limit,
            cursor=selection.cursor,
        )

    def resolve_generation_selection(
        selection: SelectionGenerationRequest, session_id: str
    ) -> GenerationSelection:
        if (
            not isinstance(settings.llm_provider, str)
            or not settings.llm_provider.strip()
            or settings.llm_provider == "unconfigured"
        ):
            raise GenerationAuthorizationError("generation provider is unavailable")
        if selection.source_id == "manual":
            rows = chat_repository.resolve_manual_import_selections(
                session_digest=digest(session_id),
                source_id=selection.source_id,
                account_id=selection.account_id,
                handle_digests=tuple(digest(item) for item in selection.conversation_selection_ids),
                now_utc=now().isoformat(),
            )
            if any(row["snapshot_id"] != selection.snapshot_id for row in rows):
                raise GenerationAuthorizationError("generation selection is unavailable")
            conversations = tuple(ExportConversation(
                conversation_id=str(row["conversation_id"]),
                display_name=row["conversation_name"],
                conversation_type=row["conversation_type"],
            ) for row in rows)
        else:
            _source, conversations = resolve_p1_conversations(
                source_id=selection.source_id,
                account_id=selection.account_id,
                handles=selection.conversation_selection_ids,
                session_id=session_id,
            )
        return GenerationSelection(
            snapshot_id=selection.snapshot_id,
            conversation_ids=tuple(
                conversation.conversation_id for conversation in conversations
            ),
            start_time=selection.start_time,
            end_time=selection.end_time,
            topic=selection.topic,
            mode=selection.mode,
            generation_kind=selection.generation_kind,
            summary_type=selection.summary_type,
            allow_undated=selection.include_undated,
            included_message_ids=selection.included_message_ids,
            provider=settings.llm_provider,
            model=settings.llm_model,
            thinking=settings.llm_thinking,
            reasoning_effort=settings.llm_reasoning_effort,
            redaction_policy_revision="text-only-outbound-v1",
            excluded_message_ids=selection.excluded_message_ids,
            budget=GenerationBudget(
                max_messages=5_000,
                max_input_tokens=100_000,
                max_provider_calls=20,
                # A full-topic document includes detailed prose and long evidence IDs.
                # Keep a hard, pre-authorized bound without truncating those references.
                max_output_tokens=settings.llm_max_output_tokens if selection.summary_type is not None else 4_000,
            ),
        )

    app.include_router(
        create_imports_router(
            get_service=lambda: chat_imports,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
        )
    )
    app.include_router(
        create_content_imports_router(
            get_service=lambda: content_imports,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
        )
    )
    app.include_router(
        create_export_jobs_router(
            get_orchestrator=lambda: export_orchestrator,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
            resolve_scope=resolve_p1_scope,
        )
    )
    app.include_router(
        create_export_batches_router(
            get_orchestrator=lambda: export_orchestrator,
            require_session=require_p1_session,
        )
    )
    app.include_router(
        create_search_router(
            get_search=lambda: chat_search,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
            resolve_search=resolve_p1_search,
        )
    )
    app.include_router(
        create_generation_router(
            get_orchestrator=lambda: generation_orchestrator,
            get_topic_expansion=lambda: topic_expansion_service,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
            resolve_selection=resolve_generation_selection,
        )
    )
    app.include_router(
        create_documents_router(
            get_service=lambda: document_service,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
        )
    )
    app.include_router(
        create_document_exports_router(
            get_service=lambda: document_export_service,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
        )
    )
    app.include_router(
        create_deletion_router(
            get_service=lambda: deletion_service,
            require_session=require_p1_session,
            require_csrf=require_p1_csrf,
        )
    )
    if frontend_dist is not None:
        static_root = Path(frontend_dist)
        if static_root.is_symlink() or not static_root.is_dir() or not (static_root / "index.html").is_file():
            raise ValueError("built frontend directory is unavailable")
        app.mount("/", StaticFiles(directory=static_root, html=True), name="frontend")
    return app


def _secret_snapshot(source, secret_backend, disclosure):
    return {
        key: source[key]
        for key in (
            "source_id", "account_id", "base_url", "client_version",
            "tool_version", "account_version", "nonce", "secret_ref",
        )
    } | {
        "secret_backend": secret_backend,
        "disclosure_revision": disclosure["revision"],
        "disclosure_hash": disclosure["hash"],
    }


def _secret_access(source, secret_backend):
    return SecretAccessScope(
        operation=SecretOperation.GET,
        secret_reference=source["secret_ref"],
        secret_backend=secret_backend,
        source_id=source["source_id"],
        account_id=source["account_id"],
        client_version=source["client_version"],
        tool_version=source["tool_version"],
        account_version=source["account_version"],
        configuration_nonce=source["nonce"],
    )


def _verification_snapshot(selection, timezone_name, start, end, disclosure):
    return {
        "selection": selection,
        "timezone": timezone_name,
        "start_ms": int(start.timestamp() * 1000),
        "end_ms": int(end.timestamp() * 1000),
        "disclosure_revision": disclosure["revision"],
        "disclosure_hash": disclosure["hash"],
    }


def _verification_scope_fingerprint(scope, timezone_name, disclosure):
    value = "|".join((
        scope.fingerprint(), timezone_name,
        disclosure["revision"], disclosure["hash"],
    ))
    return sha256(value.encode()).hexdigest()


def _secret_scope_fingerprint(snapshot, operation):
    return sha256((operation + "|" + Database.dump(snapshot)).encode()).hexdigest()


def _confirmation_matches(body, disclosure):
    revision = body.get("disclosure_revision")
    disclosure_hash = body.get("disclosure_hash")
    return (
        isinstance(revision, str)
        and isinstance(disclosure_hash, str)
        and secrets_compare(revision, disclosure["revision"])
        and secrets_compare(disclosure_hash, disclosure["hash"])
    )


def _seal_disclosure(value):
    public = json.loads(json.dumps(value, ensure_ascii=False))
    encoded = json.dumps(
        public, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    public["hash"] = sha256(encoded).hexdigest()
    return public


def _secret_disclosure(source_id, operation, secret_backend):
    source = _SOURCE_DISCLOSURE[source_id]
    risks = ["source_api_token_stays_server_side"]
    if source_id == "wechat":
        risks.append("wechat_database_key_is_not_stored_by_platform")
    return _seal_disclosure({
        "source_id": source_id,
        "tool": source["tool"],
        "operation": operation,
        "secret_backend": secret_backend,
        "revision": _SECRET_DISCLOSURE_REVISION,
        "sections": dict(_SECRET_DISCLOSURE_SECTIONS),
        "risks": risks,
    })


async def safe_json(request):
    try:
        value=await request.json(); return value if isinstance(value,dict) else {}
    except Exception:return {}
def valid_keys(body, allowed): return set(body) <= allowed
def parse_time(value):
    if not isinstance(value,str): raise ValueError
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError
    return parsed.astimezone(timezone.utc)
def secrets_compare(left,right):
    import hmac
    return hmac.compare_digest(left,right)
def public_job(row):
    result={key:row[key] for key in ("job_id","source_id","client_version","tool_version","account_version","range_start","range_end","timezone","count","sha256","completeness","state","evidence_class")}
    result["hash_ok"]=None if row["hash_ok"] is None else bool(row["hash_ok"]); result["range_ok"]=None if row["range_ok"] is None else bool(row["range_ok"])
    if row["error_code"]:result["error_code"]=row["error_code"]
    result["schema_version"] = P0_SCHEMA_VERSION
    return result

def _public_setup_options(roots, injected):
    """Return only public labels and fixed disclosure fields, never filesystem paths."""
    root_ids = tuple(roots)
    if not all(isinstance(root_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", root_id) for root_id in root_ids):
        raise ValueError("output root IDs must be opaque public identifiers")
    defaults = {
        "approved_output_roots": [
            {"output_root_id": root_id, "label": f"Approved output ({root_id})"}
            for root_id in root_ids
        ],
        "source_disclosures": {
            source_id: _source_disclosure(source_id, _DEFAULT_DISCLOSURE)
            for source_id in ("qq", "wechat")
        },
    }
    if injected is None:
        return defaults
    if not isinstance(injected, dict) or set(injected) != {"approved_output_roots", "source_disclosures"}:
        raise ValueError("invalid setup options")
    root_items = injected["approved_output_roots"]
    if not isinstance(root_items, list) or {item.get("output_root_id") for item in root_items if isinstance(item, dict)} != set(root_ids):
        raise ValueError("setup options must describe each approved output root")
    public_roots = []
    for item in root_items:
        if not isinstance(item, dict) or set(item) != {"output_root_id", "label"}:
            raise ValueError("invalid output root metadata")
        root_id, label = item["output_root_id"], item["label"]
        if not isinstance(root_id, str) or not isinstance(label, str) or not label or "/" in label or "\\" in label:
            raise ValueError("output root label must be public")
        public_roots.append({"output_root_id": root_id, "label": label})
    disclosures = injected["source_disclosures"]
    if not isinstance(disclosures, dict) or set(disclosures) != {"qq", "wechat"}:
        raise ValueError("invalid source disclosures")
    public_disclosures = {}
    for source_id, disclosure in disclosures.items():
        if not isinstance(disclosure, dict) or set(disclosure) != {"revision", "sections", "blockers"}:
            raise ValueError("invalid source disclosure")
        sections = disclosure["sections"]
        if not isinstance(disclosure["revision"], str) or not isinstance(sections, dict) or set(sections) != set(_DISCLOSURE_SECTIONS) or not isinstance(disclosure["blockers"], list):
            raise ValueError("invalid source disclosure")
        if not all(isinstance(value, str) and value for value in sections.values()) or not all(isinstance(value, str) for value in disclosure["blockers"]):
            raise ValueError("invalid source disclosure")
        public_disclosures[source_id] = _source_disclosure(source_id, disclosure)
    return {"approved_output_roots": public_roots, "source_disclosures": public_disclosures}


def _source_disclosure(source_id, disclosure):
    source = _SOURCE_DISCLOSURE[source_id]
    return _seal_disclosure({
        "source_id": source_id,
        "tool": source["tool"],
        "revision": disclosure["revision"],
        "sections": dict(disclosure["sections"]),
        "risks": list(source["risks"]),
        "blockers": list(disclosure["blockers"]),
    })
