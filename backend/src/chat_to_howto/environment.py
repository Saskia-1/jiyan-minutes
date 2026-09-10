"""Safe environment metadata collection with no chat or account discovery."""

from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Protocol


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
    installed: bool

    def public_dict(self) -> dict[str, str | bool]:
        return {
            "version": self.version,
            "architecture": self.architecture,
            "installed": self.installed,
        }


@dataclass(frozen=True, slots=True)
class PublicIssue:
    """A fixed, non-sensitive instruction for an undetectable audit field."""

    code: str
    message: str

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class SystemProbe(Protocol):
    """The deliberately small system-metadata surface used by this audit."""

    def system(self) -> str: ...

    def release(self) -> str: ...

    def version(self) -> str: ...

    def process_architecture(self) -> str: ...

    def rosetta_translated(self) -> bool | None: ...


class LocalSystemProbe:
    """Read only OS architecture metadata without inspecting application data."""

    _ROSETTA_COMMAND = ("/usr/sbin/sysctl", "-in", "sysctl.proc_translated")

    def __init__(
        self,
        *,
        system_reader: Callable[[], str] = platform.system,
        release_reader: Callable[[], str] = platform.release,
        version_reader: Callable[[], str] = platform.version,
        machine_reader: Callable[[], str] = platform.machine,
        command_runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self._system_reader = system_reader
        self._release_reader = release_reader
        self._version_reader = version_reader
        self._machine_reader = machine_reader
        self._command_runner = command_runner

    def system(self) -> str:
        return self._system_reader()

    def release(self) -> str:
        return self._release_reader()

    def version(self) -> str:
        return self._version_reader()

    def process_architecture(self) -> str:
        return self._machine_reader()

    def rosetta_translated(self) -> bool | None:
        """Return the fixed macOS Rosetta sysctl result, or unknown on any failure."""
        if self.system() != "Darwin":
            return False
        try:
            result = self._command_runner(
                self._ROSETTA_COMMAND,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
            )
        except Exception:
            return None
        if getattr(result, "returncode", None) != 0:
            return None
        value = getattr(result, "stdout", None)
        if not isinstance(value, str):
            return None
        if value.strip() == "0":
            return False
        if value.strip() == "1":
            return True
        return None


_ENVIRONMENT_ISSUE = PublicIssue(
    code="environment_detection_incomplete",
    message="Could not collect all safe environment metadata; run the local audit on the target Mac.",
)


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    os: dict[str, str | bool | None]
    applications: dict[str, VersionArchitecture]
    tools: dict[str, VersionArchitecture]
    issues: tuple[PublicIssue, ...] = ()

    def public_dict(self) -> dict[str, object]:
        """Return the intentionally small public metadata surface."""
        return {
            "os": dict(self.os),
            "applications": {
                name: metadata.public_dict() for name, metadata in self.applications.items()
            },
            "tools": {name: metadata.public_dict() for name, metadata in self.tools.items()},
            "issues": [issue.public_dict() for issue in self.issues],
        }


def collect_environment(
    app_paths: Mapping[str, Path], *, system_probe: SystemProbe | None = None
) -> EnvironmentReport:
    """Collect only OS, supplied app bundle, and fixed tool version metadata.

    ``app_paths`` enables synthetic fixtures in tests; callers should pass
    ``KNOWN_APPLICATION_PATHS`` in production. This function never scans for
    applications, accounts, message databases, or other user data.
    """
    probe = system_probe or LocalSystemProbe()
    system_name = _probe_value(probe.system)
    release = _probe_value(probe.release)
    version = _probe_value(probe.version)
    process_architecture = _normalize_architecture(_probe_value(probe.process_architecture))
    rosetta_translated = _read_rosetta(probe, system_name, process_architecture)
    physical_architecture, architecture_incomplete = _physical_architecture(
        system_name, process_architecture, rosetta_translated
    )
    applications = {
        name: _read_application_metadata(path) for name, path in sorted(app_paths.items())
    }
    tools = {"python3": _python_metadata(process_architecture)}
    os_metadata: dict[str, str | bool | None] = {
        "system": system_name,
        "release": release,
        "version": version,
        # Retain the original public field as the current process architecture.
        "architecture": process_architecture,
        "physical_architecture": physical_architecture,
        "process_architecture": process_architecture,
        "rosetta_translated": rosetta_translated,
    }
    return EnvironmentReport(
        os=os_metadata,
        applications=applications,
        tools=tools,
        issues=(
            (_ENVIRONMENT_ISSUE,)
            if architecture_incomplete or _environment_incomplete(os_metadata, applications, tools)
            else ()
        ),
    )


def _probe_value(reader: Callable[[], object]) -> str:
    """Fail closed without exposing a probe exception or host detail."""
    try:
        return _value_or_unknown(reader())
    except Exception:
        return "unknown"


def _read_rosetta(
    probe: SystemProbe, system_name: str, process_architecture: str
) -> bool | None:
    if system_name == "unknown":
        return None
    if system_name != "Darwin":
        return False
    # Rosetta only translates x86_64 processes. A native arm64 process is
    # already conclusive even when this sysctl is unavailable or permissioned.
    if process_architecture == "arm64":
        return False
    try:
        value = probe.rosetta_translated()
    except Exception:
        return None
    return value if isinstance(value, bool) else None


def _physical_architecture(
    system_name: str, process_architecture: str, rosetta_translated: bool | None
) -> tuple[str, bool]:
    """Infer hardware only when the process and Rosetta evidence agree."""
    if system_name == "unknown":
        return "unknown", True
    if system_name != "Darwin":
        return (process_architecture, process_architecture == "unknown")
    if rosetta_translated is True and process_architecture == "x86_64":
        return "arm64", False
    if rosetta_translated is False and process_architecture != "unknown":
        return process_architecture, False
    return "unknown", True


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"x86_64", "amd64"}:
        return "x86_64"
    return "unknown"


def _environment_incomplete(
    os_metadata: Mapping[str, str | bool | None],
    applications: Mapping[str, VersionArchitecture],
    tools: Mapping[str, VersionArchitecture],
) -> bool:
    """Collapse all missing safe audit fields into one fixed public issue."""
    if any(value is None or value == "unknown" for value in os_metadata.values()):
        return True
    return any(
        metadata.installed
        and (metadata.version == "unknown" or metadata.architecture == "unknown")
        for metadata in (*applications.values(), *tools.values())
    )


def _read_application_metadata(app_path: Path) -> VersionArchitecture:
    try:
        installed = app_path.exists()
    except OSError:
        return VersionArchitecture(version="unknown", architecture="unknown", installed=True)
    if not installed:
        return VersionArchitecture(
            version="unavailable", architecture="unavailable", installed=False
        )
    plist_path = app_path / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as stream:
            metadata = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return VersionArchitecture(version="unknown", architecture="unknown", installed=True)

    if not isinstance(metadata, dict):
        return VersionArchitecture(version="unknown", architecture="unknown", installed=True)

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
        installed=True,
    )


def _python_metadata(architecture: str) -> VersionArchitecture:
    # ``which`` is intentionally used only to report availability as a version
    # placeholder; its path is never returned or logged.
    installed = bool(shutil.which("python3"))
    version = platform.python_version() if installed else "unavailable"
    return VersionArchitecture(
        version=_value_or_unknown(version),
        architecture=architecture if installed else "unavailable",
        installed=installed,
    )


def _value_or_unknown(value: object) -> str:
    return value if isinstance(value, str) and value else "unknown"
