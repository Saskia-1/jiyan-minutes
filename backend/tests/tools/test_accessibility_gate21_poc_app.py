"""Safety and output contract for the final Gate 2.1 AX hierarchy app."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
APP = Path(
    os.environ.get(
        "CHAT_ACCESSIBILITY_GATE21_APP",
        ROOT
        / "tools"
        / "accessibility_poc"
        / "build"
        / "ChatAccessibilityGate21POC.app",
    )
)
EXECUTABLE = APP / "Contents" / "MacOS" / "chat-accessibility-gate21-poc"
INFO_PLIST = APP / "Contents" / "Info.plist"


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only POC")


def test_gate21_app_has_an_independent_stable_identity() -> None:
    """Catches overwriting the frozen Gate 2 bundle or shipping an unsigned app."""
    assert APP.is_dir(), f"missing app bundle: {APP}"
    assert EXECUTABLE.is_file(), f"missing app executable: {EXECUTABLE}"

    with INFO_PLIST.open("rb") as handle:
        info = plistlib.load(handle)

    assert info["CFBundleIdentifier"] == "com.chattohowto.accessibility-gate21-poc"
    assert info["CFBundleExecutable"] == "chat-accessibility-gate21-poc"
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
    assert "Identifier=com.chattohowto.accessibility-gate21-poc" in details.stderr
    assert "Signature=adhoc" in details.stderr

    entitlements = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", str(APP)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "<key>" not in entitlements.stdout


def test_gate21_app_imports_only_read_only_ax_entry_points() -> None:
    """Catches adding Accessibility mutation or low-level process APIs."""
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
        "_AXUIElementCopyAttributeNames",
        "_AXUIElementCopyAttributeValue",
        "_AXUIElementCopyAttributeValues",
        "_AXUIElementCopyParameterizedAttributeNames",
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


def test_gate21_synthetic_hierarchy_summary_is_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    """Catches raw-string output, missing caps, or broken hierarchy accounting."""
    stdout_path = tmp_path / "hierarchy.json"
    stderr_path = tmp_path / "hierarchy.err"

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
            "--synthetic-self-test",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert stderr_path.read_text(encoding="utf-8") == ""
    raw = stdout_path.read_text(encoding="utf-8")
    assert "AX_TEST_" not in raw
    assert "SYNTHETIC_UNKNOWN_CONTENT" not in raw

    report = json.loads(raw)
    assert report["mode"] == "gate21_synthetic_hierarchy_self_test"
    assert report["totalNodes"] == 7
    assert report["maximumDepthObserved"] == 3
    assert report["roleDistribution"] == {
        "AXGroup": 2,
        "AXScrollArea": 1,
        "AXStaticText": 3,
        "AXWindow": 1,
    }
    assert report["candidateContainerCounts"] == {
        "AXGroup": 2,
        "AXScrollArea": 1,
        "AXStaticText": 3,
        "AXTextArea": 0,
    }
    assert report["supportedTextAttributeNodeCount"] == 4
    assert report["markerFound"] == [True, True, True]
    assert report["markerOccurrenceCounts"] == [1, 1, 1]
    assert report["bounds"] == {
        "aggregateTextCharacters": 131072,
        "depth": 14,
        "nodes": 5000,
        "perAttributeCharacters": 1024,
    }
    assert report["boundsExceeded"] is False
    assert report["rawStringsEmitted"] is False
    assert report["textAttributes"]["AXValue"]["supportedNodeCount"] == 3
    assert report["textAttributes"]["AXValue"]["nonEmptyStringCount"] == 3
    assert report["textAttributes"]["AXHelp"]["emptyStringCount"] == 1
    assert report["safety"] == {
        "accessibilityPromptRequested": False,
        "clientContentInspected": False,
        "dataPersistedByApp": False,
        "realPrivateChatsIntentionallyRead": False,
        "scrollPerformed": False,
        "uiMutationPerformed": False,
    }


def test_gate21_rejects_live_inspection_without_explicit_acknowledgement(
    tmp_path: Path,
) -> None:
    """Catches accidental live traversal without the nonsensitive-chat gate."""
    stdout_path = tmp_path / "rejected.out"
    stderr_path = tmp_path / "rejected.err"

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
            "--target",
            "qq",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "acknowledge-nonsensitive-test-chat" in stderr_path.read_text(
        encoding="utf-8"
    )
    assert stdout_path.read_text(encoding="utf-8") == ""
