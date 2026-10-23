# 记言 · Minutes — 项目约定（给编码代理）

## 项目身份

- 对外名称：**记言 · Minutes**；原名 **Chat to How-To**（2026-10-14 改名，见 `docs/product-prd.md` 的命名说明与修订记录）。
- 产品定位：中文、本地优先、单用户的 macOS Web 应用。从微信／QQ 导入或导出的聊天与截图中筛出主题相关信息，归纳为可供个人复盘的 Markdown／DOCX 文档，并保存到本地。
- 需求来源：`docs/product-prd.md`（现行 v0.2）。历史开发需求见 `chat_to_howto_codex_prompt_v3.md`（V3 阶段；其顶部有命名变更说明）。
- 聊天导入允许部分成功：用户单独确认接受跳过未解析记录后，只导入有效记录；显示实际导入与跳过数量，没有有效记录时禁止提交。

## 改名边界（重要）

- 文档层的对外名称已改为“记言 · Minutes”：`README.md`、`docs/*.md`、前端标签页标题（`frontend/index.html`）。
- **不要重命名**：Python 包 `chat_to_howto`、配置项 `CHAT_TO_HOWTO_CONFIG`、Keychain 服务名 `com.openai.chat-to-howto.source-token`、数据目录 `~/Library/Application Support/Chat to How-To`、导出目录 `~/Documents/Chat to How-To Exports`，以及 e2e 临时目录前缀 `chat-to-howto-synthetic-e2e-`。改动这些会让运行配置、已保存的来源 Token 与既有本地数据对不上。
- 历史归档（`docs/superpowers/plans/*`、`generated/*`、`chat_to_howto_codex_prompt_v3.md` 正文）保留旧名，不要批量替换。

## 归纳方式（用户于 2026-10-14 明确要求）

- 归纳提示词、生成输入结构、分段与综合汇总逻辑使用今天修改前的版本（`topic-editorial-v10-actor-evaluation`）。不因 PRD 对齐或调测自行改变总结方式。
- 今天新增的整理目的输入及 v11/v12 写作规则已撤回；PRD 中的目的说明要求暂缓，重新实现须取得用户明确指示。导入、搜索故障、文档保存与数据清理修复不属于总结方式调整。

## 不可越过的边界

- 不自动登录账号、不读取聊天目录、不获取微信数据库密钥、不提权、不关闭 SIP、不授予完全磁盘访问、不修改或重签名第三方客户端。
- 只有 AI 生成步骤可能联网；外发前必须展示完整脱敏正文并取得用户逐次授权，未授权不得发送。脱敏不能保证覆盖全部个人信息，不得宣称完全匿名。
- 日志、错误响应与指标记录不得包含聊天正文、密钥或绝对路径。

## 数据保留规则

- 原文（导入的聊天正文、截图与 OCR 正文、粘贴笔记）：自确认导入起保留 7 天，用户可提前删除；到期停用并触发清理。清理须覆盖快照、全文索引、预览缓存、过程记录、内部依据副本，以及平台已写出的原子导出批次与暂存文件。
- 文档与保留版本：不设期限，允许内嵌依据附录；原文删除后文档仍可阅读、编辑和下载，只是依据不可再查看。
- 首版的应用内往期文档查看受浏览器会话窗口限制，以“另存为”引导用户持有文档；解除会话绑定列为 P1。

## 开发与验证

- 后端：`make test PYTHON=backend/.venv/bin/python`。
- 前端：`cd frontend && corepack pnpm test && corepack pnpm run typecheck && corepack pnpm run build`。
- 已完成“工作”主题单例真实模型调测（见 `docs/real-model-work-smoke-test.md`）；跨领域正式质量验收仍未完成，不得用 FakeProvider 或模拟输出替代；未验证真实 QQ／微信导出前不要声称已验证。
