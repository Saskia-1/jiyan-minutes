"""Static bundle and API-surface contract for the Accessibility trust-check app."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "tools" / "accessibility_poc" / "build" / "ChatAccessibilityPOC.app"
EXECUTABLE = APP / "Contents" / "MacOS" / "chat-accessibility-poc"
INFO_PLIST = APP / "Contents" / "Info.plist"


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only POC")


def test_app_bundle_has_stable_identity_and_valid_signature() -> None:
    assert APP.is_dir(), f"missing app bundle: {APP}"
    assert EXECUTABLE.is_file(), f"missing app executable: {EXECUTABLE}"

    with INFO_PLIST.open("rb") as handle:
        info = plistlib.load(handle)

    assert info["CFBundleIdentifier"] == "com.chattohowto.accessibility-poc"
    assert info["CFBundleExecutable"] == "chat-accessibility-poc"
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
    assert "Identifier=com.chattohowto.accessibility-poc" in details.stderr
    assert "Signature=adhoc" in details.stderr

    entitlements = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", str(APP)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "<key>" not in entitlements.stdout


def test_app_executable_imports_only_accessibility_trust_check_apis() -> None:
    assert EXECUTABLE.is_file(), f"missing app executable: {EXECUTABLE}"

    undefined_symbols = subprocess.run(
        ["nm", "-u", str(EXECUTABLE)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "_AXIsProcessTrusted" in undefined_symbols
    assert "_AXIsProcessTrustedWithOptions" in undefined_symbols
    assert "_kAXTrustedCheckOptionPrompt" in undefined_symbols
    assert "_AXUIElement" not in undefined_symbols
    for forbidden in (
        "task_for_pid",
        "ptrace",
        "mach_vm",
        "process_vm",
        "dlopen",
        "AXMakeProcessTrusted",
    ):
        assert forbidden not in undefined_symbols
