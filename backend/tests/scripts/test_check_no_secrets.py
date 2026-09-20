"""Repository scanner identifies likely credentials without printing values."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = spec_from_file_location("check_no_secrets", ROOT / "scripts" / "check_no_secrets.py")
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_scanner_rejects_private_key_api_key_and_synthetic_chat_sentinel(tmp_path):
    clean = tmp_path / "clean.md"
    key = tmp_path / "key.txt"
    pem = tmp_path / "pem.txt"
    chat = tmp_path / "chat.json"
    clean.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    key.write_text("DEEPSEEK_API_KEY=" + "sk-" + "a" * 30, encoding="utf-8")
    pem.write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    sentinel = b"synthetic-private-chat-sentinel"
    chat.write_bytes(sentinel)

    findings = MODULE.scan_files((clean, key, pem, chat), sentinels=(sentinel,))

    assert findings == [key, pem, chat]


def test_scanner_accepts_repository_text_without_known_secrets():
    findings = MODULE.scan_files(MODULE.repository_text_paths(ROOT))
    assert findings == []
