"""Behavior tests for typed, explicit application settings."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chat_to_howto.config import Settings


class SettingsTests(unittest.TestCase):
    def test_load_uses_safe_loopback_defaults_when_no_known_settings_are_configured(self) -> None:
        """Changing a default away from loopback must make this test fail."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.load()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.timezone, "Asia/Shanghai")
        self.assertIsNone(settings.data_dir)
        self.assertIsNone(settings.export_root)
        self.assertEqual(settings.qce_base_url, "http://127.0.0.1:40653")
        self.assertEqual(settings.tracememo_base_url, "http://127.0.0.1:6131")
        self.assertEqual(settings.llm_provider, "unconfigured")
        self.assertEqual(settings.llm_model, "deepseek-v4-flash")
        self.assertEqual(settings.llm_daily_request_limit, 100)

    def test_load_reads_only_declared_configuration_names(self) -> None:
        """An unrelated environment value must not influence typed configuration."""
        with patch.dict(
            os.environ,
            {
                "APP_HOST": "127.0.0.1",
                "APP_TIMEZONE": "UTC",
                "APP_DATA_DIR": "/private/tmp/app-data",
                "EXPORT_ROOT": "/private/tmp/exports",
                "QCE_BASE_URL": "http://127.0.0.1:4567",
                "TRACEMEMO_BASE_URL": "http://127.0.0.1:6789",
                "LLM_PROVIDER": "unconfigured",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
                "LLM_DAILY_REQUEST_LIMIT": "12",
                "UNRELATED_SECRET": "DO_NOT_READ_OR_REPORT",
            },
            clear=True,
        ):
            settings = Settings.load()

        self.assertEqual(settings.timezone, "UTC")
        self.assertEqual(settings.data_dir, Path("/private/tmp/app-data"))
        self.assertEqual(settings.export_root, Path("/private/tmp/exports"))
        self.assertEqual(settings.qce_base_url, "http://127.0.0.1:4567")
        self.assertEqual(settings.tracememo_base_url, "http://127.0.0.1:6789")
        self.assertEqual(settings.llm_model, "deepseek-v4-pro")
        self.assertEqual(settings.llm_daily_request_limit, 12)
        self.assertNotIn("DO_NOT_READ_OR_REPORT", repr(settings))

    def test_load_rejects_a_non_loopback_bind_host(self) -> None:
        """Accepting a public bind address would expose the local service."""
        with patch.dict(os.environ, {"APP_HOST": "0.0.0.0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "APP_HOST must be 127.0.0.1"):
                Settings.load()

    def test_load_rejects_invalid_daily_request_limits(self) -> None:
        for value in ("0", "10001", "1.5", "-1", "yes"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"LLM_DAILY_REQUEST_LIMIT": value}, clear=True):
                    with self.assertRaisesRegex(ValueError, "LLM_DAILY_REQUEST_LIMIT"):
                        Settings.load()
