# 聊天记录整理平台：项目现状与待决策项

更新日期：2026-09-20（北京时间）  
用途：供 ChatGPT 或项目协作者分析、确定下一轮修改范围。本文只报告当前工作区可见实现、已有验收证据与本轮检查结果；不把合成测试等同于 QQ/微信自动导出或真实大模型效果。

## 一句话结论

项目已具备可运行的本地 Web 应用。**用户手动提供 TXT 等聊天文件 → 预览确认 → 本地搜索/时间筛选 → 排除消息 → Fake AI 生成 → 文档编辑/版本化 → Markdown/DOCX 导出 → 刷新恢复**，已用一份真实但非敏感的 TXT 完成端到端验收。QQ/微信自动获取聊天与真实 DeepSeek 调用尚未验收；QQ Accessibility 路线已按既定结论关闭。当前最紧迫的工程风险是大量核心代码仍未提交到 Git，现有工作区的成功不能直接代表干净检出后的可复现性。

## 证据口径与当前基线

- 本轮读取了当前代码、Git 状态、README、项目文档与测试配置；没有修改产品代码，没有重新运行 Accessibility 探测，也没有访问 QQ/微信窗口、数据库或聊天内容。
- 当前分支：`codex/chat-to-howto-v3`；最近提交为 `e493878 docs: lock P1 export and search contracts`。工作区有 **19 个已跟踪文件修改**，另有大量未跟踪文件/目录，包括 `frontend/`、`scripts/`、后端 API、服务和测试，以及文档。`git diff --stat` 仅覆盖已跟踪文件，为 1702 行新增、125 行删除；**不包含**这些未跟踪代码。没有执行 reset、clean 或覆盖。
- 2026-09-20 本轮验证：后端核心测试（排除已关闭的 `backend/tests/tools/` Accessibility POC 套件）**759 passed、1 skipped、101 subtests passed**；前端 Vitest **187/187 passed**；TypeScript 类型检查和 Vite 生产构建通过。`frontend/package.json` 没有独立 lint 脚本。
- 默认后端测试命令若包含工具 POC 测试，本轮结果为 **767 passed、1 skipped、3 failed**；失败项是三个以 macOS LaunchServices 启动 Accessibility `.app` 的工具测试，在当前执行环境返回非零退出状态。它们不属于上述核心业务测试，也不能被写成“全量测试通过”。应保持单独的手工/环境依赖验证，不删除测试。
- 2026-09-18 之前一轮真实内容验收已记录：用户提供的原始 TXT 经浏览器链路导入 **88/88 条、0 未解析、0 重复、11 位发送者**；“就业”命中 10 条、“保研”命中 6 条；北京时间范围筛出 86 条，人工排除 1 条后向 **Fake AI** 提交 85 条；文档第 2/3 版、Markdown/DOCX 导出、刷新后导入/搜索/文档恢复通过，浏览器阻断外部请求且记录为 0。本轮**未重新读取或重跑**该真实 TXT 测试；对应可执行测试为 [`frontend/e2e/p0-real-content.spec.ts`](../frontend/e2e/p0-real-content.spec.ts)。这证明该样本链路可用，不证明所有聊天格式、真实 AI 的语义质量或自动来源能力。

## 已实现的功能

| 领域 | 代码中可见的能力 | 验证边界 |
| --- | --- | --- |
| 本地运行与安全边界 | 单用户、回环地址同源 Web 服务；会话 Cookie、CSRF、配置化数据/导出目录；macOS 启停脚本和锁定依赖。 | 代码/自动化测试；不是多用户或云部署。 |
| 手动导入 | 粘贴文本及 TXT、JSON、JSONL、CSV、XLSX、受支持的单文件 ZIP；预览后确认，解析问题/重复数展示，独立批次与快照。TXT 支持含年月日或仅月日时间的受支持方言，后者要求明确默认年份。 | 合成测试覆盖多格式；一份真实 TXT 已通过端到端。不是任意格式自动识别。 |
| 聊天检索 | 导入后选择会话，本地关键词搜索、北京时间时间范围筛选、分页、生成前排除指定消息。 | 单样本真实内容和合成测试通过。 |
| AI 生成管线 | Provider 抽象、FakeProvider、DeepSeek 适配器；外发预览/单次授权、分块、提取与合并、证据校验、失败/取消/缓存与用量约束。 | Fake/Mock 路径通过；真实 DeepSeek 认证、成本和效果未测。 |
| 文档 | 创建指南、追加式版本、历史恢复、证据查看、Markdown/DOCX 两种导出模式、按会话恢复列表与删除。 | 自动测试与真实 TXT 样本链路通过；编辑能力目前只覆盖标题和已有步骤的文字。 |
| 数据治理 | SQLite 持久化、导入批次恢复/删除、文档恢复、影响指纹和受管文件删除流程；会话恢复窗口是首次签发后最多 7 天。 | 自动测试覆盖；不是长期账户或跨设备恢复。 |
| QQ/微信来源框架 | 来源契约、只读适配器、范围/证据/原子导入代码存在，前后端显示阻断状态。 | 只经过合成测试和第三方发布物静态审计；真实 QQ/微信自动导出均未成功验收。 |

关键实现入口：[`backend/src/chat_to_howto/main.py`](../backend/src/chat_to_howto/main.py)、[`backend/src/chat_to_howto/services/manual_import.py`](../backend/src/chat_to_howto/services/manual_import.py)、[`frontend/src/features/imports/ChatImportPanel.tsx`](../frontend/src/features/imports/ChatImportPanel.tsx)、[`frontend/src/features/search/ChatSearchPanel.tsx`](../frontend/src/features/search/ChatSearchPanel.tsx)、[`frontend/src/features/generation/GenerationPanel.tsx`](../frontend/src/features/generation/GenerationPanel.tsx)、[`frontend/src/features/documents/DocumentEditor.tsx`](../frontend/src/features/documents/DocumentEditor.tsx)。

## 当前问题、影响与可行处理方向

| 优先级 | 问题与依据 | 影响 | 建议处理方向 |
| --- | --- | --- | --- |
| P0 | **Git 基线不可复现**：核心前端、后端新模块、脚本和大量测试均未跟踪；另有 19 个已跟踪文件修改。 | 换机/干净检出可能无法启动，也难以审查、回退和定位回归。 | 先逐项盘点未跟踪文件，区分源码、生成物和个人数据；保留人工修改，分逻辑提交源码/测试/文档；从干净检出重跑核心测试和构建。不要直接 `git add .` 或清理未知文件。 |
| P1 | **长期可找回性不足**：已导入数据持久化，但批次/文档恢复依赖本地浏览器会话；7 天绝对窗口过期或 Cookie 丢失后，旧数据仍在本地却不再通过该会话列出。见 [`main.py`](../backend/src/chat_to_howto/main.py) 的 `session_recovery_ttl_seconds` 及 [`privacy-and-cleanup.md`](privacy-and-cleanup.md)。 | 用户可能误以为刷新恢复等于永久恢复，或留下难以管理的孤立本地数据。 | 先做安全/产品决策：本机账户、恢复码或经用户确认的本地数据集重新绑定机制；规定身份验证、删除权限与失效策略，再实现和测试。不要仅无限延长 Cookie。 |
| P1 | **指南编辑范围有限**：当前 [`DocumentEditor.tsx`](../frontend/src/features/documents/DocumentEditor.tsx) 只编辑标题与已有步骤 instruction，不能在界面中新增/删除/重排步骤，也不能完整编辑准备事项、检查项等结构。 | 用户难以把生成稿按自己的要求整理成完整指南；Fake AI 输出尤其需要人工完善。 | 先定义可编辑文档结构及证据状态变化规则，再增量扩展编辑器和版本 API；验收应覆盖新增/删除/重排、保存、恢复及两种导出一致性。 |
| P1 | **真实来源缺失**：QQ/微信自动导出未验收；QCE/TraceMemo 候选因签名、权限能力、启动副作用、版本兼容等问题保持 `blocked / NO-GO`。QQ Accessibility 路线已关闭。 | 项目目前依赖用户合法取得的导出文本/文件，不能承诺“一键读取 QQ/微信”。 | 近期继续以用户主动提供的文件为准；若以后重启来源研究，须以独立、安全、合法、可审计方案重新立项，不能把静态框架算作可用功能。 |
| P2 | **文档与测试口径漂移**：[`acceptance-report.md`](acceptance-report.md) 最后更新于 2026-09-13，仍写 TXT 两种方言、P4 仅合成数据、前端 183 测试；这些已落后于 9 月 18 日验收和当前 187 测试。 | 协作者可能误判完成度；默认后端测试还混入已关闭 POC 的环境依赖项。 | 更新正式验收矩阵，按“代码/合成/真实手动导入/真实来源/真实 Provider”分层；把 AX POC 测试明确标记为手工、独立套件。 |
| P2 | **文档列表同页状态可能滞后**：[`DocumentLibrary.tsx`](../frontend/src/features/documents/DocumentLibrary.tsx) 保存初次恢复的列表，编辑器保存/恢复版本后没有向列表同步新标题、版本号和更新时间；刷新后能重新获取。 | 用户同页看到的卡片可能仍是旧元数据。 | 加 `onSaved/onRestored` 回调或保存后重新拉取当前文档；补前端测试。此项为代码推断，尚未单独做浏览器复现。 |
| P2 | **真实使用覆盖面有限**：只完成一份 88 条真实 TXT 样本验收；附件、撤回/系统消息、跨年时间、更多导出变体、大规模数据、异常编码等未据此宣称通过。 | 格式兼容与性能风险尚不清楚。 | 用用户提供的非敏感样本建立脱敏测试矩阵，先补边界测试与明确错误提示；不自行构造“真实验收数据”。 |

其他明确边界：本地 SQLite 默认不加密；备份/同步软件副本不由普通删除保证擦除。真实 DeepSeek 调用、费用和生成质量尚未验证；不能将 FakeProvider 的通过表述为真实 AI 可用。`README.md` 已披露这些限制，但正式报告需要同步。

## 功能缺口与近期任务排序

1. **P0：固定可复现代码基线。** 涉及当前未跟踪的 `frontend/`、`backend/src/chat_to_howto/{api,services,repositories,providers}/`、`scripts/`、相关测试与文档。完成标准：经审查的源码/锁文件/必要文档进入 Git，个人数据和生成物不进入 Git；干净检出可安装、运行，核心测试/类型检查/构建结果可复现。无其他任务前置依赖。
2. **P1：决定并实现持久数据恢复身份。** 主要涉及会话 API、导入/文档恢复服务、前端恢复入口与安全测试。完成标准：Cookie 丢失或 7 天到期后的用户可控恢复/删除行为明确、可测试、不会让其他本机用户无授权访问。依赖第 1 项稳定基线及产品安全决策。
3. **P1：补齐指南的结构化人工编辑。** 涉及文档模型/API、`DocumentEditor`、版本与导出测试。完成标准：可按用户要求增删改排指南关键内容，版本历史与证据标记准确，两种导出与所见内容一致。依赖第 1 项；文档结构需先确认。
4. **P2：同步验收文档、隔离 AX 工具测试、扩展真实文件兼容矩阵。** 涉及 `docs/acceptance-report.md`、`README.md`、pytest 配置/测试、导入解析测试。完成标准：报告数字与当前代码一致，默认核心测试不因已关闭工具的 GUI 环境失败而误报，新增用户样本的边界能清晰通过或给出可操作错误。依赖第 1 项。
5. **暂缓、不纳入下一轮主线**：QQ `.bak`、微信自动导出、真实 DeepSeek、重新开展 Accessibility Gate。若未来要做，须单独授权与安全/验收方案；本报告不建议为它们扩大当前改动范围。

## 建议交给 ChatGPT 的分析问题

请基于本报告和当前仓库回答：① 如何在不丢失未提交人工修改的前提下建立最小可复现 Git 基线？② 对单用户本地应用，7 天会话失效后的数据恢复应采用何种安全模型？③ 为满足“按用户要求制作指南”，文档编辑器最小需要开放哪些字段与结构操作，如何保持证据状态真实？④ 哪些真实 TXT 边界测试最值得先补？⑤ 怎样把自动化结果、真实手动导入验收、真实来源与真实 Provider 验证清晰分层，避免过度宣称？

## 相关资料

- 项目说明：[`README.md`](../README.md)
- 旧版验收报告（需更新）：[`docs/acceptance-report.md`](acceptance-report.md)
- 手动导入与来源现状：[`docs/p4-real-chat-ingestion.md`](p4-real-chat-ingestion.md)
- 数据恢复/删除边界：[`docs/privacy-and-cleanup.md`](privacy-and-cleanup.md)
- 第三方来源阻断依据：[`docs/ultra-gate-1-binary-audit.md`](ultra-gate-1-binary-audit.md)
- 真实 TXT 浏览器验收测试：[`frontend/e2e/p0-real-content.spec.ts`](../frontend/e2e/p0-real-content.spec.ts)
