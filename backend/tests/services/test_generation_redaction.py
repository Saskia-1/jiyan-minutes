"""Outbound redaction is deterministic and happens before preview/provider bytes."""

from chat_to_howto.services.generation_redaction import redact_outbound_text


def test_targeted_identifiers_are_redacted_without_rewriting_operational_numbers() -> None:
    source = "联系手机 13800138000，邮箱 user@example.com，QQ：12345678；导出第3到10页。"

    redacted = redact_outbound_text(source, policy_revision="text-only-outbound-v1")

    assert "13800138000" not in redacted
    assert "user@example.com" not in redacted
    assert "12345678" not in redacted
    assert "第3到10页" in redacted
    assert "[已脱敏手机号]" in redacted


def test_unknown_redaction_policy_fails_closed() -> None:
    import pytest

    with pytest.raises(ValueError, match="redaction policy"):
        redact_outbound_text("safe", policy_revision="unknown-policy")
