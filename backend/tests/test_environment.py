"""Behavior tests for the privacy-preserving environment inventory."""

from __future__ import annotations

import json
import plistlib
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chat_to_howto.environment import LocalSystemProbe, collect_environment


@dataclass(frozen=True)
class FixedSystemProbe:
    """A non-host probe so architecture tests never inspect the test machine."""

    system_name: str
    release_value: str = "release"
    version_value: str = "version"
    process_value: str = "arm64"
    rosetta_value: bool | None = False
    release_error: Exception | None = None
    version_error: Exception | None = None
    rosetta_error: Exception | None = None

    def system(self) -> str:
        return self.system_name

    def release(self) -> str:
        if self.release_error is not None:
            raise self.release_error
        return self.release_value

    def version(self) -> str:
        if self.version_error is not None:
            raise self.version_error
        return self.version_value

    def process_architecture(self) -> str:
        return self.process_value

    def rosetta_translated(self) -> bool | None:
        if self.rosetta_error is not None:
            raise self.rosetta_error
        return self.rosetta_value


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
                report = collect_environment(
                    {"qq": app_path},
                    system_probe=FixedSystemProbe("Darwin", process_value="arm64", rosetta_value=False),
                ).public_dict()

        serialized = json.dumps(report, sort_keys=True)
        self.assertEqual(set(report), {"os", "applications", "tools", "issues"})
        self.assertEqual(
            report["applications"],
            {"qq": {"version": "6.9.1", "architecture": "arm64", "installed": True}},
        )
        self.assertEqual(
            set(report["os"]),
            {
                "system",
                "release",
                "version",
                "architecture",
                "physical_architecture",
                "process_architecture",
                "rosetta_translated",
            },
        )
        self.assertEqual(set(report["tools"]["python3"]), {"version", "architecture", "installed"})
        self.assertEqual(report["issues"], [])
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
            {"version": "unknown", "architecture": "unknown", "installed": True},
        )
        self.assertEqual([issue["code"] for issue in report["issues"]], ["environment_detection_incomplete"])
        self.assertNotIn(str(plist_path), json.dumps(report, sort_keys=True))

    def test_valid_non_dictionary_plist_returns_unknown_metadata(self) -> None:
        """A plist list is valid data but cannot provide bundle metadata."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_path = Path(temporary_directory) / "QQ.app"
            plist_path = app_path / "Contents" / "Info.plist"
            plist_path.parent.mkdir(parents=True)
            with plist_path.open("wb") as stream:
                plistlib.dump(["valid", "plist", "array"], stream)

            report = collect_environment({"qq": app_path}).public_dict()

        self.assertEqual(
            report["applications"]["qq"],
            {"version": "unknown", "architecture": "unknown", "installed": True},
        )

    def test_missing_optional_source_bundles_are_unavailable_without_an_environment_issue(self) -> None:
        """Absent QQ/WeChat tools are source setup work, not failed metadata detection."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_paths = {
                name: Path(temporary_directory) / f"{name}.app"
                for name in ("qq", "wechat", "qce", "tracememo")
            }
            report = collect_environment(
                missing_paths,
                system_probe=FixedSystemProbe("Darwin", process_value="arm64", rosetta_value=False),
            ).public_dict()

        self.assertEqual(
            report["applications"],
            {
                name: {"version": "unavailable", "architecture": "unavailable", "installed": False}
                for name in ("qce", "qq", "tracememo", "wechat")
            },
        )
        self.assertEqual(report["issues"], [])
        self.assertNotIn(temporary_directory, json.dumps(report, sort_keys=True))

    def test_existing_damaged_bundle_is_unknown_and_triggers_one_environment_issue(self) -> None:
        """A present bundle with unreadable metadata is distinct from not being installed."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_path = Path(temporary_directory) / "QQ.app"
            plist_path = app_path / "Contents" / "Info.plist"
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("damaged plist", encoding="utf-8")
            report = collect_environment(
                {"qq": app_path},
                system_probe=FixedSystemProbe("Darwin", process_value="arm64", rosetta_value=False),
            ).public_dict()

        self.assertEqual(
            report["applications"]["qq"],
            {"version": "unknown", "architecture": "unknown", "installed": True},
        )
        self.assertEqual([issue["code"] for issue in report["issues"]], ["environment_detection_incomplete"])
        self.assertNotIn(temporary_directory, json.dumps(report, sort_keys=True))

    def test_native_arm64_reports_native_physical_and_process_architecture(self) -> None:
        """An ARM64 process is native only when the Rosetta probe is explicitly false."""
        report = collect_environment(
            {}, system_probe=FixedSystemProbe("Darwin", process_value="arm64", rosetta_value=False)
        ).public_dict()

        self.assertEqual(report["os"]["physical_architecture"], "arm64")
        self.assertEqual(report["os"]["process_architecture"], "arm64")
        self.assertFalse(report["os"]["rosetta_translated"])
        self.assertEqual(report["issues"], [])

    def test_local_native_arm64_does_not_call_the_rosetta_sysctl(self) -> None:
        """An arm64 process cannot be Rosetta-translated and needs no sysctl fallback."""
        calls: list[tuple[object, ...]] = []

        def forbidden_runner(*args: object, **_kwargs: object) -> object:
            calls.append(args)
            raise AssertionError("native arm64 must not run the Rosetta probe")

        probe = LocalSystemProbe(
            system_reader=lambda: "Darwin",
            release_reader=lambda: "release",
            version_reader=lambda: "version",
            machine_reader=lambda: "arm64",
            command_runner=forbidden_runner,
        )
        report = collect_environment({}, system_probe=probe).public_dict()

        self.assertEqual(calls, [])
        self.assertEqual(report["os"]["physical_architecture"], "arm64")
        self.assertEqual(report["os"]["process_architecture"], "arm64")
        self.assertFalse(report["os"]["rosetta_translated"])
        self.assertEqual(report["issues"], [])

    def test_native_arm64_ignores_an_impossible_rosetta_true_claim(self) -> None:
        """A macOS arm64 process is conclusive even if a probe reports impossible data."""
        report = collect_environment(
            {}, system_probe=FixedSystemProbe("Darwin", process_value=" arm64 ", rosetta_value=True)
        ).public_dict()

        self.assertEqual(report["os"]["physical_architecture"], "arm64")
        self.assertEqual(report["os"]["process_architecture"], "arm64")
        self.assertFalse(report["os"]["rosetta_translated"])
        self.assertEqual(report["issues"], [])

    def test_rosetta_x86_process_is_reported_as_arm64_physical_hardware(self) -> None:
        """Rosetta must not make an Apple Silicon Mac look like an Intel Mac."""
        report = collect_environment(
            {}, system_probe=FixedSystemProbe("Darwin", process_value="x86_64", rosetta_value=True)
        ).public_dict()

        self.assertEqual(report["os"]["physical_architecture"], "arm64")
        self.assertEqual(report["os"]["process_architecture"], "x86_64")
        self.assertTrue(report["os"]["rosetta_translated"])
        self.assertEqual(report["issues"], [])

    def test_intel_mac_and_non_macos_do_not_claim_rosetta_or_target_acceptance(self) -> None:
        """Non-Rosetta x86 architecture stays x86 without implying target-device acceptance."""
        for probe in (
            FixedSystemProbe("Darwin", process_value="x86_64", rosetta_value=False),
            FixedSystemProbe("Linux", process_value="x86_64", rosetta_value=False),
        ):
            with self.subTest(system=probe.system_name):
                report = collect_environment({}, system_probe=probe).public_dict()
                self.assertEqual(report["os"]["physical_architecture"], "x86_64")
                self.assertEqual(report["os"]["process_architecture"], "x86_64")
                self.assertFalse(report["os"]["rosetta_translated"])
                self.assertEqual(report["issues"], [])

    def test_undetectable_rosetta_state_returns_one_safe_actionable_issue(self) -> None:
        """A translated-or-Intel ambiguity must fail closed with one public issue."""
        report = collect_environment(
            {}, system_probe=FixedSystemProbe("Darwin", process_value="x86_64", rosetta_value=None)
        ).public_dict()

        self.assertEqual(report["os"]["physical_architecture"], "unknown")
        self.assertEqual(report["os"]["process_architecture"], "x86_64")
        self.assertIsNone(report["os"]["rosetta_translated"])
        self.assertEqual(
            report["issues"],
            [
                {
                    "code": "environment_detection_incomplete",
                    "message": "Could not collect all safe environment metadata; run the local audit on the target Mac.",
                }
            ],
        )

    def test_probe_errors_are_redacted_and_grouped_into_the_same_issue(self) -> None:
        """Sysctl errors may contain host details and never cross the public boundary."""
        sentinel = "SYSCTL_DENIED_SENTINEL"
        report = collect_environment(
            {},
            system_probe=FixedSystemProbe(
                "Darwin",
                process_value="x86_64",
                rosetta_error=RuntimeError(sentinel),
            ),
        ).public_dict()

        serialized = json.dumps(report, sort_keys=True)
        self.assertEqual(report["os"]["physical_architecture"], "unknown")
        self.assertIsNone(report["os"]["rosetta_translated"])
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(report["issues"][0]["code"], "environment_detection_incomplete")
        self.assertNotIn(sentinel, serialized)

    def test_unknown_operating_system_does_not_claim_non_rosetta_architecture(self) -> None:
        """A failed OS probe cannot safely be treated as a non-macOS host."""
        report = collect_environment(
            {},
            system_probe=FixedSystemProbe("unknown", process_value="x86_64", rosetta_value=False),
        ).public_dict()

        self.assertEqual(report["os"]["physical_architecture"], "unknown")
        self.assertIsNone(report["os"]["rosetta_translated"])
        self.assertEqual([issue["code"] for issue in report["issues"]], ["environment_detection_incomplete"])

    def test_macos_probe_uses_only_the_fixed_rosetta_sysctl_and_treats_bad_results_as_unknown(self) -> None:
        """No host-derived values may become shell arguments or a false Rosetta verdict."""
        observed_arguments: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def malformed_runner(args: tuple[str, ...], **kwargs: object) -> object:
            observed_arguments.append((args, kwargs))
            return type("Result", (), {"returncode": 0, "stdout": "not-a-bool"})()

        probe = LocalSystemProbe(
            system_reader=lambda: "Darwin",
            release_reader=lambda: "release",
            version_reader=lambda: "version",
            machine_reader=lambda: "x86_64",
            command_runner=malformed_runner,
        )
        report = collect_environment({}, system_probe=probe).public_dict()

        self.assertEqual(observed_arguments[0][0], ("/usr/sbin/sysctl", "-in", "sysctl.proc_translated"))
        self.assertEqual(report["os"]["physical_architecture"], "unknown")
        self.assertIsNone(report["os"]["rosetta_translated"])
        self.assertEqual(len(report["issues"]), 1)

    def test_missing_denied_and_malformed_sysctl_results_share_the_unknown_verdict(self) -> None:
        """Operational probe failures must not make an x86 process look like Intel hardware."""
        scenarios: tuple[object, ...] = (
            FileNotFoundError("sysctl missing"),
            type("Denied", (), {"returncode": 13, "stdout": "permission denied"})(),
            type("Malformed", (), {"returncode": 0, "stdout": "unexpected"})(),
        )
        for scenario in scenarios:
            def runner(*_args: object, _scenario: object = scenario, **_kwargs: object) -> object:
                if isinstance(_scenario, Exception):
                    raise _scenario
                return _scenario

            probe = LocalSystemProbe(
                system_reader=lambda: "Darwin",
                release_reader=lambda: "release",
                version_reader=lambda: "version",
                machine_reader=lambda: "x86_64",
                command_runner=runner,
            )
            with self.subTest(scenario=type(scenario).__name__):
                report = collect_environment({}, system_probe=probe).public_dict()
                self.assertEqual(report["os"]["physical_architecture"], "unknown")
                self.assertIsNone(report["os"]["rosetta_translated"])
                self.assertEqual([issue["code"] for issue in report["issues"]], ["environment_detection_incomplete"])

    def test_release_and_version_probe_failures_share_one_redacted_environment_issue(self) -> None:
        """Any missing safe system field is visible as one generic, non-sensitive issue."""
        sentinel = "VERSION_PROBE_SECRET"
        for probe in (
            FixedSystemProbe("Darwin", release_error=RuntimeError(sentinel)),
            FixedSystemProbe("Darwin", version_error=RuntimeError(sentinel)),
            FixedSystemProbe(
                "Darwin",
                release_error=RuntimeError(sentinel),
                version_error=RuntimeError(sentinel),
            ),
        ):
            with self.subTest(probe=probe):
                report = collect_environment({}, system_probe=probe).public_dict()
                serialized = json.dumps(report, sort_keys=True)
                self.assertEqual(report["os"]["physical_architecture"], "arm64")
                self.assertFalse(report["os"]["rosetta_translated"])
                self.assertEqual(len(report["issues"]), 1)
                self.assertEqual(report["issues"][0]["code"], "environment_detection_incomplete")
                self.assertNotIn(sentinel, serialized)

    def test_architecture_aliases_and_blank_values_are_normalized_or_fail_closed(self) -> None:
        """Architecture spelling differences are safe, while blank data is never guessed."""
        for process, expected in ((" AARCH64 ", "arm64"), (" amd64 ", "x86_64")):
            with self.subTest(process=process):
                report = collect_environment(
                    {}, system_probe=FixedSystemProbe("Linux", process_value=process)
                ).public_dict()
                self.assertEqual(report["os"]["process_architecture"], expected)
                self.assertEqual(report["os"]["physical_architecture"], expected)
                self.assertEqual(report["issues"], [])

        blank = collect_environment(
            {}, system_probe=FixedSystemProbe("Darwin", process_value="  ", rosetta_value=False)
        ).public_dict()
        self.assertEqual(blank["os"]["process_architecture"], "unknown")
        self.assertEqual(blank["os"]["physical_architecture"], "unknown")
        self.assertEqual(len(blank["issues"]), 1)
        self.assertEqual(blank["issues"][0]["code"], "environment_detection_incomplete")
