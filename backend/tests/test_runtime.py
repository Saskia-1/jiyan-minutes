"""Production runtime configuration remains explicit, private, and local-only."""

from __future__ import annotations

import json
import os

import pytest

from chat_to_howto.config import Settings
import chat_to_howto.runtime as runtime_module
from chat_to_howto.runtime import RuntimeConfig, load_runtime_config


def _write_config(tmp_path, project_root, **changes):
    data = tmp_path / "private-data"
    output = tmp_path / "approved-output"
    data.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    config = data / "runtime-config.json"
    payload = {
        "schema_version": 1,
        "app_timezone": "Asia/Shanghai",
        "app_data_dir": str(data),
        "export_root": str(output),
        "llm_provider": "deepseek",
        "deepseek_model": "deepseek-v4-flash",
        "llm_daily_request_limit": 100,
    } | changes
    config.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(config, 0o600)
    return config, data, output


def test_runtime_config_loads_only_explicit_nonsecret_settings(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config, data, output = _write_config(tmp_path, project)

    loaded = load_runtime_config(config, project_root=project)

    assert loaded.settings.host == "127.0.0.1"
    assert loaded.settings.llm_provider == "deepseek"
    assert loaded.settings.llm_model == "deepseek-v4-flash"
    assert loaded.settings.llm_thinking == "disabled"
    assert loaded.settings.llm_reasoning_effort is None
    assert loaded.settings.llm_max_output_tokens == 16_000
    assert loaded.settings.data_dir == data.resolve()
    assert loaded.settings.export_root == output.resolve()
    assert loaded.origin == "http://127.0.0.1:8765"
    assert "api_key" not in repr(loaded).lower()


def test_runtime_config_accepts_explicit_thinking_acceptance_profile_without_changing_defaults(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config, _data, _output = _write_config(
        tmp_path, project, deepseek_model="deepseek-v4-pro",
        deepseek_thinking="enabled", deepseek_reasoning_effort="max",
        deepseek_max_output_tokens=64_000,
    )
    settings = load_runtime_config(config, project_root=project).settings

    assert settings.llm_model == "deepseek-v4-pro"
    assert settings.llm_thinking == "enabled"
    assert settings.llm_reasoning_effort == "max"
    assert settings.llm_max_output_tokens == 64_000


@pytest.mark.parametrize(
    "changes",
    [
        {"llm_provider": "fake"},
        {"deepseek_model": "moving-latest"},
        {"llm_daily_request_limit": 0},
        {"schema_version": 2},
        {"unexpected": "value"},
        {"deepseek_thinking": "enabled"},
        {"deepseek_thinking": "enabled", "deepseek_reasoning_effort": "max"},
        {"deepseek_reasoning_effort": "max"},
        {"deepseek_thinking": []},
        {"deepseek_reasoning_effort": []},
        {"deepseek_thinking": "enabled", "deepseek_reasoning_effort": "medium"},
        {"deepseek_thinking": "enabled", "deepseek_reasoning_effort": "max",
         "deepseek_max_output_tokens": 64_000},
        {"deepseek_max_output_tokens": 0},
        {"deepseek_max_output_tokens": True},
        {"deepseek_max_output_tokens": 400_000},
    ],
)
def test_runtime_config_rejects_unreviewed_or_invalid_values(tmp_path, changes):
    project = tmp_path / "project"
    project.mkdir()
    config, _data, _output = _write_config(tmp_path, project, **changes)
    with pytest.raises(ValueError):
        load_runtime_config(config, project_root=project)


def test_runtime_config_rejects_group_readable_file_and_project_internal_data(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config, _data, _output = _write_config(tmp_path, project)
    os.chmod(config, 0o640)
    with pytest.raises(PermissionError):
        load_runtime_config(config, project_root=project)

    internal = project / "data"
    internal.mkdir(mode=0o700)
    os.chmod(config, 0o600)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["app_data_dir"] = str(internal)
    config.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(config, 0o600)
    with pytest.raises(ValueError):
        load_runtime_config(config, project_root=project)


@pytest.mark.parametrize(("thinking", "effort", "timeout", "response_limit"), [
    ("disabled", None, 180, 1_048_576),
    ("enabled", "max", 600, 4_194_304),
])
def test_runtime_app_exposes_gate1_no_go_blockers_for_both_sources(
    tmp_path, monkeypatch, thinking, effort, timeout, response_limit
):
    data = tmp_path / "private-data"
    output = tmp_path / "approved-output"
    data.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    settings = Settings(
        host="127.0.0.1",
        timezone="Asia/Shanghai",
        data_dir=data,
        export_root=output,
        qce_base_url="http://127.0.0.1:40653",
        tracememo_base_url="http://127.0.0.1:6131",
        llm_provider="deepseek",
        llm_model="deepseek-v4-flash",
        llm_thinking=thinking,
        llm_reasoning_effort=effort,
    )
    captured = {}
    provider = object()
    monkeypatch.setattr(runtime_module, "load_deepseek_environment", lambda _root: True)

    class DeepSeekProviderStub:
        @classmethod
        def from_environment(cls, *, timeout_seconds, max_response_bytes):
            assert timeout_seconds == timeout
            assert max_response_bytes == response_limit
            return provider

    monkeypatch.setattr(
        runtime_module, "DeepSeekProvider", DeepSeekProviderStub, raising=False
    )
    monkeypatch.setattr(
        runtime_module,
        "load_runtime_config",
        lambda *_args, **_kwargs: RuntimeConfig(
            settings=settings, origin="http://127.0.0.1:8765"
        ),
    )
    monkeypatch.setattr(runtime_module, "KeychainSecretStore", lambda **_kwargs: object())
    monkeypatch.setattr(runtime_module, "Database", lambda _path: object())

    def capture_app(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime_module, "create_app", capture_app)

    runtime_module.create_runtime_app()

    disclosures = captured["setup_options"]["source_disclosures"]
    assert disclosures["qq"]["blockers"]
    assert disclosures["wechat"]["blockers"]
    assert "QCE" in " ".join(disclosures["qq"]["blockers"])
    assert "TraceMemo" in " ".join(disclosures["wechat"]["blockers"])
    provider_factory = captured["generation_provider_factory"]
    assert provider_factory("deepseek") is provider
    with pytest.raises(ValueError):
        provider_factory("another-provider")
