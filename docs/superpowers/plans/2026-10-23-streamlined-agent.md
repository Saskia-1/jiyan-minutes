# Streamlined Agent Implementation Plan

> **For agentic workers:** Use test-driven development and independent review. The user explicitly requested these interaction changes; proceed in the current checkout.

**Goal:** 选择资料后自动导入，发送要求后自动交付纯结果，聊天消息左右分列。
**Architecture:** 保留 React/FastAPI/SQLite 与已有检索、生成、证据核验。新增逐轮 automatic opt-in；默认旧手动接口兼容。展示与 TXT 只去除系统附加证据，独立参考仍保留。
**Tech Stack:** TypeScript/React/Vitest/Playwright、Python/pytest。
**Spec:** 本轮用户明确指令与 AGENTS.md 的 2026-10-23 主对话交互更新。

## Global Constraints
名称及系统目录、原文 7 天、文档保留、v10 总结逻辑不变。自动权限限当前用户主动发送的单轮、原来源及会话，不授权任意工具或旧任务。空输入、零有效记录与未知 OCR 不能冒充成功。仅合成资料测试，只有项目文件提交。

## Review Focus
旧授权／历史刷新不能自动外发；停止及晚响应不恢复；多附件不覆盖来源；正文与独立参考精确版本一致；部分解析与错误只保留必要提示，不显示虚假完成。

- [x] 后端 automatic 新任务、绑定权限、有界执行、默认兼容与取消回归。
- [x] TXT 纯结果与独立参考回归，不修改底层生成内容或用户真实文字。
- [x] 用户手选文件后免确认、异常校对、无回执大卡、左右消息；直接成果展示。
- [x] 全套后端、前端类型／构建及浏览器回归、独立复核；本地运行展示，按既有授权仅同步项目文件。

最终结果：后端 1,137 项与 101 子测试通过，前端 326 项通过，浏览器 32 项通过、2 项跳过。详见 `docs/streamlined-agent-verification-2026-10-23.md`。用户补充“手动选文件后默认正确”已落实，未增加任何本机文件自动搜索。剩余交付同步沿用已批准的 GitHub 官方 API 快进方式。
