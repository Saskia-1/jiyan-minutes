#!/usr/bin/env python3
"""Create one private non-secret runtime configuration without overwriting it."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--provider", choices=("unconfigured", "deepseek"), default="deepseek")
    parser.add_argument("--model", choices=("deepseek-v4-flash", "deepseek-v4-pro"), default="deepseek-v4-flash")
    parser.add_argument("--daily-request-limit", type=int, default=100)
    args = parser.parse_args()

    project_root = args.project_root.resolve(strict=True)
    data_dir = _external_directory(args.data_dir, project_root)
    export_root = _external_directory(args.export_root, project_root)
    if data_dir == export_root:
        raise SystemExit("数据目录与导出目录必须不同。")
    if not 1 <= args.daily_request_limit <= 10_000:
        raise SystemExit("每日请求上限必须介于 1 和 10000。")
    target = data_dir / "runtime-config.json"
    if target.exists() or target.is_symlink():
        raise SystemExit("运行配置已存在；为保护已有数据，本次未覆盖。")
    payload = {
        "schema_version": 1,
        "app_timezone": "Asia/Shanghai",
        "app_data_dir": str(data_dir),
        "export_root": str(export_root),
        "llm_provider": args.provider,
        "deepseek_model": args.model,
        "llm_daily_request_limit": args.daily_request_limit,
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-config-", dir=data_dir)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        os.unlink(temporary)
        directory_fd = os.open(data_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
    print(str(target))
    return 0


def _external_directory(path: Path, project_root: Path) -> Path:
    if not path.is_absolute():
        raise SystemExit("数据与导出目录必须使用绝对路径。")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise SystemExit("目录不能是符号链接。")
    resolved = path.resolve(strict=True)
    if resolved == project_root or project_root in resolved.parents:
        raise SystemExit("数据与导出目录必须位于源码目录之外。")
    os.chmod(resolved, 0o700)
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
