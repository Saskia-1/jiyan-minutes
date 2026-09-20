#!/usr/bin/env python3
"""Fail closed when repository text contains a likely committed credential."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
from typing import Iterable


_TEXT_SUFFIXES = {
    ".css", ".html", ".js", ".json", ".lock", ".md", ".mjs", ".py",
    ".sh", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        rb"(?m)^(?:DEEPSEEK_API_KEY|OPENAI_API_KEY|QCE_API_TOKEN|TRACEMEMO_API_TOKEN)"
        rb"[ \t]*=[ \t]*[^\s#][^\r\n]*$"
    ),
)
_SYNTHETIC_FIXTURE_PATHS = frozenset({"backend/tests/scripts/test_check_no_secrets.py"})


def scan_files(paths: Iterable[Path], *, sentinels: Iterable[bytes] = ()) -> list[Path]:
    sentinel_values = tuple(value for value in sentinels if value)
    findings: list[Path] = []
    for path in paths:
        if path.suffix.lower() not in _TEXT_SUFFIXES or path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append(path)
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in _SECRET_PATTERNS) or any(
            sentinel in data for sentinel in sentinel_values
        ):
            findings.append(path)
    return findings


def repository_text_paths(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    names = result.stdout.split(b"\0")
    decoded = (name.decode("utf-8") for name in names if name)
    return tuple(root / name for name in decoded if name not in _SYNTHETIC_FIXTURE_PATHS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    findings = scan_files(repository_text_paths(root))
    if findings:
        for path in findings:
            print(f"possible secret: {path.relative_to(root)}")
        return 1
    print("repository secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
