"""Executable dependency boundaries for provider-neutral P2 core modules."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_generation_core_imports_do_not_load_the_deepseek_adapter() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(backend_root / "src")
    script = """
import sys
import chat_to_howto.api.generation
import chat_to_howto.main
import chat_to_howto.services.generation_cache
import chat_to_howto.services.generation_extraction
import chat_to_howto.services.generation_jobs
import chat_to_howto.services.generation_merge
import chat_to_howto.services.generation_planning
import chat_to_howto.services.generation_redaction
import chat_to_howto.services.generation_scope
raise SystemExit(1 if 'chat_to_howto.providers.deepseek' in sys.modules else 0)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
