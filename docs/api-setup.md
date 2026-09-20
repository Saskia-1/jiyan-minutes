# DeepSeek API 配置、外发与费用

## 当前实现

P2 的生成编排先依赖通用、可运行时检查的 `LLMProvider` 协议和注入式 Provider factory；范围冻结、预算、脱敏、证据校验、缓存、取消与一次性外发授权都不依赖 DeepSeek。DeepSeek 是当前生产配置允许的具体适配器，只有组合根负责构造它；核心 API/服务模块导入时不会加载 DeepSeek 适配器。FakeProvider 及自定义命名的 Deterministic Provider 只在合成测试中启用，且走相同生成链路，不会成为生产失败时的静默降级。

DeepSeek 适配器固定调用 `https://api.deepseek.com/chat/completions`，使用 Chat Completions 和 `response_format={"type":"json_object"}`，不假设支持 OpenAI Responses API 或所有 OpenAI 参数。当前本项目配置允许的模型为 `deepseek-v4-flash` 与 `deepseek-v4-pro`。参考的是 DeepSeek 官方 [API 文档](https://api-docs.deepseek.com/)、[创建对话补全](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [价格页](https://api-docs.deepseek.com/quick_start/pricing)。

ChatGPT 订阅不是 DeepSeek API 额度，本项目不会购买额度、创建账号、搜索本机已有 Key，或因为环境里“可能有 Key”就自动调用。真实 Provider 尚未用真实 Key 验证。

## 配置方式

`runtime-config.json` 是工作区外、权限 `0600` 的非秘密配置。`llm_provider` 设为 `deepseek`，`deepseek_model` 设为上述固定模型之一，`llm_daily_request_limit` 为 1–10000；初始化默认 100。API Key 只在启动进程环境中显式提供：

```sh
export DEEPSEEK_API_KEY="your-dedicated-key"
export CHAT_TO_HOWTO_CONFIG="/absolute/private/data/runtime-config.json"
./scripts/start_macos.sh
```

不要把实际值写入 `.env.example`、仓库、命令参数、问题报告或对话。当前实现不会把 DeepSeek Key 持久化到 SQLite 或前端；环境变量由用户负责在本机安全注入。若需要长期凭据管理，应使用专用启动环境或后续实现独立 Keychain 流，而不是提交 `.env`。

## 每次外发前会发生什么

1. 本地冻结快照、会话、半开时间范围、主题、模式、排除项、模型、脱敏修订和预算。
2. 本地做确定性目标脱敏：明显邮箱、中国大陆手机号、带标签的 QQ/微信号、Bearer/API Key 形态会替换。此规则不能保证识别所有个人信息，用户必须查看预览。
3. 页面显示每个即将作为 `user` 消息发送的完整 UTF-8 JSON 文本块、条数、字符数、最多调用数和输出上限。缓存命中时外发正文为空、请求数为零。
4. 只有用户勾选本次外发确认，后端才签发短期、一次性、会话与内容指纹绑定的授权。范围变化、过期或重放均失败。
5. 请求还包含固定系统提示，要求只输出结构化 JSON、只引用给定消息标识、不得编造证据。不会发送附件、数据库、来源 Token、DeepSeek Key 或绝对本机路径。

搜索结果中的“不用于 AI 生成”只影响下一次生成范围，不删除本地消息。单次最多排除 2,000 条；修改排除项会立即清除旧预览和旧授权，必须重新检查外发内容。建立预览、取得授权或生成任务未结束时，页面会锁定来源、账号、会话、时区、时间和排除控件。后端还会确认每个排除 ID 都属于当前会话、快照和时间范围，越界 ID 会被拒绝。

## 用量、费用、取消与重试

每次即将发给 Provider 的调用先写一条不含正文的用量记录，保存 Provider/模型、状态、可用的请求 ID、输入/输出 token 和时间。价格来源、版本、观察时间、币种及估算/实际费用目前都为 `unavailable/null`，因此 UI 明示“费用无法估算”，不会用过期价格编造金额。

取消会尝试中止本地 HTTP 任务，但 Provider 可能已收到请求并计费；状态会显示“可能已产生费用”。失败后的重试重新执行同一冻结范围，保留之前的请求用量记录，不假装续接半个响应。每日请求上限在新请求落库前原子检查；达到上限返回 `daily_usage_limit`，不会发出下一次网络请求。缓存命中和本地文档编辑/导出不会增加 Provider 调用。

常见错误：

- 未设置 Key/Provider：`provider_unavailable` 或配置错误；本地能力继续可用。
- 401/403、额度或限流：对外统一为不含响应正文的 Provider 失败，真实费用以供应商控制台为准。
- 超时/网络失败：任务失败；可能已产生一次请求。
- 空输出、截断、非 JSON、结构错误：拒绝结果，不保存为已验证指南。
- 越界/修改/不存在的证据 ID：`evidence_invalid`，失败关闭。
