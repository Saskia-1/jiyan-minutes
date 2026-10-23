# Conversational Agent Implementation Plan

> **For agentic workers:** Use test-driven-development and verification-before-completion. Shared checkout, file boundaries; independent review before delivery.

**Goal:** 将记言改成上传资料、连续对话、后台白名单工具循环自动交付同版双TXT的智能体。
**Architecture:** 现有FastAPI/SQLite/React，模型结构化工具动作、会话CAS状态、已有mixed search/generation/document服务。
**Tech Stack:** Python/pytest、TypeScript/React/Vitest、Playwright。
**Spec:** docs/superpowers/specs/2026-10-23-conversational-agent-design.md（用户明确直接执行）。

## Global Constraints
名称及系统路径/v10/逐次外发授权/部分跳过/原文7天/文档保留均不改变。不读取真实聊天，不调用真实模型。仅项目文件可提交。

## Review Focus
会话串线与跨owner；重启后旧授权；取消和晚模型响应；原文过期后会话复活；两轮修改与两TXT精确版本。

### Task 1: 本地会话存储
Files: 新repositories/agent_conversations.py、迁移18及测试。
接口：AgentConversationRepository(chat_repository)。create(*,session_digest,now_utc,state)->row，list(*,session_digest)->tuple[row]，get(conversation_id,*,session_digest)->row，update(conversation_id,*,session_digest,expected_revision,state,now_utc)->row（CAS失败ValueError），sweep(*,now_utc)->int。row包含conversation_id,revision,created_at_utc,updated_at_utc,state(dict)。state为服务私有JSON；限制1MiB，所有row均owner校验。
- [x] RED→GREEN：CRUD/owner、CAS/并发、七天清理不续期、保留artifact文档引用、v17升级18幂等。

### Task 2: 工具执行服务与路由
Files: 新services/conversation_agent.py、api/agent_conversations.py及测试；root负责main接线。
使用Task1存储，构造ConversationAgentService(repository=ChatRepository, mixed_search, generation=GenerationJobOrchestrator, documents=DocumentService, provider_factory, provider,model,clock,max_daily_provider_requests)。提供list_conversations/create_conversation/get_conversation/replace_sources/send_message/approve/cancel/advance方法与create_agent_conversations_router(get_service,require_session,require_csrf)。方法具体入参依照公开REST，advance(run_id,session_id,conversation_id)由BackgroundTasks推进。白名单search_sources/generate_document/ask_user，JSON严格参数；operation='agent_tool'。复用reserve_agent_plan_request/finish及generation额度。payload完整freeze及一次性preview在内存；运行文档独立成果revision，不改现有DocumentService。
- [x] RED→GREEN：模型根据搜索结果继续选工具、零命中重搜/询问、控制与生成授权隔离、全程自动交付、两轮修改上下文、取消/owner/限额/过期/restart。

### Task 3: 聊天UI与客户端
Files: 新features/conversation/ConversationWorkspace.tsx及拆分组件/测试、api/conversations.ts（接入LocalApiClient）、types单独文件、styles.css和App.tsx。不要改旧ChatImportPanel/ImportedChatWorkspace；保留其测试入口。
依照spec固定REST/响应；普通首页仅聊天。多附件通过现有previewImport/commitImport、previewContentImport/commitContentImport在消息流内确认，部分跳过独立checkbox。轮询运行状态，后台执行不让用户选keywords/direction。成果卡使用documentTextPair固定版本下载，响应晚到/换会话/清理时revokeBlob。历史文档高级工具可展开。
- [x] RED→GREEN：附件确认、提交对话、授权卡、状态、成果与TXT、连续两轮、换会话/取消/晚响应、刷新恢复、窄屏。

### Task 4: 集成验证与运行
Root main接线、FakeProvider operation agent_tool契约fixture、正式首页e2e（旧页面测试转专用component harness，不在production加testmode）、文档。独立审查、后端全套、前端单测/type/build、e2e、secret scan；重启本地打开页面，按既有授权同步仅项目文件。
- [x] 完成实现验收与本地运行展示，不以合成测试声称真实质量验收；项目同步按既有官方 API 授权执行。

验证记录：`docs/conversational-agent-verification-2026-10-23.md`。后端 1,118 项、101 子测试；前端 310 项；端到端 26 项通过、2 项跳过。类型检查与生产构建通过。独立复核已关闭全部确认的 P1/P2。正式服务在 127.0.0.1:8765 运行，浏览器已确认新版聊天界面。

## Rulings
- 复用当前已运行checkout；保留所有无关未跟踪个人资料，不创建额外worktree或移动用户文件。
- 用户已批准完整开发Prompt并明确直接执行，无需再次审批设计或执行方式。
- 每轮成果是会话中的新整理版本，对应独立immutable document，避免把新证据并入旧文档冒充原引用；两TXT仍绑定确切文档版本。
