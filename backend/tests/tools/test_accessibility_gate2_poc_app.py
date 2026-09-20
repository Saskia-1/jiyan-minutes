"""Bundle, safety, and synthetic behavior contract for the Gate 2 AX app."""

from __future__ import annotations

import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "tools" / "accessibility_poc" / "build" / "ChatAccessibilityGate2POC.app"
EXECUTABLE = APP / "Contents" / "MacOS" / "chat-accessibility-gate2-poc"
INFO_PLIST = APP / "Contents" / "Info.plist"


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only POC")


def test_gate2_app_has_stable_identity_and_valid_signature() -> None:
    assert APP.is_dir(), f"missing app bundle: {APP}"
    assert EXECUTABLE.is_file(), f"missing app executable: {EXECUTABLE}"

    with INFO_PLIST.open("rb") as handle:
        info = plistlib.load(handle)

    assert info["CFBundleIdentifier"] == "com.chattohowto.accessibility-gate2-poc"
    assert info["CFBundleExecutable"] == "chat-accessibility-gate2-poc"
    assert info["CFBundlePackageType"] == "APPL"
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP)],
        check=True,
        capture_output=True,
        text=True,
    )
    details = subprocess.run(
        ["codesign", "-dvvv", str(APP)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Identifier=com.chattohowto.accessibility-gate2-poc" in details.stderr
    assert "Signature=adhoc" in details.stderr

    entitlements = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", str(APP)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "<key>" not in entitlements.stdout


def test_gate2_app_imports_read_only_accessibility_apis() -> None:
    assert EXECUTABLE.is_file(), f"missing app executable: {EXECUTABLE}"

    symbols = subprocess.run(
        ["nm", "-u", str(EXECUTABLE)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    for required in (
        "_AXIsProcessTrusted",
        "_AXUIElementCreateApplication",
        "_AXUIElementCopyAttributeValue",
        "_AXUIElementCopyAttributeValues",
        "_AXUIElementCopyParameterizedAttributeValue",
    ):
        assert required in symbols

    for forbidden in (
        "AXUIElementPerformAction",
        "AXUIElementSetAttributeValue",
        "CGEventPost",
        "task_for_pid",
        "ptrace",
        "mach_vm",
        "process_vm",
        "dlopen",
        "AXMakeProcessTrusted",
    ):
        assert forbidden not in symbols


def test_gate2_synthetic_classifier_via_launch_services(tmp_path: Path) -> None:
    assert APP.is_dir(), f"missing app bundle: {APP}"
    stdout_path = tmp_path / "classifier.json"
    stderr_path = tmp_path / "classifier.err"

    subprocess.run(
        [
            "open",
            "-n",
            "-W",
            "--stdout",
            str(stdout_path),
            "--stderr",
            str(stderr_path),
            str(APP),
            "--args",
            "--classifier-self-test",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert stderr_path.read_text(encoding="utf-8") == ""
    raw = stdout_path.read_text(encoding="utf-8")
    assert "AX_TEST_" not in raw
    report = json.loads(raw)

    assert report["ordered"]["messageText"] == "YES"
    assert report["ordered"]["stableOrder"] == "YES"
    assert report["ordered"]["sender"] == "PARTIAL"
    assert report["ordered"]["timestamp"] == "PARTIAL"
    assert report["partial"]["messageText"] == "PARTIAL"
    assert report["missing"]["messageText"] == "NO"
