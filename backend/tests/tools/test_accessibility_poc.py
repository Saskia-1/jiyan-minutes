"""Static and executable safety contract for the macOS Accessibility POC."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "tools" / "accessibility_poc" / "main.m"


def test_accessibility_poc_uses_official_read_only_api_and_contains_no_redline_techniques() -> None:
    text = SOURCE.read_text(encoding="utf-8")

    assert "AXUIElementCreateApplication" in text
    assert "AXUIElementCopyAttributeValue" in text
    assert "AXIsProcessTrusted()" in text
    for forbidden in (
        "task_for_pid", "ptrace", "mach_vm", "process_vm", "SQLCipher",
        "WCDB", "Frida", "dlopen", "DYLD_INSERT_LIBRARIES", "csrutil",
        "AXUIElementPerformAction", "AXUIElementSetAttributeValue",
        "candidateText", "--reveal-text",
    ):
        assert forbidden not in text


def test_accessibility_self_test_does_not_request_or_read_target_content(tmp_path: Path) -> None:
    binary = tmp_path / "accessibility-poc"
    subprocess.run(
        ["xcrun", "clang", "-fobjc-arc", f"-fmodules-cache-path={tmp_path / 'module-cache'}", str(SOURCE),
         "-framework", "AppKit", "-framework", "ApplicationServices", "-o", str(binary)],
        check=True, capture_output=True, text=True,
    )
    completed = subprocess.run(
        [str(binary), "--self-test"], check=True, capture_output=True, text=True,
    )

    assert '"mode" : "self_test"' in completed.stdout
    assert '"contentRead" : false' in completed.stdout
    assert '"clientContentInspected" : false' in completed.stdout

    classified = subprocess.run(
        [str(binary), "--classifier-self-test"],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(classified.stdout)
    assert report["containerOnly"]["status"] == "NO_GO_NO_ACCESSIBLE_TEXT_CANDIDATES"
    assert report["containerOnly"]["messageText"] == "NO"
    label = report["unclassifiedWindowLabel"]
    assert label["status"] == "HUMAN_REVIEW_REQUIRED_TEXT_CANDIDATES_ARE_NOT_MESSAGES"
    assert label["messageText"] == "UNVERIFIED"
    assert label["sender"] == "UNVERIFIED"
    assert label["timestamp"] == "UNVERIFIED"
    assert label["stableOrder"] == "UNVERIFIED"
    assert "candidateText" not in label
    assert "Settings 10:20" not in classified.stdout
