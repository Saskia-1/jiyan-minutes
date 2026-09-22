"""Production assembly for the loopback-only macOS application."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chat_to_howto.config import Settings, load_deepseek_environment
from chat_to_howto.db import Database
from chat_to_howto.domain.consent import ConsentRecord, SecretAccessScope
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.main import create_app
from chat_to_howto.providers.deepseek import DeepSeekProvider
from chat_to_howto.security.secrets import KeychainSecretStore
from chat_to_howto.sources.factory import LocalSourceAdapterFactory


_CONFIG_KEYS = {
    "schema_version",
    "app_timezone",
    "app_data_dir",
    "export_root",
    "llm_provider",
    "deepseek_model",
    "llm_daily_request_limit",
}
_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
_DEFAULT_CONFIG = Path.home() / "Library" / "Application Support" / "Chat to How-To" / "runtime-config.json"
_GATE1_SOURCE_BLOCKERS = {
    "qq": [
        "QCE v6.2.10 is blocked by the Ultra Gate 1 NO-GO verdict; initialization and real-device access are disabled."
    ],
    "wechat": [
        "TraceMemo v2.3.0 is blocked by the Ultra Gate 1 NO-GO verdict; initialization and real-device access are disabled."
    ],
}


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    settings: Settings
    origin: str


def load_runtime_config(path: Path, *, project_root: Path) -> RuntimeConfig:
    """Load one private, non-secret JSON file without scanning the environment."""
    config_path = Path(path)
    metadata = config_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or config_path.is_symlink():
        raise PermissionError("runtime configuration must be a regular private file")
    if metadata.st_mode & 0o077:
        raise PermissionError("runtime configuration must not be group/world accessible")
    if metadata.st_size > 64 * 1024:
        raise ValueError("runtime configuration is too large")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("runtime configuration is invalid") from None
    if not isinstance(raw, dict) or set(raw) != _CONFIG_KEYS or raw.get("schema_version") != 1:
        raise ValueError("runtime configuration schema is invalid")
    timezone_name = raw.get("app_timezone")
    provider = raw.get("llm_provider")
    model = raw.get("deepseek_model")
    limit = raw.get("llm_daily_request_limit")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("application timezone is invalid")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ValueError("application timezone is invalid") from None
    if provider not in {"unconfigured", "deepseek"}:
        raise ValueError("LLM provider is invalid")
    if model not in _MODELS:
        raise ValueError("DeepSeek model is invalid")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
        raise ValueError("daily request limit is invalid")
    source_root = Path(project_root).resolve(strict=True)
    data_dir = _private_external_directory(raw.get("app_data_dir"), source_root)
    export_root = _private_external_directory(raw.get("export_root"), source_root)
    if data_dir == export_root:
        raise ValueError("data and export directories must be different")
    settings = Settings(
        host="127.0.0.1",
        timezone=timezone_name,
        data_dir=data_dir,
        export_root=export_root,
        qce_base_url="http://127.0.0.1:40653",
        tracememo_base_url="http://127.0.0.1:6131",
        llm_provider=provider,
        llm_model=model,
        llm_daily_request_limit=limit,
    )
    return RuntimeConfig(settings=settings, origin="http://127.0.0.1:8765")


def create_runtime_app():
    project_root = Path(__file__).resolve().parents[3]
    load_deepseek_environment(project_root)
    config_value = os.getenv("CHAT_TO_HOWTO_CONFIG")
    config_path = Path(config_value).expanduser() if config_value else _DEFAULT_CONFIG
    runtime = load_runtime_config(config_path, project_root=project_root)
    settings = runtime.settings
    if settings.data_dir is None or settings.export_root is None:
        raise RuntimeError("runtime directories are unavailable")
    secret_store = KeychainSecretStore(service="com.openai.chat-to-howto.source-token")
    base_urls = {"qq": settings.qce_base_url, "wechat": settings.tracememo_base_url}

    def adapter_factory(
        source_id: str,
        account_id: str,
        *,
        token_access: SecretAccessScope | None = None,
        token_consent: ConsentRecord | None = None,
    ):
        if not isinstance(token_access, SecretAccessScope):
            raise PermissionError("source authorization is unavailable")
        selected = SourceFingerprint(
            client_version=token_access.client_version,
            tool_version=token_access.tool_version,
            account_version=token_access.account_version,
        )
        placeholder = SourceFingerprint(
            client_version="unconfigured",
            tool_version="unconfigured",
            account_version="unconfigured",
        )
        fingerprints = {"qq": placeholder, "wechat": placeholder, source_id: selected}
        factory = LocalSourceAdapterFactory(
            secret_store=secret_store,
            base_urls=base_urls,
            fingerprints=fingerprints,
            clock=lambda: datetime.now(timezone.utc),
        )
        return factory(
            source_id,
            account_id,
            token_access=token_access,
            token_consent=token_consent,
        )

    frontend_dist = project_root / "frontend" / "dist"

    def generation_provider_factory(provider_name: str):
        if provider_name != "deepseek" or provider_name != settings.llm_provider:
            raise ValueError("generation provider is not configured")
        return DeepSeekProvider.from_environment(timeout_seconds=180)

    return create_app(
        settings=settings,
        database=Database(settings.data_dir / "platform.sqlite3"),
        adapter_factory=adapter_factory,
        secret_store=secret_store,
        clock=lambda: datetime.now(timezone.utc),
        output_roots={"local-exports": settings.export_root},
        source_root=project_root,
        origin=runtime.origin,
        run_jobs=True,
        frontend_dist=frontend_dist,
        generation_provider_factory=generation_provider_factory,
        setup_options={
            "approved_output_roots": [
                {"output_root_id": "local-exports", "label": "已批准的本地导出目录"}
            ],
            "source_disclosures": {
                source_id: {
                    "revision": "production-local-contract-2",
                    "sections": {
                        "source_version": "来源版本必须与当前配置和单次授权完全一致。",
                        "access_impact": "只使用受审查的只读本机接口；不会安装、登录、提权或修改客户端。",
                        "scope_overread": "上游可能按页或整秒过读；平台会再次按所选会话和半开时间范围过滤。",
                        "storage_cleanup": "项目数据、受管文件、上游缓存和系统权限必须分别清理。",
                        "permissions_network": "来源适配器只连接固定回环端口；外部 AI 仅在独立预览确认后调用。",
                        "action_stop": "授权过期、版本变化、范围不确定或来源错误都会停止当前操作。",
                    },
                    "blockers": list(_GATE1_SOURCE_BLOCKERS[source_id]),
                }
                for source_id in ("qq", "wechat")
            },
        },
    )


def _private_external_directory(value: object, project_root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("runtime directory is invalid")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("runtime directory must be an existing absolute directory")
    resolved = path.resolve(strict=True)
    if resolved == project_root or project_root in resolved.parents:
        raise ValueError("runtime directory must be outside the source tree")
    mode = resolved.stat().st_mode
    if mode & 0o077:
        raise PermissionError("runtime directory must not be group/world accessible")
    return resolved
