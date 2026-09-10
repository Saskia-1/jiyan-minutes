"""Behavior tests for the privacy-preserving environment inventory."""

from __future__ import annotations

import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chat_to_howto.environment import collect_environment


class EnvironmentInventoryTests(unittest.TestCase):
    def test_public_report_contains_only_safe_version_and_architecture_metadata(self) -> None:
        """A leak of a path, username, plist field, or environment value is a bug."""
        home_path = "/Users/alice"
        username = "alice"
        environment_secret = "ENVIRONMENT_SENTINEL_DO_NOT_DISCLOSE"
        plist_secret = "PLIST_SENTINEL_DO_NOT_DISCLOSE"

        with tempfile.TemporaryDirectory() as temporary_directory:
            app_path = Path(temporary_directory) / "QQ.app"
            plist_path = app_path / "Contents" / "Info.plist"
            plist_path.parent.mkdir(parents=True)
            with plist_path.open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "6.9.1",
                        "LSArchitecturePriority": ["arm64", "x86_64"],
                        "PrivateDebugValue": plist_secret,
                    },
                    stream,
                )

            with patch.dict("os.environ", {"ENV_AUDIT_SENTINEL": environment_secret}):
                report = collect_environment({"qq": app_path}).public_dict()

        serialized = json.dumps(report, sort_keys=True)
        self.assertEqual(set(report), {"os", "applications", "tools"})
        self.assertEqual(report["applications"], {"qq": {"version": "6.9.1", "architecture": "arm64"}})
        self.assertEqual(set(report["os"]), {"system", "release", "version", "architecture"})
        self.assertEqual(set(report["tools"]["python3"]), {"version", "architecture"})
        for forbidden in (home_path, username, environment_secret, plist_secret, str(plist_path)):
            self.assertNotIn(forbidden, serialized)

    def test_missing_or_malformed_plists_return_unknown_metadata_without_disclosing_paths(self) -> None:
        """The collector must not leak a supplied application path in an error report."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            malformed_app = Path(temporary_directory) / "TraceMemo.app"
            plist_path = malformed_app / "Contents" / "Info.plist"
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("not a plist", encoding="utf-8")

            report = collect_environment({"tracememo": malformed_app}).public_dict()

        self.assertEqual(
            report["applications"]["tracememo"],
            {"version": "unknown", "architecture": "unknown"},
        )
        self.assertNotIn(str(plist_path), json.dumps(report, sort_keys=True))

