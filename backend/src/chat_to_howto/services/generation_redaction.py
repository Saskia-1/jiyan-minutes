"""Deterministic, local redaction for the first outbound text policy."""

from __future__ import annotations

import re


_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])")
_CN_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LABELLED_IDENTIFIER = re.compile(
    r"(?i)(?P<label>(?:QQ|QQ\s*号|微信号|WeChat\s*ID)\s*[:：]? ?\s*)(?P<value>[A-Za-z0-9_-]{5,32})"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_API_KEY = re.compile(r"\b(?:sk|dk)-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_LABELLED_SECRET = re.compile(
    r"(?i)(?P<label>(?:API\s*Key|Token|数据库密钥|DB\s*Key)\s*[:：] ?\s*)(?P<value>\S{6,})"
)


def redact_outbound_text(text: str, *, policy_revision: str) -> str:
    if policy_revision not in {"text-only-outbound-v1", "outbound-v1"}:
        raise ValueError("unsupported outbound redaction policy")
    if not isinstance(text, str):
        raise TypeError("outbound text must be text")
    value = _EMAIL.sub("[已脱敏邮箱]", text)
    value = _CN_MOBILE.sub("[已脱敏手机号]", value)
    value = _LABELLED_IDENTIFIER.sub(lambda match: f'{match.group("label")}[已脱敏账号]', value)
    value = _BEARER.sub("Bearer [已脱敏令牌]", value)
    value = _API_KEY.sub("[已脱敏 API Key]", value)
    value = _LABELLED_SECRET.sub(lambda match: f'{match.group("label")}[已脱敏秘密]', value)
    return value
