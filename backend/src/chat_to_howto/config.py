"""Explicit, non-secret application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration read from a small, declared allowlist of environment names."""

    host: str
    timezone: str
    data_dir: Path | None = field(repr=False)
    export_root: Path | None = field(repr=False)
    qce_base_url: str
    tracememo_base_url: str
    llm_provider: str
    llm_model: str = "deepseek-v4-flash"
    llm_daily_request_limit: int = 100

    @classmethod
    def load(cls) -> "Settings":
        """Load non-secret settings without enumerating the process environment."""
        provider = os.getenv("LLM_PROVIDER", "unconfigured")
        return cls(
            host=_loopback_host(),
            timezone=os.getenv("APP_TIMEZONE", "Asia/Shanghai"),
            data_dir=_optional_path("APP_DATA_DIR"),
            export_root=_optional_path("EXPORT_ROOT"),
            qce_base_url=os.getenv("QCE_BASE_URL", "http://127.0.0.1:40653"),
            tracememo_base_url=os.getenv("TRACEMEMO_BASE_URL", "http://127.0.0.1:6131"),
            llm_provider=provider,
            llm_model=os.getenv(
                "DEEPSEEK_MODEL",
                "deterministic" if provider == "fake" else "deepseek-v4-flash",
            ),
            llm_daily_request_limit=_positive_int("LLM_DAILY_REQUEST_LIMIT", 100),
        )


def _optional_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value) if value else None


def _loopback_host() -> str:
    host = os.getenv("APP_HOST", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("APP_HOST must be 127.0.0.1")
    return host


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"{name} must be a positive integer")
    value = int(raw)
    if not 1 <= value <= 10_000:
        raise ValueError(f"{name} must be between 1 and 10000")
    return value
