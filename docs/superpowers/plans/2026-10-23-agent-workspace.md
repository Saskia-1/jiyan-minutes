# 记言 · Minutes Agent 工作台实现计划

> **For agentic workers:** Use test-driven-development and verification-before-completion. Independent planning, text rendering, and frontend integration may be delegated with fixed interfaces; review the combined change before pushing.

**Goal:** 上传当前支持的文件后，用自然语言驱动整理，输出同一版本的整理内容与核查参考两个 TXT。

**Architecture:** 使用现有 FastAPI／SQLite／React 与已配置的 DeepSeek。本地指令规划只转换为现有关键词、主题与方向；生成沿用 v10。两个 TXT 使用一次结构化来源编号映射，下载时生成，不另存托管依据副本。

**Tech Stack:** Python、FastAPI、React、TypeScript、pytest、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-23-agent-workspace-design.md`，用户已确认按方案开发。

## Global Constraints

- 当前错误修复与首次 push 完成后才开始产品代码。
- 保持「记言 · Minutes」名称及既有配置、Keychain、数据和导出目录名称。
- 保持 `topic-editorial-v10-actor-evaluation` 及现有生成输入结构、分段综合逻辑。
- 完整自然语言要求放入已有生成 `topic`，一并脱敏；未逐次确认完整预览不外发。
- 原文 7 天、文档版本长期保留；不读取真实聊天，不执行文档内嵌指令，不新增模型调用。
- 上传仅项目文件，不包含私密配置、运行数据、生成输出、个人学习与面试材料。

## Review Focus

- 否定词／模糊要求不能被错误宣称为已理解；允许修改关键词和方向。
- 修改要求或来源后不能复用旧预览、授权或异步检索结果。
- 两份 TXT 使用同一不可变版本，不能将用户改写内容标为已被原引用核验。
- 依据清理后不能通过 TXT 恢复摘录或编造引用。
- 只有用户当前选择／新导入的来源进入任务，不能自动纳入历史全部资料。

### Task 1: 本地任务理解

**Files:** 创建 `backend/src/chat_to_howto/services/agent_planning.py`、`backend/src/chat_to_howto/api/agent.py`；修改 `backend/src/chat_to_howto/main.py`；测试 `backend/tests/services/test_agent_planning.py`、`backend/tests/api/test_agent.py`。

**Interfaces:** `POST /api/agent/plans {instruction: string}`（1–512 字符，绑定会话与 CSRF），返回 `{instruction, goal, keywords: string[], summary_type, all_content: boolean, needs_clarification: boolean, notices: string[]}`。`summary_type` 沿用 viewpoints／ideas／how_to／mixed。服务不访问文件、数据库正文或模型。

- [x] 写并运行 RED：中文主题与四方向、引号／多关键词、否定指令、空白超长、无主题、明确全部内容；断言完整指令不丢失、规划零外发。
- [x] 实现有边界的规则提词与方向识别；模糊时返回可编辑默认和说明，不自动猜测事实。
- [x] 服务与 API 定向通过，记录接口供前端使用。

### Task 2: 同版本双 TXT

**Files:** 创建 `backend/src/chat_to_howto/services/document_text_pair.py`；修改 `backend/src/chat_to_howto/api/documents.py`；测试 `backend/tests/services/test_document_text_pair.py`、相关 API 测试。

**Interfaces:** `POST /api/documents/{document_id}/text-pair {version_id}`，会话／CSRF保护。返回 `{document_id, version_id, body: {filename, text}, reference: {filename, text}}`，文件名为安全标题加「整理内容.txt」「核查参考.txt」。只从会话所有的保留文档与指定版本渲染；不写托管文件或过程副本。

- [x] 写并运行 RED：四方向正文、相同编号、混合来源定位、UTF-8、固定版本、用户编辑警示、原文清理后的参考说明、跨会话拒绝。
- [x] 按结构化引用一次建立编号映射；正文纯文本显示核查编号，参考只列使用的依据并说明 AI 补充／编辑内容的限制。
- [x] 渲染与 API 定向通过；不修改 Markdown／DOCX 既有渲染或总结方法。

### Task 3: Agent 工作台

**Files:** 修改 `frontend/src/App.tsx`、`frontend/src/api/client.ts`、`frontend/src/types.ts`、`frontend/src/features/imports/ImportedChatWorkspace.tsx` 与文档结果组件；创建必要的任务卡组件及对应测试。

**Interfaces:** 使用 Task 1 规划响应，将关键词用于本地检索、完整指令用于现有生成 topic；每次下载使用 Task 2 取得当前固定版本的配对文本，创建两个 UTF-8 下载按钮。不引入新模型供应商、后台外发或独立 Agent 框架。

- [x] 写并运行 RED：自然语言生成可编辑任务卡、零命中提示、更新使旧预览失效、取消零外发、成功提供两个同版本 TXT、已清原文时的依据说明。
- [x] 页面保留名称，主要流程改为文件、自然语言要求、理解结果、外发授权、任务进度与双文件；高级筛选折叠保留。
- [x] 当前导入／截图与笔记／部分成功授权保留；仅当前选择来源纳入任务。
- [x] 前端单测、类型检查、构建通过；正式首页合成端到端覆盖完整 Agent 流程及修改／取消路径。

### Task 4: 集成验收与推送

- [x] 后端全套、前端单测／类型检查／构建、合成端到端全部通过。
- [x] 独立审查授权、时效、编号、版本及异步竞态，重要问题以 RED→GREEN 修复。
- [x] 更新 README 与测试记录，重启本地服务并打开工作台；不以合成测试声称真实模型质量验收。
- [ ] 只提交项目文件，安全扫描后推送当前分支并核对远端哈希。

## Execution ledger

- 当前修复已独立提交为 `fb7f45f`：后端 938 passed／2 skipped／101 subtests，前端 226 单测、类型检查与构建通过，合成 e2e 18 passed／2 skipped。
- 标准 Git 推送入口持续超时；用户明确确认只增不删的官方 API 同步方案后，34 个提交逐一核对并上传成功，远端与本地 `fb7f45f` 完全一致。原含临时远端分支删除的方案被自动审批拒绝，已移除全部删除操作；实际同步保留全部分支，无覆盖历史。
- 用户已确认 Agent 设计「按方案开发」；任务 1、2、3 在同一工作区内按文件边界并行实现，分别由 agent_planning、agent_text_pair、agent_frontend 负责。当前修复与新功能分开提交。

- Agent 集成验证：后端 1033 passed／2 skipped／101 subtests，前端 237 单测、类型检查与构建通过；桌面与 360px 合成 e2e 22 passed／2 skipped。独立复核无剩余确证 P1/P2。
- 实际启动发现较早本地 v15 结构缺少清理记录表与保留文档标记列。用户明确授权仅检查与修复记言自身数据库结构后，追加迁移 16；合成回归 RED→GREEN、定向 30 passed，实机重启成功，首页与会话 HTTP 200。未读取聊天正文或微信／QQ数据库。
