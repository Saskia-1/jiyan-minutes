"""Session protected local planning; invalid input never echoes private text."""

from datetime import datetime, timezone

import pytest


def test_agent_planning_is_local_and_requires_session_and_csrf(p0):
    p0.clock.now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    p0.start()
    body = {"instruction": "整理关于创业的观点"}
    assert p0.request("POST", "/api/agent/plans", json=body).status_code == 401
    p0.bootstrap()
    assert p0.request("POST", "/api/agent/plans", json=body, csrf=False).status_code == 401
    response = p0.request("POST", "/api/agent/plans", json=body)
    assert response.status_code == 200, response.text
    assert response.json() | {"notices": []} == {
        "instruction": body["instruction"], "goal": body["instruction"], "keywords": ["创业"],
        "summary_type": "viewpoints", "all_content": False, "needs_clarification": False, "notices": [],
    }
    assert p0.factory.reads == []
    assert p0.factory.enumerations == 0
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [{}, {"instruction": ""}, {"instruction": " \n"}, {"instruction": 42}, {"instruction": "私密" * 257}, {"instruction": "关于工作", "unexpected": "private-extra"}])
def test_agent_invalid_input_is_rejected_without_echoing_instruction(p0, body):
    p0.start()
    p0.bootstrap()
    response = p0.request("POST", "/api/agent/plans", json=body)
    assert response.status_code == 422
    assert "私密" not in response.text
    assert "private-extra" not in response.text


def test_replanning_uses_only_current_instruction(p0):
    p0.start()
    p0.bootstrap()
    first = p0.request("POST", "/api/agent/plans", json={"instruction": "整理关于创业的观点"}).json()
    second = p0.request("POST", "/api/agent/plans", json={"instruction": "提取关于摄影的想法"}).json()
    assert first["keywords"] == ["创业"]
    assert second["keywords"] == ["摄影"]
    assert second["summary_type"] == "ideas"


def test_ai_plan_preview_is_available_before_configuration_but_execute_fails_safely(p0):
    p0.start()
    body = {"instruction": "联系13800138000整理创业观点"}
    assert p0.request("POST", "/api/agent/plan-previews", json=body).status_code == 401
    p0.bootstrap()
    assert p0.request("POST", "/api/agent/plan-previews", json=body, csrf=False).status_code == 401
    preview_response = p0.request("POST", "/api/agent/plan-previews", json=body)
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert "13800138000" not in preview_response.text
    assert set(preview) == {"preview_id", "preview_fingerprint", "instruction", "outbound_text",
                            "provider", "model", "expires_at_utc", "max_output_tokens"}
    authorization = {"preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    assert p0.request("POST", "/api/agent/plans/execute", json=authorization,
                      csrf=False).status_code == 401
    response = p0.request("POST", "/api/agent/plans/execute", json=authorization)
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "provider_unavailable"}
    assert "13800138000" not in response.text
    assert p0.request("POST", "/api/agent/plans/execute", json=authorization).status_code == 403


def test_ai_pending_cancel_is_session_csrf_protected_and_one_shot(p0):
    p0.start()
    p0.bootstrap()
    preview = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "创业"}).json()
    path = f'/api/agent/plan-previews/{preview["preview_id"]}/cancel'
    assert p0.request("POST", path, json={}, csrf=False).status_code == 401
    assert p0.request("POST", path, json={}).status_code == 200
    response = p0.request("POST", "/api/agent/plans/execute", json={
        "preview_id": preview["preview_id"], "preview_fingerprint": preview["preview_fingerprint"],
        "confirmed": True})
    assert response.status_code == 403


def test_ai_success_returns_existing_agent_plan_shape_and_daily_limit_code(p0):
    from chat_to_howto.providers.fake import FakeProvider
    from chat_to_howto.services.ai_agent_planning import AIAgentPlanningService
    p0.start()
    p0.bootstrap()
    provider = FakeProvider()
    factories = []
    def factory(name):
        factories.append(name)
        return provider
    p0.app.state.ai_agent_planning_service = AIAgentPlanningService(
        repository=p0.app.state.chat_repository, provider_factory=factory,
        provider="fake", model="fixture-model", clock=p0.clock, max_daily_provider_requests=1)
    preview_response = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "提取创业想法"})
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert factories == []
    authorization = {"preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    response = p0.request("POST", "/api/agent/plans/execute", json=authorization)
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"instruction", "goal", "keywords", "summary_type", "all_content",
                           "needs_clarification", "notices"}
    assert result["keywords"] == ["创业"] and result["summary_type"] == "ideas"
    assert result["instruction"] == preview["instruction"]
    assert result["all_content"] is False and result["needs_clarification"] is False
    assert provider.request_payloads[0].decode() == preview["outbound_text"]
    assert response.headers["cache-control"] == "no-store"
    preview = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "创业"}).json()
    response = p0.request("POST", "/api/agent/plans/execute", json={"preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True})
    assert response.status_code == 429 and response.json()["detail"]["code"] == "daily_usage_limit"
    assert provider.call_count == 1


@pytest.mark.parametrize("confirmation,status", [(1, 422), ("true", 422), (False, 403)])
def test_ai_confirmation_is_explicit_strict_bool_and_does_not_consume_valid_preview(p0, confirmation, status):
    p0.start()
    p0.bootstrap()
    preview = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "创业"}).json()
    authorization = {"preview_id": preview["preview_id"],
        "preview_fingerprint": preview["preview_fingerprint"], "confirmed": confirmation}
    assert p0.request("POST", "/api/agent/plans/execute", json=authorization).status_code == status
    authorization["confirmed"] = True
    assert p0.request("POST", "/api/agent/plans/execute", json=authorization).status_code == 503


def test_ai_preview_and_cancel_reject_extra_private_fields_without_echo(p0):
    p0.start()
    p0.bootstrap()
    bad = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "创业", "extra": "private-field"})
    assert bad.status_code == 422 and "private-field" not in bad.text
    preview = p0.request("POST", "/api/agent/plan-previews", json={"instruction": "创业"}).json()
    path = f'/api/agent/plan-previews/{preview["preview_id"]}/cancel'
    bad = p0.request("POST", path, json={"extra": "private-field"})
    assert bad.status_code == 422 and "private-field" not in bad.text
    assert p0.request("POST", path, json={}).json() == {"cancelled": True}
    assert p0.request("POST", path, json={}).json() == {"cancelled": False}
