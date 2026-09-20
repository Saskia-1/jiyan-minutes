"""Test-only real ASGI backend with an in-memory adapter and owned private root.

Started only by the explicit synthetic E2E runner. Never imports a real adapter,
reads Keychain, scans source data, or opens an outbound socket. No fixture body
is printed. UI requests still pass through the actual Task 4 API and verifier.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import sys
import time

if os.environ.get("P0_SYNTHETIC_E2E") != "1":
    raise SystemExit("synthetic_runner_required")
root_value = os.environ.get("P0_E2E_RUN_ROOT", "")
root = Path(root_value)
if not root_value or root.is_symlink() or not root.is_dir() or not root.name.startswith("chat-to-howto-synthetic-e2e-"):
    raise SystemExit("private_synthetic_root_required")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))
from chat_to_howto.config import Settings
from chat_to_howto.db import Database
from chat_to_howto.domain.sources import SourceFingerprint
from chat_to_howto.main import create_app
from chat_to_howto.providers.fake import FakeProvider
from chat_to_howto.security.secrets import InMemorySecretStore
from chat_to_howto.sources.base import CompletenessEvidence, ConversationRef, FetchCancelled, FetchPage, SourceHealth, SourceMessage
import uvicorn

def no_outbound(*args, **kwargs):
    raise RuntimeError("synthetic_server_outbound_forbidden")
socket.socket.connect = no_outbound
socket.create_connection = no_outbound

class Adapter:
    evidence_class = "synthetic"
    fingerprint = SourceFingerprint(client_version="fixture-client-1", tool_version="fixture-tool-1", account_version="fixture-account-1")

    def __init__(self, source_id, account_id, **kwargs):
        self.source_id, self.account_id = source_id, account_id
        self.cancelled = False

    def health(self):
        return SourceHealth(True, False)

    def list_accounts(self):
        return ("synthetic-account",)

    def list_conversations(self):
        return [
            ConversationRef(self.source_id, self.account_id, "group:synthetic-A", "同名合成群", "group"),
            ConversationRef(self.source_id, self.account_id, "group:synthetic-B", "同名合成群", "group"),
            ConversationRef(self.source_id, self.account_id, "group:synthetic-cancel", "取消测试会话", "group"),
        ]

    def fetch_range(self, conversation_id, start_ms, end_ms):
        if self.cancelled:
            raise FetchCancelled("synthetic_cancelled")
        if conversation_id not in {"group:synthetic-A", "group:synthetic-B", "group:synthetic-cancel"} or start_ms != 1788220800000 or end_ms != 1788220860000:
            raise ValueError("synthetic_scope_mismatch")
        if conversation_id == "group:synthetic-cancel":
            time.sleep(1.5)
            if self.cancelled:
                raise FetchCancelled("synthetic_cancelled")
        messages = (SourceMessage("m1", conversation_id, start_ms, "合成记录，仅用于本地测试", 0), SourceMessage("m2", conversation_id, end_ms - 1, "合成记录，仅用于本地测试", 1))
        return FetchPage(messages, None, CompletenessEvidence(status="source_range_verified", reason="synthetic_fixture", requested_start_ms=start_ms, requested_end_ms=end_ms, observed_start_ms=start_ms, observed_end_ms=end_ms - 1, fetched_count=2, accepted_count=2))

    def cancel(self):
        self.cancelled = True

output = root / "approved-output"
output.mkdir(mode=0o700)
workspace = root / "synthetic-workspace"
workspace.mkdir(mode=0o700)
public_sections = {
    "source_version": "合成适配器 fixture-tool-1；仅自动化测试，无真实工具许可断言。",
    "access_impact": "不访问真实客户端，不修改或重签名客户端。",
    "scope_overread": "只生成两条合成记录并写入批准的合成目录。",
    "storage_cleanup": "私有测试文件；测试结束后仅由开发者清理本次临时根。",
    "permissions_network": "不申请系统权限；禁止外连；遥测、媒体、AI、监控均关闭。",
    "action_stop": "本次只验证合成文件计数、范围、哈希；拒绝、失败和取消均停止。",
}
options = {
    "approved_output_roots": [{"output_root_id": "fixture-root", "label": "已批准的合成目录"}],
    "source_disclosures": {name: {"revision": "synthetic-contract-1", "sections": public_sections, "blockers": []} for name in ("qq", "wechat")},
}

def generation_provider_factory(provider_name: str):
    if provider_name != "fake":
        raise ValueError("generation provider is not configured")
    return FakeProvider()

app = create_app(
    settings=Settings(host="127.0.0.1", timezone="Asia/Shanghai", data_dir=root, export_root=None, qce_base_url="http://127.0.0.1:40653", tracememo_base_url="http://127.0.0.1:6131", llm_provider="fake", llm_model="deterministic"),
    database=Database(root / "synthetic.sqlite3"), adapter_factory=Adapter,
    secret_store=InMemorySecretStore(), clock=lambda: datetime.now(timezone.utc),
    output_roots={"fixture-root": output}, source_root=workspace,
    setup_options=options, origin="http://127.0.0.1:4173", run_jobs=True,
    generation_provider_factory=generation_provider_factory,
)
# The actual application bootstrap configures the synthetic source through its
# public API from Playwright; the database is not populated with mock job data.
uvicorn.run(app, host="127.0.0.1", port=18765, access_log=False, log_level="error")
