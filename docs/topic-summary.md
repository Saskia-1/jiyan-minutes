# 主题整理：四种提炼方向

## 使用

1. 沿用手动导入，确认会话与时间范围。
2. 搜索区选“主题相关检索（扩展关键词）”，输入原始主题。
3. 扩展词仅用于本地召回；界面展示关键词。多词结果按消息 ID 去重并保持原始顺序。
4. 在同一快照、会话和时间范围内补充每条命中前后最多两条消息及有效被回复消息。补充内容会展示，用户可以排除。
5. 在生成区选择 viewpoints（观点）、ideas（Idea / 想法）、how_to（方法论 / 怎么做）或 mixed（综合）。确认外发预览，再开始生成。
6. 主要内容旁展开“查看原始依据”。正文和依据分开呈现；证据来自本地原消息，不是模型改写。
7. 创建文档，编辑章节/步骤，保存版本，导出 Markdown 或 DOCX。旧指南和旧版固定字段总结仍能打开、编辑和导出。

普通关键词搜索仍可使用；不强制 AI 扩展。扩展失败时保留原始词搜索，不将扩展词写成聊天事实。

## 共享链路

`Topic → Expansion → Local Search → Bounded Context → Preview / Exclusion → summary_type → Frozen Scope / Redaction / Budget → Chunks → Evidence-checked Extraction → Cross-chunk Synthesis → Final Evidence Validation → Document / Version / Export`

- `generation_planning.py` 负责范围、排序、上下文、消息边界分块和预算，不在生成时悄悄补回被排除的消息。
- `generation_jobs.py` 复用既有预览、一次性授权、用量记录、缓存、取消与恢复；多块新方向比原始分块数多一次整合调用。
- `generation_prompts.py` 使用共用提炼原则加少量方向策略。观点/Idea/综合使用自然章节；how_to 保持原指南字段。
- `generation_extraction.py` 对新方向只接受模型返回的证据 ID；从冻结文本恢复引用并交给 `evidence_validation.py` 再与本地快照核对。
- `generation_merge.py` 保持旧精确去重兼容；新跨块整合由同一 Provider 对照原消息完成，而非简单拼接块结果。
- `documents.py` / `document_export.py` 复用版本与导出；带依据导出使用轻量章节引用和集中证据附录。

## 原问题与改动边界

旧 Summary 不是完全独立的一次性请求：它已经复用了分块、预算和证据。但它只选字面命中的消息、使用固定摘要栏目，跨块只做精确字符串去重；Fake 输出只证明契约可运行，不证明语义整理质量。旧 How-to 的完整范围与操作性字段约束更充分，但旧链路也没有通用的语义整合阶段。

本次没有增加向量检索、新 Provider、会议纪要实体或新来源。新增自然章节是对原生成结果的兼容扩展，而不是另一套文档系统。

## 边界与验证

- 邻近消息仅是上下文候选，不能保证语义相关；仍需人工预览与排除。
- 合并后的召回/上下文最多 2,000 条。单条不能超出分块预算。中间提炼连同引用 ID 最多 12,000 字符；一次跨块整合输入上限为估算 64,000 token，且所有调用仍须满足 100,000 总输入预算。预算在授权前预留，超限要求缩小范围，不自动截断。
- 预览会披露二次发送同一消息与中间提炼；整合请求尚未生成的部分不能在第一次预览展示，预算已预留。没有无限递归摘要。
- DeepSeek JSON 请求显式关闭默认思考模式，使已有输出预算用于结构化正文；空、截断、无效 JSON 或越界引用仍失败关闭，不自动修补模型 JSON，也不自动付费重试。
- 证据校验验证 ID、范围和原文一致性，不等于自动证明每个语义结论都正确；质量仍须阅读原聊天对照验收。
- 未知时间保持未知。评估脚本可对显式消息 ID 开启内部 `allow_undated`；没有更改产品的时间筛选/导入规则，也不把评估夹具称作 UI 导入验收。
- `backend/scripts/evaluate_topic_generation.py` 使用用户显式给定的文件、发言人和主题，在忽略目录 `generated/` 保存私有快照、旧/新结果及 Markdown/DOCX。原始聊天和生成文档不得提交。
- 真实对照使用同一消息 ID 集合、主题、模型和分块上限；旧 Summary 本无 `summary_type`，不能声称旧版已支持某个方向。Fake 回归只证明契约与工作流，不能代替真实模型质量对照。

DeepSeek 参数依据：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。
