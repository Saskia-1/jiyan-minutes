"""Synthetic AI task planning, exact authorization, and bounded safe results."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Event

import pytest

from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.repositories.chat import ChatRepository


NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
VALID = dict(goal="归纳创业讨论中的可探索方向", keywords=["创业"], summary_type="ideas",
             all_content=False, needs_clarification=False, notices=[])


class PlanningProvider:
    name = "fake"

    def __init__(self, result=None, *, failure=None, finish_reason="stop"):
        self.result = VALID if result is None else result
        self.failure = failure
        self.finish_reason = finish_reason
        self.requests = []

    async def generate(self, request, cancellation):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return ProviderResponse(provider="fake", model=request.model,
            content=json.dumps(self.result, ensure_ascii=False), finish_reason=self.finish_reason,
            prompt_tokens=20, completion_tokens=15)


def _service(tmp_path, provider=None, *, clock=None, limit=100, timeout=30):
    from chat_to_howto.services.ai_agent_planning import AIAgentPlanningService
    repository = ChatRepository(tmp_path / "planning.sqlite3")
    provider = provider or PlanningProvider()
    factories = []

    def factory(name):
        factories.append(name)
        return provider

    service = AIAgentPlanningService(repository=repository, provider_factory=factory,
        provider="fake", model="fixture-model", clock=clock or (lambda: NOW),
        max_daily_provider_requests=limit, timeout_seconds=timeout)
    return service, provider, factories, repository


def _execute(service, preview, **changes):
    arguments = dict(preview_fingerprint=preview.preview_fingerprint,
                     confirmed=True, session_id="owner")
    arguments.update(changes)
    return service.execute_authorized(preview.preview_id, **arguments)


def test_preview_redacts_and_freezes_exact_request_without_factory_or_data_reads(tmp_path):
    service, provider, factories, repository = _service(tmp_path)
    preview = service.create_preview("  联系 13800138000，提取创业想法  ", session_id="owner")
    assert factories == []
    assert provider.requests == []
    assert len(preview.preview_id) == 32
    assert len(preview.preview_fingerprint) == 64
    assert preview.provider == "fake"
    assert preview.expires_at_utc == NOW + timedelta(minutes=5)
    assert "13800138000" not in preview.outbound_text
    assert json.loads(preview.outbound_text) == {"operation": "plan_agent", "instruction": preview.instruction}
    assert preview.instruction == "联系 [已脱敏手机号]，提取创业想法"
    assert preview.max_output_tokens > 0
    result = _execute(service, preview)
    assert result.instruction == preview.instruction
    assert result.goal == VALID["goal"]
    assert result.keywords == ["创业"]
    assert result.summary_type == "ideas"
    assert provider.requests[0].payload.decode() == preview.outbound_text
    assert provider.requests[0].max_output_tokens == preview.max_output_tokens
    assert factories == ["fake"]
    with pytest.raises(PermissionError):
        _execute(service, preview)
    assert len(provider.requests) == 1
    repository.close()


def test_unconfirmed_wrong_session_changed_fingerprint_expired_and_cancelled_do_not_dispatch(tmp_path):
    now = [NOW]
    service, provider, factories, repository = _service(tmp_path, clock=lambda: now[0])
    preview = service.create_preview("创业", session_id="owner")
    for changes in ({"confirmed": False}, {"session_id": "other"},
                    {"preview_fingerprint": "0" * 64}):
        with pytest.raises(PermissionError):
            _execute(service, preview, **changes)
    with pytest.raises(PermissionError):
        service.cancel(preview.preview_id, session_id="other")
    assert factories == []
    now[0] += timedelta(minutes=5)
    with pytest.raises(PermissionError):
        _execute(service, preview)
    preview = service.create_preview("创业", session_id="owner")
    assert service.cancel(preview.preview_id, session_id="owner") is True
    with pytest.raises(PermissionError):
        _execute(service, preview)
    assert factories == [] and provider.requests == []
    repository.close()


def test_preview_pending_capacity_recovers_after_cancel_or_expiry(tmp_path):
    now = [NOW]
    service, _, factories, repository = _service(tmp_path, clock=lambda: now[0])
    previews = [service.create_preview("创业", session_id="owner") for _ in range(128)]
    with pytest.raises(ValueError):
        service.create_preview("创业", session_id="owner")
    service.cancel(previews[0].preview_id, session_id="owner")
    service.create_preview("创业", session_id="owner")
    now[0] += timedelta(minutes=5)
    service.create_preview("创业", session_id="owner")
    assert factories == []
    repository.close()


def test_redaction_can_expand_instruction_beyond_original_input_limit(tmp_path):
    service, _, _, repository = _service(tmp_path, PlanningProvider(VALID | {"all_content": True}))
    instruction = ("a@b.co " * 70) + "创业"
    assert len(instruction) <= 512
    preview = service.create_preview(instruction, session_id="owner")
    assert len(preview.instruction) > 512
    assert _execute(service, preview).instruction == preview.instruction
    repository.close()


@pytest.mark.parametrize("changes", [
    {"goal": ""}, {"goal": "x" * 513}, {"goal": "private\ntext"},
    {"keywords": ["x"] * 11}, {"keywords": ["x" * 65]}, {"keywords": ["x\t"]},
    {"keywords": [42]}, {"keywords": [""]}, {"summary_type": "invented"},
    {"all_content": 1}, {"needs_clarification": "false"}, {"notices": ["x"] * 6},
    {"notices": ["x" * 257]}, {"notices": ["x\0"]}, {"extra": "private-output"},
])
def test_strict_bounded_model_output_fails_safely_without_rule_fallback(tmp_path, changes):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    service, _, _, repository = _service(tmp_path, PlanningProvider(VALID | changes))
    preview = service.create_preview("创业", session_id="owner")
    with pytest.raises(AgentPlanningFailure) as caught:
        _execute(service, preview)
    assert str(caught.value) == "provider_output_invalid"
    repository.close()


@pytest.mark.parametrize("instruction,changes,expected_all,expected_clarification", [
    ("帮我整理一下", {"all_content": True, "keywords": []}, False, True),
    ("整理全部聊天内容", {"all_content": True, "keywords": []}, True, False),
    ("关于创业的观点，不要发送商业机密", {}, False, True),
    ("总结全部聊天内容但排除医疗内容", {"all_content": True, "keywords": []}, False, True),
    ("创业", {"keywords": []}, False, True),
])
def test_scope_is_never_widened_or_claimed_to_apply_exclusions(tmp_path, instruction, changes,
                                                            expected_all, expected_clarification):
    service, _, _, repository = _service(tmp_path, PlanningProvider(VALID | changes))
    result = _execute(service, service.create_preview(instruction, session_id="owner"))
    assert result.all_content is expected_all
    assert result.needs_clarification is expected_clarification
    if "排除" in instruction or "不要发送" in instruction:
        assert any("尚未执行" in item for item in result.notices)
    repository.close()


def test_provider_failures_are_safe_consumed_and_never_fall_back(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    service, _, _, repository = _service(tmp_path,
        PlanningProvider(failure=RuntimeError("private /Users/sensitive/token")))
    preview = service.create_preview("创业", session_id="owner")
    with pytest.raises(AgentPlanningFailure) as caught:
        _execute(service, preview)
    assert str(caught.value) == "provider_unavailable"
    with pytest.raises(PermissionError):
        _execute(service, preview)
    repository.close()


def test_factory_configuration_failure_is_safe_and_requires_new_authorization(tmp_path):
    from chat_to_howto.providers.base import ProviderConfigurationError
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    service, _, _, repository = _service(tmp_path)
    def unavailable(_name):
        raise ProviderConfigurationError("private-secret-config")
    service._provider_factory = unavailable
    preview = service.create_preview("创业", session_id="owner")
    with pytest.raises(AgentPlanningFailure, match="^provider_unavailable$"):
        _execute(service, preview)
    with pytest.raises(PermissionError):
        _execute(service, preview)
    repository.close()


def test_running_cancel_discards_even_provider_that_ignores_token(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    entered, release = Event(), Event()
    class IgnoringProvider(PlanningProvider):
        async def generate(self, request, cancellation):
            entered.set()
            await asyncio.to_thread(release.wait, 2)
            return await super().generate(request, cancellation)
    service, _, _, repository = _service(tmp_path, IgnoringProvider())
    preview = service.create_preview("创业", session_id="owner")
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(_execute, service, preview)
        assert entered.wait(2)
        assert service.cancel(preview.preview_id, session_id="owner") is True
        release.set()
        with pytest.raises(AgentPlanningFailure, match="^planning_cancelled$"):
            running.result(timeout=3)
    repository.close()


def test_provider_timeout_is_bounded_and_cancels_token(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    tokens = []
    class SlowProvider(PlanningProvider):
        async def generate(self, request, cancellation):
            tokens.append(cancellation)
            await asyncio.sleep(10)
    service, _, _, repository = _service(tmp_path, SlowProvider(), timeout=0.01)
    with pytest.raises(AgentPlanningFailure, match="^provider_timeout$"):
        _execute(service, service.create_preview("创业", session_id="owner"))
    assert tokens[0].is_cancelled
    repository.close()


def test_daily_limit_persists_across_service_restart_and_contains_no_text(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningDailyLimit
    service, provider, _, repository = _service(tmp_path, limit=1)
    _execute(service, service.create_preview("创业", session_id="owner"))
    repository.close()
    restarted, restarted_provider, _, repository = _service(tmp_path, limit=1)
    with pytest.raises(AgentPlanningDailyLimit):
        _execute(restarted, restarted.create_preview("创业", session_id="owner"))
    assert len(provider.requests) == 1 and restarted_provider.requests == []
    import sqlite3
    with sqlite3.connect(repository.path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM chat_agent_plan_requests").fetchone())
        assert row["status"] == "completed"
        assert row["prompt_tokens"] == 20 and row["completion_tokens"] == 15
        assert not set(row) & {"instruction", "payload", "goal", "session_id", "session_digest"}
        assert "创业" not in str(row)
    repository.close()


def test_concurrent_connections_reserve_only_one_daily_attempt(tmp_path):
    path = tmp_path / "quota.sqlite3"
    repositories = [ChatRepository(path), ChatRepository(path)]
    def reserve(item):
        index, repository = item
        try:
            repository.reserve_agent_plan_request(request_record_id=f"request-{index}",
                provider="fake", model="fixture", created_at_utc=NOW.isoformat(),
                day_start_utc=NOW.isoformat(), day_end_utc=(NOW + timedelta(days=1)).isoformat(),
                daily_request_limit=1)
            return True
        except PermissionError:
            return False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, enumerate(repositories))) == [False, True]
    finally:
        for repository in repositories:
            repository.close()


def test_cancel_after_reservation_but_before_dispatch_never_creates_provider(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    service, provider, factories, repository = _service(tmp_path)
    preview = service.create_preview("创业", session_id="owner")
    original = repository.reserve_agent_plan_request
    def reserve_and_cancel(**kwargs):
        original(**kwargs)
        service.cancel(preview.preview_id, session_id="owner")
    repository.reserve_agent_plan_request = reserve_and_cancel
    with pytest.raises(AgentPlanningFailure, match="^planning_cancelled$"):
        _execute(service, preview)
    assert factories == [] and provider.requests == []
    import sqlite3
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT status FROM chat_agent_plan_requests").fetchone() == ("cancelled",)
    repository.close()


@pytest.mark.parametrize("raw", ["not json", "[]", "{}", json.dumps(VALID)[:-1] + ',"all_content":true}',
                                '"' + "x" * 16_384 + '"'])
def test_malformed_or_ambiguous_json_is_rejected_safely(tmp_path, raw):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    class RawProvider(PlanningProvider):
        async def generate(self, request, cancellation):
            return ProviderResponse(provider="fake", model=request.model, content=raw, finish_reason="stop")
    service, _, _, repository = _service(tmp_path, RawProvider())
    with pytest.raises(AgentPlanningFailure, match="^provider_output_invalid$"):
        _execute(service, service.create_preview("创业", session_id="owner"))
    repository.close()


def test_incomplete_model_response_never_returns_a_plan(tmp_path):
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    service, _, _, repository = _service(tmp_path, PlanningProvider(finish_reason="length"))
    with pytest.raises(AgentPlanningFailure, match="^provider_output_invalid$"):
        _execute(service, service.create_preview("创业", session_id="owner"))
    repository.close()


def test_error_type_cannot_carry_provider_exception_text():
    from chat_to_howto.services.ai_agent_planning import AgentPlanningFailure
    assert str(AgentPlanningFailure("private /Users/sensitive/key")) == "provider_unavailable"
