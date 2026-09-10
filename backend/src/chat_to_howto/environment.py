"""Safe environment metadata collection with no chat or account discovery."""

from __future__ import annotations

import platform
import plistlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


KNOWN_APPLICATION_PATHS: Mapping[str, Path] = MappingProxyType(
    {
        "qq": Path("/Applications/QQ.app"),
        "wechat": Path("/Applications/WeChat.app"),
        "qce": Path("/Applications/QQChatExporter.app"),
        "tracememo": Path("/Applications/TraceMemo.app"),
    }
)


@dataclass(frozen=True, slots=True)
class VersionArchitecture:
    version: str
    architecture: str

    def public_dict(self) -> dict[str, str]:
        return {"version": self.version, "architecture": self.architecture}


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    os: dict[str, str]
    applications: dict[str, VersionArchitecture]
    tools: dict[str, VersionArchitecture]

    def public_dict(self) -> dict[str, object]:
        """Return the intentionally small public metadata surface."""
        return {
            "os": dict(self.os),
            "applications": {
                name: metadata.public_dict() for name, metadata in self.applications.items()
            },
            "tools": {name: metadata.public_dict() for name, metadata in self.tools.items()},
        }


def collect_environment(app_paths: Mapping[str, Path]) -> EnvironmentReport:
    """Collect only OS, supplied app bundle, and fixed tool version metadata.

    ``app_paths`` enables synthetic fixtures in tests; callers should pass
    ``KNOWN_APPLICATION_PATHS`` in production. This function never scans for
    applications, accounts, message databases, or other user data.
    """
    architecture = _value_or_unknown(platform.machine())
    applications = {
        name: _read_application_metadata(path) for name, path in sorted(app_paths.items())
    }
    return EnvironmentReport(
        os={
            "system": _value_or_unknown(platform.system()),
            "release": _value_or_unknown(platform.release()),
            "version": _value_or_unknown(platform.version()),
            "architecture": architecture,
        },
        applications=applications,
        tools={"python3": _python_metadata(architecture)},
    )


def _read_application_metadata(app_path: Path) -> VersionArchitecture:
    plist_path = app_path / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as stream:
            metadata = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return VersionArchitecture(version="unknown", architecture="unknown")

    if not isinstance(metadata, dict):
        return VersionArchitecture(version="unknown", architecture="unknown")

    version = metadata.get("CFBundleShortVersionString") or metadata.get("CFBundleVersion")
    architecture = metadata.get("LSArchitecturePriority")
    if isinstance(architecture, list) and architecture and isinstance(architecture[0], str):
        architecture_value = architecture[0]
    elif isinstance(architecture, str):
        architecture_value = architecture
    else:
        architecture_value = "unknown"
    return VersionArchitecture(
        version=_value_or_unknown(version),
        architecture=_value_or_unknown(architecture_value),
    )


def _python_metadata(architecture: str) -> VersionArchitecture:
    # ``which`` is intentionally used only to report availability as a version
    # placeholder; its path is never returned or logged.
    version = platform.python_version() if shutil.which("python3") else "unavailable"
    return VersionArchitecture(version=_value_or_unknown(version), architecture=architecture)


def _value_or_unknown(value: object) -> str:
    return value if isinstance(value, str) and value else "unknown"
