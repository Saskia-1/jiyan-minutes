# 验收报告

更新时间：2026-09-13。本报告区分代码实现、合成自动化、当前 Mac 安全检测、真实来源验证和真实 Provider 验证；任何一项都不借另一项升级状态。

## 结论概览

| 阶段 | 实际状态 | 说明 |
| --- | --- | --- |
| P0 环境检测与来源契约 | 代码/合成测试、固定资产下载验哈希与离线静态审计完成；真实 QQ 与微信均 blocked/未实测 | 当前 Mac 检出 QQ 7.0.1、微信 4.1.13；QCE/TraceMemo 发布物均为 NO-GO，未安装或启动。 |
| P1 导出、落盘、导入、搜索 | 代码与合成 E2E 完成 | 没有用手动上传或固定文件冒充真实来源成功。 |
| P2 Provider 生成 | 通用抽象、DeepSeek 适配器、Fake/Deterministic Provider E2E 完成；真实 DeepSeek 未实调 | 核心服务不导入 DeepSeek，非 DeepSeek 名称的注入 Provider 可完成同一全链路；没有读取/使用真实 Key，也未产生已知 API 费用。 |
| P3 文档与交付 | 编辑、版本、Markdown/DOCX、删除、恢复、启动、文档已实现 | DOCX 自动回读通过，4 页渲染结果已逐页检查。 |
| P4 本地聊天导入 | TXT/JSON/JSONL/CSV/XLSX/单文件 ZIP/粘贴的预览、确认、恢复、搜索到文档合成链路完成 | 全部使用合成数据；真实 QQ/微信内容未读取。Accessibility 仅运行无正文 self-test，目标能力未实测。 |

Ultra Gate 1 的调研、固定资产摘要核对和离线静态审计已完成，详见 [调研与决策记录](ultra-gate-1-research.md) 与 [发布物静态审计](ultra-gate-1-binary-audit.md)。审计没有执行候选工具或读取真实数据；两个候选均因可信链、原生能力、运行副作用、许可/版本或兼容证据问题停在安装之前。

用户批准后，精确私有审计目录 `/private/tmp/chat-to-howto-ultra-audit.k88lz8` 已直接删除并复核不存在。项目仓库、用户数据、聊天记录、数据库与正式交付文档没有被删除命令指向。此后未继续任何真实适配探索。

## 已实现

- 严格回环 Host/Origin、浏览器会话、HttpOnly Cookie、CSRF、无缓存错误响应；活跃租约可在 7 天绝对恢复期限内续期，旧数据库迁移不扩权，过期 cookie 不可无限复活。
- QQ/微信独立状态、公开版本绑定、短期不透明会话选择、Keychain 秘密引用与一次性秘密授权；当前 Gate 1 blocker 同时由前端和后端强制执行，清理操作不被锁死。
- 只读 QCE/TraceMemo 适配器 allowlist、分页/字节/时间/重复/完整性证据与取消边界。
- 范围冻结、一次性导出授权、幂等任务、原子私有文件、SHA-256/计数/范围校验、自动导入和快照限定搜索。
- 通用 `LLMProvider`/factory 边界、DeepSeek 固定 HTTPS Chat Completions 适配器、确定性 Fake Provider、完整脱敏外发正文预览、预算/分块、严格 JSON 提取/合并/证据校验、缓存、取消/重试、每日请求上限和不含正文的逐请求用量。
- 追加式人工版本、本地 Markdown/DOCX 双模式导出、影响指纹删除、文件暂存/事务/启动恢复、来源断开。
- 手动聊天记录先预览后确认，支持 TXT 两种明确方言、JSON、JSONL、CSV、XLSX、受支持单文件 ZIP 和粘贴；输入/展开/行列/字段/会话/问题/缓存均有硬上限，XLSX 畸形引用与类型失败关闭。
- 每次手动导入原子创建独立 batch/snapshot，稳定身份按文件与解析选项命名空间化；恢复保留提交时区、刷新不透明会话句柄，可分页逐批删除，并复用 Search → exclusion → Generation → evidence → Document 全链路。
- 只读 macOS Accessibility POC 有独立显式目标确认、精确 bundle allowlist、聚焦窗口/可见静态文本/节点深度/字符与总量边界；未运行目标模式，不能声称正文、发送者、时间或顺序能力。
- macOS arm64 初始化、同源单进程启动/停止、Python 与 pnpm 锁文件、中文操作文档。

## 自动化结果

本次最终回归实际结果：

- 后端全量 `.venv/bin/pytest -q`：757 passed，1 skipped，101 subtests passed，2 warnings，用时 25.54s。唯一 skip 是需要显式设置 `RUN_LOGIN_KEYCHAIN_INTEGRATION=1` 的默认登录钥匙串真实集成测试；本次未改动真实 Keychain。两个警告来自 TestClient/httpx2 和 anyio 别名的依赖弃用提示。
- 后端合成导出→搜索→生成→编辑→Markdown/DOCX→删除 E2E：包含在上述全量结果中，单项 1 passed。
- 后端手动导入→搜索→FakeProvider 生成→证据→文档→删除 E2E：包含在上述全量结果中，单项 1 passed。
- 前端 Vitest：10 files / 183 tests passed。
- TypeScript `tsc --noEmit`：通过。
- Vite production build：28 modules transformed，通过；主 JS 324.37 kB（gzip 96.91 kB）。
- Playwright 完整 P0/P1/P2/P3/P4 顺序集：桌面与 360px 各覆盖 4 项，本次 8 passed（16.7s）。P4 覆盖粘贴→预览→确认→搜索→排除→FakeProvider 外发→证据→文档、刷新恢复多个批次及逐批删除。
- 生产运行时烟雾是前序验收证据：实际进程只绑定 `127.0.0.1:8765`；前端根路由同源服务返回，无会话访问 `/api/sources` 返回 401。本次为避免触碰既有运行数据没有重复启动用户生产配置。
- Python `pip check`：No broken requirements found（pip 用户缓存不可写，因此禁用缓存；不影响依赖一致性结果）。
- 仓库常见私钥/API Key 模式扫描：无命中。
- Accessibility POC 重新编译并运行无正文 self-test：QQ 7.0.1、微信 4.1.13 已安装但未运行；Accessibility 未授权，`contentRead=false`，未检查客户端内容、未滚动、未执行 UI 修改、未请求权限弹窗。
- 正式交付 DOCX 的自动回读和 4 页逐页渲染检查是前序验收证据，本次 P4 改动未触及该文档生成链。

以上全部使用合成内容、FakeProvider、私有临时目录；浏览器 E2E 明确阻止外部请求。

## 真实设备与外部服务

### QQ

- 实测：完成安全元数据检测及 QCE 固定资产下载、独立摘要核对、安全解包和离线静态审计；QQ 7.0.1 build 52892 已安装，主程序是 x86_64/arm64 universal，App Sandbox 开启。QCE 没有安装或启动。
- 未实测：QCE 安装/启动、Token、会话列表、窄范围正文读取、真实文件计数/哈希/范围。
- 供应链证据：QCE v6.2.10 完整资产实际大小/实算 SHA-256 与 Release 元数据一致；但核心文件只有 ad-hoc 签名或未签名，没有 Developer ID/公证/Gatekeeper 接纳证据，许可/SBOM/原生组件归属不完整。原生模块包含 Frida 与进程内 Hook/Mach 内存能力。
- blocker：无 QQ `7.0.1-52892` + QCE `6.2.10` 精确实机兼容证据；launcher 会清除扩展属性、复制并弱化重签名无沙箱 QQ 运行副本、链接真实数据、可能移动既有链接目标；服务可绑定 `0.0.0.0`，端口冲突处理还可能终止未核验归属的监听进程。当前发布物为 NO-GO，不请求继续安装。
- 状态：`blocked / documented_not_tested`，`real_device_verified=false`。

### 微信

- 实测：完成安全元数据检测及 TraceMemo 固定 DMG 下载、独立摘要/CRC 核对、只读挂载静态审计和卸载；微信 4.1.13 build 269602 已安装，主程序是 x86_64/arm64 universal，App Sandbox 开启，SIP enabled。TraceMemo 没有安装或启动。
- 未实测：TraceMemo 安装/初始化、Token、会话列表、窄范围正文读取、真实文件计数/哈希/范围。
- 供应链证据：TraceMemo v2.3.0 DMG 实际大小/实算 SHA-256 与 Release 元数据一致且 CRC 有效；但 DMG 未签名，App 无 Developer ID/Team ID/Hardened Runtime、深度严格签名失败，关键 helper/库签名无效，Gatekeeper 未接纳且无有效公证票据。固定源码 2.2.3 与发布包 2.3.0 不一致，产品许可/SBOM/原生来源不完整。
- blocker：微信 4.1.13 不在上游公开已测组合；自动取钥要求 SIP 检查、管理员授权和带调试/进程内存能力的 helper。首次启动还会开启 API/Agent Hub、执行连接器账号枚举、启动调度器和检查更新，广权 Token 同时覆盖发送/定时任务写能力。当前发布物为 NO-GO；即使用户已有合法密钥也不进入初始化。
- 状态：`blocked / documented_not_tested`，`real_device_verified=false`。

### DeepSeek

- 实现与 mock：请求目标、请求体、JSON 模式、错误/取消/大小/证据和用量记录均由 MockTransport/FakeProvider 测试。
- 未实测：真实认证、真实配额/限流、真实输出质量、真实 token 账单和真实费用。
- 原因：未获得一次具体真实 Key 使用与可能收费授权；项目没有自动寻找或调用现有 Key。

## 未覆盖或需要人工确认

- 当前 QCE 与 TraceMemo 发布物未通过 ULTRA GATE，不能进入 QQ/微信真实窄范围导出；重新评估需要新的可验证发布/兼容/许可证据和独立计划。
- DeepSeek 真实调用需要用户在本机安全设置专用 Key并确认一次外发；不得把聊天或 Key贴到对话。
- 当前未声称 Windows/Linux 支持或非 Mac CI 等同于 Mac 验收。
- SQLite 未加密；同步/备份/第三方工具缓存和 macOS 权限需要用户按独立边界管理。
