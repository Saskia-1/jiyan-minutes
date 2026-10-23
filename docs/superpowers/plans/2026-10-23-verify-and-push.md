# 记言 · Minutes 验证与推送计划

> **For agentic workers:** Use systematic debugging and verification-before-completion. Independent frontend and backend failure groups are delegated under dispatching-parallel-agents.

**Goal:** 修复现有失败检查，仅提交项目文件并推送到已配置 GitHub 仓库。

**Architecture:** 沿用现有本地 FastAPI、SQLite 与 React 实现；补齐已存在但未接通的原文保留与前端接口约定。网络排查仅影响本次 Git 命令，不修改系统网络配置。

**Tech Stack:** Python、FastAPI、SQLite、React、TypeScript、pytest、Vitest、Playwright。

**Spec:** `AGENTS.md` 与 `docs/product-prd.md`，总结方式调整仍暂缓。

## Global Constraints

- 不改变 v10 归纳提示词、生成输入和分段综合方式，不增加目的输入。
- 不读取真实聊天，不向模型外发内容，不修改系统或第三方客户端权限。
- 原文自确认导入保留 7 天；文档和版本长期保留。
- 排除密钥、运行数据、缓存、构建输出、个人学习与面试材料。

## Review Focus

- 删除与生成并发时不能继续使用已撤销的正文或重新写回缓存。
- 到期计算不能因重试或更换浏览器会话而延期。
- 删除原文后文档仍可阅读、编辑与导出。
- 部分导入需单独确认跳过，零有效记录不能提交。
- 前端数据管理源码不能被运行数据的忽略规则排除。

## Tasks

- [x] 复现完整检查失败，区分应用缺口与 macOS 沙箱限制。
- [x] 后端：用已有保留规则失败测试验证 7 天停用、清理副本、保留版本与竞态处理。
- [x] 前端：接通保留状态与删除计划接口，验证合规、部分导入及检索降级的现有测试。
- [x] 修正 `.gitignore` 的数据目录匹配范围。
- [x] 运行后端全套、前端单测、类型检查、构建与合成端到端测试。
- [x] 审核待提交文件及历史凭据，提交修复并推送当前分支，核对远端提交哈希。用户确认官方 API 同步后，34 个提交逐一核对成功，远端为 `fb7f45f91ad73021864969c20285fbd4a8a7bc20`；保留全部分支，无删除或覆盖。
