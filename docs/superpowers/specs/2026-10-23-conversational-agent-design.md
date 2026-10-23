# 记言 · Minutes 对话式智能体

状态：用户于2026-10-23明确要求直接按本会话上一条开发Prompt执行；不再重复请求设计批准。

## 产品与交互

首页是会话侧栏、连续消息区、底部附件和输入框。上传当前支持的文件／图片，解析与部分跳过的确认在对话卡片内完成；确认后成为本会话来源。发送自然语言开始任务，聊天内显示实际执行状态、完整脱敏授权卡、结果预览和两份TXT附件。普通路径不再出现四步表单、关键词或方向选择。设置可展开旧高级工具，旧组件测试入口保留。

支持新建、切换、刷新恢复本地会话。后续消息沿用本会话来源和既有完整要求，在512字符的既有topic预算内组合；超出时明确要求简化，不悄悄截断约束。每次整理保留独立不可变文档，作为同一会话的第N版成果，旧成果卡继续可下载；每版双TXT绑定其确切document_id/version_id。不会跨文档合并证据或改动旧版本。

## 后台执行

结构化模型工具选择循环，工具白名单 search_sources、generate_document、ask_user。自动先检查来源元数据；控制模型选择搜索参数，后端执行本地检索，把不含正文的结果数量和查询信息回传下一次控制模型。控制模型依据工具结果决定重新搜索、生成或询问。每次控制调用都显示完整脱敏payload并取得一次性授权；generate_document复用既有v10预览／授权／生成，并自动核验和交付。最多4次控制调用、最多3次搜索；不允许模型任意执行代码或读文件。零命中不能生成。

所有状态来自实际工具执行。控制模型payload仅包含本次完整要求、受限会话请求上下文与本地工具元数据，不附带资料摘录。生成请求才包含所选脱敏资料，并独立授权。取消、换会话、新消息或更改来源都废弃旧授权，晚响应不可覆盖新状态。后端源所有权和时效在每次动作前检查。模型调用共用持久每日上限。真实调用须在产品内逐次授权，开发仅用合成Provider。

## 存储与保留

迁移18添加本地会话表，owner为既有浏览器会话digest；消息、来源与运行状态受revision原子比较更新保护。内部状态不保存导入正文或检索摘录；完整预览只放运行时内存，重启后废弃待授权或运行中任务，保留可恢复消息及文档引用。用户消息／控制上下文如含原文也按7天清理；清理不得通过刷新续期。删除／过期原文导致引用来源不可继续处理，已有文档按原规则保留。

名称、包名、目录、Keychain、v10写作规则、部分成功授权和现有下载边界不变。

## 固定公开接口

base `/api/agent/conversations`，GET列表，POST{}创建。GET `/{id}`完整会话。POST `/{id}/sources` {sources:[{kind:'chat_snapshot'|'content_batch',id,label}]} 替换当前来源，必须全部属于当前会话且有效。POST `/{id}/messages` {text,client_message_id}提交自然语言并自动准备第一轮控制预览。POST `/{id}/runs/{run_id}/approve` {approval_id,preview_fingerprint,confirmed:true} 原子消费授权并后台推进。POST `/{id}/runs/{run_id}/cancel` {} 停止。

列表返回 {conversations:[{conversation_id,title,revision,created_at,updated_at}]}；完整返回：
{conversation_id,title,revision,created_at,updated_at,sources:[{kind,id,label}],messages:[{message_id,role:'user'|'assistant',text,created_at,run_id:null|string,document_id:null|string,version_id:null|string,artifact_revision:null|number}],active_run:null|{run_id,revision,state:'waiting_approval'|'running'|'complete'|'cancelled'|'failed'|'needs_input',stage:string,error_code:null|string,approval:null|{approval_id,kind:'agent'|'generation',preview_fingerprint,provider,model,outbound_payloads:string[],provider_calls,max_output_tokens,expires_at},document_id:null|string,version_id:null|string,artifact_revision:null|number}}。
公开响应不得带内部state/原始请求缓存；错误detail.code安全静态码。所有接口绑定session+CSRF，no-store。
