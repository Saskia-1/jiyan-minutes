"""The formal user-flow document covers every required action and field."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_how_it_works_covers_required_actions_and_explanation_fields():
    text = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")
    actions = (
        "首次初始化", "打开平台", "连接数据源", "选择聊天", "选择时间",
        "导出聊天到本地", "自动导入", "输入话题", "查看命中", "完整筛选",
        "外发确认", "生成", "结果校验", "查看依据", "编辑", "导出 Word",
        "删除", "断开数据源",
    )
    for action in actions:
        assert action in text
    fields = (
        "**用户动作 →**", "**本平台后端接口 →**", "**本地源接口（如有）→**",
        "**数据读写 →**", "**是否调用外部模型/发送什么 →**", "**返回结果 →**",
        "**常见失败/取消/重试/费用 →**",
    )
    for field in fields:
        assert text.count(field) >= len(actions)
    for term in ("适配器", "快照", "幂等", "Token", "CSRF", "Provider"):
        assert f"**{term}**" in text


def test_all_required_delivery_documents_exist():
    for name in (
        "environment.md", "source-contracts.md", "source-setup.md", "api-setup.md",
        "how-it-works.md", "acceptance-report.md", "decisions.md", "privacy-and-cleanup.md",
        "ultra-gate-1-research.md", "ultra-gate-1-binary-audit.md",
    ):
        assert (ROOT / "docs" / name).is_file(), name
