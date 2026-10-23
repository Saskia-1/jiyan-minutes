# 记言 · Minutes：AI 任务理解补全计划

依据已批准的 Agent 工作台设计，以及用户再次明确要求实现自然语言 Agent，补全 AI 任务理解。沿用现有本地平台、DeepSeek、现有文件格式与同版本双 TXT，不修改 v10 总结提示词、生成 schema 或分段综合。

## 实现取舍

在现有工作台内增加受逐次授权保护的 AI 规划调用，复用现有 Provider 接口。保留本地规则作为明确标识的离线选项；不将模型失败自动伪装为成功，不自动扩大资料范围。不引入新供应商、SDK 或云端数据迁移。

## 冻结接口

- 保留 `POST /api/agent/plans {instruction}` 的本地规则接口。
- 新增 `POST /api/agent/plan-previews {instruction}`，仅本地脱敏和冻结。返回 `preview_id, preview_fingerprint, instruction, outbound_text, provider, model, expires_at_utc, max_output_tokens`。不读取或发送文件正文。
- 新增 `POST /api/agent/plans/execute {preview_id, preview_fingerprint, confirmed:true}`。会话、CSRF、过期、一次性授权保护；执行冻结请求，返回已有 `AgentPlan` 结构。请求 payload 为 `{operation:"plan_agent",instruction:脱敏要求}`。
- 新增 `POST /api/agent/plan-previews/{preview_id}/cancel {}`，清理待授权请求／协作取消运行中请求，不触发其他工具。
- AI 只返回结构化目标、最多10个短关键词、四种整理方向、全文/澄清标记与说明。完整用户要求仍由前端保留并传给既有生成 topic，模型不得丢弃原约束。
- 资料排除条件不能被宣称已执行；模型不得把含糊输入扩大成全文。新增规划请求只记录不含正文的用量元数据，并与既有生成请求共同受每日上限约束。

## 任务与验证

- [x] 后端：先红测试，再实现规划预览、授权、解析、取消与安全失败；用量限额持久化，迁移向后兼容。仅使用合成 Provider。
- [x] 前端：默认 AI 任务理解，完整预览＋本次授权；修改、取消、切换和清理使旧任务失效，显式本地选项。AI结果自动推进本地检索及既有生成预览，不能自动批准资料外发。
- [x] 完整流程：隔离 FakeProvider 处理 plan_agent，合成端到端验证规划授权与正文授权独立、取消零后续外发、双TXT同版本。
- [x] 独立复核，后端全套、前端单测/类型/构建、e2e；更新说明、重启页面、仅项目文件同步。

不执行真实模型调用；开发请求不代替产品内逐次外发授权。不得以模拟测试声称真实模型质量验收完成。

## 执行结果

后端1081 passed／2 skipped／101 subtests，前端281 passed、typecheck和build通过，合成e2e22 passed／2 skipped。独立复核无剩余P1/P2；已重启实际页面并确认默认AI理解。首轮全套测试在并行编辑尚未结束时读取中间测试文件而失败，冻结修改后最终全套通过；正式交付仅使用最终结果。同步操作按本会话此前明确授权的GitHub官方API快进方式执行，不删除或覆盖历史。
