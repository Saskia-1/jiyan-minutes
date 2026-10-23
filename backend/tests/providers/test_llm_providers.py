"""Provider contract tests use synthetic payloads and never call the network."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from unittest import mock

import httpx
import pytest

from chat_to_howto.providers.base import (
    CancellationToken,
    LLMProvider,
    ProviderCancelled,
    ProviderFailure,
    ProviderMalformedOutput,
    ProviderRequest,
    ProviderTimeout,
)
from chat_to_howto.providers.deepseek import DeepSeekProvider
from chat_to_howto.providers.fake import FakeProvider


SENTINEL_KEY = "synthetic-deepseek-secret"
SENTINEL_CHAT = "synthetic-private-chat"


def _request(**changes: object) -> ProviderRequest:
    values: dict[str, object] = {
        "model": "deepseek-v4-flash",
        "system_prompt": "Return the requested how-to as json.",
        "payload": json.dumps(
            {"topic": "synthetic", "messages": [{"message_id": "m1", "text": SENTINEL_CHAT}]}
        ).encode(),
        "max_output_tokens": 2_000,
    }
    values.update(changes)
    return ProviderRequest(**values)


def _run(awaitable):
    return asyncio.run(awaitable)


@pytest.mark.parametrize(
    ("mode", "expected_exception"),
    [
        ("timeout", ProviderTimeout),
        ("failure", ProviderFailure),
    ],
)
def test_fake_provider_has_deterministic_error_modes(mode: str, expected_exception: type[Exception]) -> None:
    with pytest.raises(expected_exception):
        _run(FakeProvider(mode=mode).generate(_request(), CancellationToken()))


def test_fake_provider_satisfies_the_generic_runtime_contract() -> None:
    assert isinstance(FakeProvider(), LLMProvider)


def test_fake_provider_success_malformed_and_partial_are_stable() -> None:
    success = _run(FakeProvider(mode="success").generate(_request(), CancellationToken()))
    malformed = _run(FakeProvider(mode="malformed").generate(_request(), CancellationToken()))
    partial = _run(FakeProvider(mode="partial").generate(_request(), CancellationToken()))

    assert json.loads(success.content)["result"]["status"] == "complete"
    assert success.finish_reason == "stop"
    assert malformed.content == "{"
    assert malformed.finish_reason == "stop"
    assert partial.finish_reason == "length"
    assert partial.content == '{"status":"complete"'
    assert success.provider == malformed.provider == partial.provider == "fake"


def test_fake_provider_returns_stable_topic_expansion_without_chat_messages() -> None:
    request = _request(payload=b'{"operation":"expand_topic","topic":"\xe5\xb0\xb1\xe4\xb8\x9a"}')

    first = _run(FakeProvider().generate(request, CancellationToken()))
    second = _run(FakeProvider().generate(request, CancellationToken()))

    assert first.content == second.content
    assert json.loads(first.content) == {
        "terms": ["就业", "求职", "秋招", "实习", "offer", "面试", "简历"],
    }


def test_fake_provider_ai_plan_is_a_contract_fixture_without_source_content() -> None:
    request = _request(payload=json.dumps({"operation": "plan_agent", "instruction": "把大家聊的创业点子捋一捋，归纳下一步值得试的方向"}, ensure_ascii=False).encode())
    response = _run(FakeProvider().generate(request, CancellationToken()))
    result = json.loads(response.content)
    assert result["keywords"] == ["创业"]
    assert result["summary_type"] == "ideas"
    assert result["goal"] == "归纳创业讨论中的想法与可探索方向"
    assert result["all_content"] is False
    assert result["needs_clarification"] is False
    assert set(result) == {"goal", "keywords", "summary_type", "all_content", "needs_clarification", "notices"}


def test_fake_agent_chooses_tool_using_search_feedback_and_latest_request() -> None:
    def choose(results, requests):
        payload = {"operation": "agent_tool", "instruction": "创业整理", "requests": requests,
                   "sources": [], "tool_results": results}
        response = _run(FakeProvider().generate(_request(payload=json.dumps(payload).encode()), CancellationToken()))
        return json.loads(response.content)
    first = choose([], ["整理创业的想法"])
    assert first == {"tool": "search_sources", "arguments": {"terms": ["创业"], "summary_type": "ideas", "goal": "整理所选资料"}}
    assert choose([{"tool": "search_sources", "terms": ["创业"], "hit_count": 2}], ["整理创业的想法"]) == {
        "tool": "generate_document", "arguments": {}}
    assert choose([], ["整理创业的想法", "只保留商业模式方面的观点"])["arguments"]["terms"] == ["商业模式"]
    assert choose([{"tool": "search_sources", "terms": ["创业"], "hit_count": 0}], ["整理创业的想法"])["tool"] == "ask_user"


def test_fake_provider_summary_is_stable_and_cites_the_supplied_message() -> None:
    request = _request(payload=json.dumps({
        "generation_kind": "summary", "topic": "就业",
        "messages": [{"message_id": "m1", "text": "合成就业讨论"}],
    }, ensure_ascii=False).encode("utf-8"))

    first = _run(FakeProvider().generate(request, CancellationToken()))
    second = _run(FakeProvider().generate(request, CancellationToken()))

    assert first.content == second.content
    parsed = json.loads(first.content)
    assert parsed["result"]["summary"]["core_summary"] == [
        {"text": "合成就业讨论", "source_message_ids": ["m1"]},
    ]
    assert parsed["evidence"] == [{"message_id": "m1", "quote": "合成就业讨论"}]


def test_fake_provider_waits_for_and_observes_cancellation() -> None:
    async def scenario() -> None:
        token = CancellationToken()
        task = asyncio.create_task(FakeProvider(mode="cancellation").generate(_request(), token))
        await asyncio.sleep(0)
        token.cancel()
        with pytest.raises(ProviderCancelled):
            await asyncio.wait_for(task, timeout=0.2)

    _run(scenario())


def test_pre_cancelled_request_never_reaches_deepseek_transport() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    token = CancellationToken()
    token.cancel()
    provider = DeepSeekProvider(
        api_key=SENTINEL_KEY,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderCancelled):
        _run(provider.generate(_request(), token))
    assert calls == 0


def test_deepseek_uses_fixed_endpoint_bearer_auth_json_output_and_no_ambient_state() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SENTINEL_KEY}"
        assert "cookie" not in request.headers
        assert body == {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "Return the requested how-to as json."},
                {"role": "user", "content": _request().payload.decode("utf-8")},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 2_000,
            "stream": False,
        }
        return httpx.Response(
            200,
            headers={"set-cookie": "provider-cookie=must-not-replay"},
            json={
                "id": "synthetic-response",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"status":"complete"}'},
                    }
                ],
                "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
            },
        )

    provider = DeepSeekProvider(
        api_key=SENTINEL_KEY,
        transport=httpx.MockTransport(handler),
    )
    first = _run(provider.generate(_request(), CancellationToken()))
    second = _run(provider.generate(_request(), CancellationToken()))

    assert first.content == second.content == '{"status":"complete"}'
    assert first.prompt_tokens == 42
    assert first.completion_tokens == 7
    assert first.request_id == "synthetic-response"
    assert first.provider == "deepseek"
    assert len(seen) == 2


def test_deepseek_thinking_profile_sends_exact_parameters_and_only_returns_final_content(caplog) -> None:
    private_reasoning = "private reasoning must never enter a document or log"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert body["model"] == "deepseek-v4-pro"
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "max"
        assert body["max_tokens"] == 64_000
        assert body["response_format"] == {"type": "json_object"}
        assert body["stream"] is False
        assert "temperature" not in body
        assert request.extensions["timeout"] == {
            "connect": 600.0, "read": 600.0, "write": 600.0, "pool": 600.0,
        }
        return httpx.Response(200, json={
            "id": "thinking-response", "model": "deepseek-v4-pro",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "reasoning_content": private_reasoning,
                "content": '{"result":{"status":"complete"}}',
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 500,
                      "completion_tokens_details": {"reasoning_tokens": 450}},
        })

    provider = DeepSeekProvider(api_key=SENTINEL_KEY, timeout_seconds=600,
                                max_response_bytes=4_194_304, transport=httpx.MockTransport(handler))
    response = _run(provider.generate(_request(model="deepseek-v4-pro", thinking="enabled",
                                               reasoning_effort="max", max_output_tokens=64_000), CancellationToken()))

    assert response.content == '{"result":{"status":"complete"}}'
    assert private_reasoning not in repr(response)
    assert private_reasoning not in response.model_dump_json()
    assert private_reasoning not in caplog.text
    assert response.completion_tokens == 500  # includes the provider's reasoning tokens


def test_deepseek_thinking_rejects_a_reported_model_downgrade() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "deepseek-flash", "choices": [{"finish_reason": "stop",
                "message": {"content": '{"result":{}}', "reasoning_content": "private"}}],
        })

    provider = DeepSeekProvider(api_key=SENTINEL_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderMalformedOutput):
        _run(provider.generate(_request(model="deepseek-v4-pro", thinking="enabled",
                                            reasoning_effort="max"), CancellationToken()))


def test_thinking_request_requires_explicit_effort_and_disabled_request_rejects_effort() -> None:
    with pytest.raises(ValueError):
        _request(thinking="enabled")
    with pytest.raises(ValueError):
        _request(reasoning_effort="max")


def test_deepseek_cancels_an_inflight_request() -> None:
    entered = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def scenario() -> None:
        provider = DeepSeekProvider(
            api_key=SENTINEL_KEY,
            transport=httpx.MockTransport(handler),
        )
        token = CancellationToken()
        task = asyncio.create_task(provider.generate(_request(), token))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        token.cancel()
        with pytest.raises(ProviderCancelled):
            await asyncio.wait_for(task, timeout=0.2)

    _run(scenario())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
        ),
        httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {
                "content": None, "reasoning_content": "private reasoning is not the final answer"}}]},
        ),
    ],
)
def test_deepseek_rejects_malformed_envelopes_without_leaking_bodies(response: httpx.Response) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response

    provider = DeepSeekProvider(api_key=SENTINEL_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderMalformedOutput) as raised:
        _run(provider.generate(_request(), CancellationToken()))
    assert "not-json" not in str(raised.value)
    assert SENTINEL_CHAT not in str(raised.value)


def test_deepseek_maps_timeout_and_http_failure_to_body_free_errors() -> None:
    async def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout with " + SENTINEL_CHAT)

    async def failure_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=("failure " + SENTINEL_CHAT).encode())

    timeout_provider = DeepSeekProvider(
        api_key=SENTINEL_KEY,
        transport=httpx.MockTransport(timeout_handler),
    )
    failure_provider = DeepSeekProvider(
        api_key=SENTINEL_KEY,
        transport=httpx.MockTransport(failure_handler),
    )

    with pytest.raises(ProviderTimeout) as timeout_error:
        _run(timeout_provider.generate(_request(), CancellationToken()))
    with pytest.raises(ProviderFailure) as failure_error:
        _run(failure_provider.generate(_request(), CancellationToken()))
    assert SENTINEL_CHAT not in str(timeout_error.value)
    assert SENTINEL_CHAT not in str(failure_error.value)


def test_deepseek_http_failure_keeps_only_safe_diagnostic_metadata(caplog) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {
            "code": "invalid_request_error", "type": "invalid_request_error",
            "message": "repeated private chat: " + SENTINEL_CHAT + " and " + SENTINEL_KEY,
        }})

    provider = DeepSeekProvider(api_key=SENTINEL_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure) as raised:
        _run(provider.generate(_request(), CancellationToken()))

    diagnostic = getattr(raised.value, "diagnostic", None)
    assert diagnostic is not None
    assert diagnostic.phase == "http_status"
    assert diagnostic.exception_type == "HTTPResponseStatus"
    assert diagnostic.http_status == 422
    assert diagnostic.service_error_code == "invalid_request_error"
    assert diagnostic.service_error_type == "invalid_request_error"
    assert diagnostic.status_note == "invalid_parameters"
    assert diagnostic.response_headers_received is True
    assert diagnostic.response_body_complete is True
    assert diagnostic.received_bytes > 0
    assert diagnostic.elapsed_ms >= 0
    assert diagnostic.code_position == "deepseek._send.http_status"
    assert SENTINEL_CHAT not in str(raised.value)
    assert SENTINEL_CHAT not in str(asdict(diagnostic))
    assert SENTINEL_KEY not in str(asdict(diagnostic))
    assert '"http_status": 422' in caplog.text
    assert SENTINEL_CHAT not in caplog.text
    assert SENTINEL_KEY not in caplog.text


def test_deepseek_connect_failure_keeps_stage_without_exception_text(caplog) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private chat: " + SENTINEL_CHAT)

    provider = DeepSeekProvider(api_key=SENTINEL_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure) as raised:
        _run(provider.generate(_request(), CancellationToken()))

    diagnostic = getattr(raised.value, "diagnostic", None)
    assert diagnostic is not None
    assert diagnostic.phase == "connect"
    assert diagnostic.exception_type == "ConnectError"
    assert diagnostic.http_status is None
    assert diagnostic.response_headers_received is False
    assert diagnostic.received_bytes == 0
    assert diagnostic.response_body_complete is False
    assert diagnostic.code_position == "deepseek._send.await_headers"
    assert SENTINEL_CHAT not in str(raised.value)
    assert SENTINEL_CHAT not in str(asdict(diagnostic))
    assert '"phase": "connect"' in caplog.text
    assert SENTINEL_CHAT not in caplog.text


def test_deepseek_rejects_oversized_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 129)

    provider = DeepSeekProvider(
        api_key=SENTINEL_KEY,
        transport=httpx.MockTransport(handler),
        max_response_bytes=128,
    )
    with pytest.raises(ProviderMalformedOutput):
        _run(provider.generate(_request(), CancellationToken()))


def test_deepseek_key_is_loaded_only_from_declared_environment_name_and_never_repr() -> None:
    with mock.patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            DeepSeekProvider.from_environment()

    with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": SENTINEL_KEY}, clear=True):
        provider = DeepSeekProvider.from_environment(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
        )
    assert SENTINEL_KEY not in repr(provider)
    assert repr(provider) == "<DeepSeekProvider>"
    assert SENTINEL_CHAT not in repr(_request())
