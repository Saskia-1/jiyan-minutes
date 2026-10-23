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
