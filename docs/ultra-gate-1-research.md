# Ultra Gate 1：QQ / 微信调研、静态审计与决策记录

更新日期：2026-09-11。本轮先完成 `research / inspect / risk assessment / plan`，再经用户具体批准下载固定发布资产并完成离线静态审计；没有进入安装、启动或真实适配执行。二进制清单、摘要和逐项证据见 [`ultra-gate-1-binary-audit.md`](ultra-gate-1-binary-audit.md)。

## 本轮结论

QQ 与微信的代码适配器和合成测试已经完成，但两个来源在当前 Mac 上仍分别是 `blocked / documented_not_tested`，且都是 `real_device_verified=false`。固定 QCE 归档已离线解包，TraceMemo DMG 曾以只读方式挂载并已卸载；没有安装、启动或执行任何候选 App、脚本、helper、连接器或原生模块。没有登录或切换账号，没有读取容器内容、聊天正文、数据库、Keychain 或进程内存，也没有更改 SIP、Gatekeeper、TCC/完全磁盘访问、应用签名或客户端。

只有对某个来源真实完成“枚举会话 → 用户选择会话 → 选择时间范围 → 导出 → 验证 → 持久化”，才能单独把该来源标记为 `real_device_verified=true`。健康接口、Token、登录成功、查到联系人、合成测试或手动上传都不能替代这条验收链。

## 当前 Mac 的安全只读检测

| 项目 | 实测结果 | 证据边界 |
| --- | --- | --- |
| 系统 | macOS 26.5.2（Build 25F84），Apple Silicon `arm64`，SIP enabled | 未更改系统保护 |
| QQ | 7.0.1 build 52892；主程序 universal x86_64/arm64；Team ID `FN2V63AD2J`；App Sandbox 开启 | 只读应用元数据，未启动、退出或登录 |
| 微信 | 4.1.13 build 269602；主程序 universal x86_64/arm64；Team ID `5A4RE8SF68`；App Sandbox 开启 | 只读应用元数据，未启动或登录 |
| 本地容器 | QQ/微信 Container 与 App Group 的已知顶层目录存在，权限为 `0700` | 只检查存在性/权限，未枚举或读取内部文件 |
| 候选工具 | Spotlight 未发现 QCE/TraceMemo；本机端口 40653/6131 无监听 | 只证明当时未发现应用/服务，不证明从未有过其他痕迹 |
| 信任链检查 | 两个现有客户端的 `codesign --verify` 均返回 `CSSMERR_TP_NOT_TRUSTED`，`spctl` 返回签名子系统内部错误 | 本轮无法完成信任链评估；不据此推断应用已损坏或被篡改 |

更精简的环境摘要见 [`environment.md`](environment.md)。

## QQ / QCE 候选路线

### 公开与静态审计证据

- 候选版本是 [QCE v6.2.10](https://github.com/shuakami/qq-chat-exporter/releases/tag/v6.2.10)；注释 tag 对应的目标提交是 `cf8924d4790ddd857f5e26cca2b0163e4ec3b44f`，tag 对象与提交都未签名。
- macOS arm64 预览资产 `NapCat-QCE-macOS-arm64-v6.2.10.tar.gz` 的实际下载大小为 41,657,553 bytes，实算 SHA-256 为 `c4156259a99232ff0fab9a59d90cd76e0581f75fde9ccab85f26d48096ef748e`，与 Release 元数据完全一致；Release API 报告 `immutable: false`。
- 离线审计发现 4 个 arm64 Mach-O：3 个仅 ad-hoc/linker 签名、1 个未签名，没有 Developer ID/Team ID/公证/Gatekeeper 接纳证据。原生 `napi2native` 明确包含 Frida Gum、send/recv Hook 与 Mach 内存/线程能力。
- QCE 源码为 GPL-3.0；捆绑的 NapCat 和页面有独立/自定义条款。发布包没有产品级 LICENSE、NOTICE、THIRD_PARTY、SBOM 或完整原生依赖授权清单。
- 上游只给出通用最低 QQ build 和推荐版本；没有找到本机 QQ `7.0.1-52892` + QCE `6.2.10` 这一精确 macOS 组合的实机证据。不能用数字 build 的大小直接推导兼容。

### 机制与风险判定

QCE 的 [macOS 部署路线](https://shuakami.github.io/qq-chat-exporter/docs/macos-deploy.html) 不是对现有 QQ 客户端的纯只读旁路提取。完整模式会复制 QQ.app 生成约 1 GB 的 `QQNapCatRuntime.app`，修改副本的 `package.json`，本地重签名，移除副本的 App Sandbox，并将它链接到 QQ 数据路径。还要完全退出正常 QQ，由用户在副本中扫码登录，会占用桌面登录名额。该服务实现可绑定 `0.0.0.0`，需要在真实启动前独立验证本机可达边界。

实际包内 launcher 还会用 `xattr -cr` 清除扩展属性，给副本加入 JIT、未签名可执行内存、关闭 library validation/可执行页保护等 entitlement，并可能移动既有数据链接目标。本地桥端口冲突时会向 `lsof` 找到的监听 PID 发送 `SIGTERM`，但不先验证进程归属。包内前端/插件存在自动 GitHub、字体、Analytics、CDN、插件索引及代理/镜像网络面；没有发现明确聊天正文外发静态证据，但不能据此证明原生模块和运行时“不外发”。

Standalone 模式不需要登录 QQ，但新会话/消息获取类功能会返回 `503 STANDALONE_MODE`，不能完成本需求的真实小范围导出。Docker/Linux QQ 也是另一个登录和缓存环境，不能被当成读取本机日常 macOS QQ 历史的无改动替代方案。

因此，在当前边界内没有已验证的“不复制/修改/重签名客户端、不退出/重登录”的 QCE 实时历史读取路线，且发布物可信链未通过。QQ 保持 `blocked / NO-GO`，不安装、不启动、不尝试真实导出。

### 已停止的后续阶段

1. **资产静态审计**：已完成，结论 NO-GO。
2. **安装/首次初始化**：未批准，也不建议基于当前发布物继续。
3. **QQ 运行副本改动**：当前发布物不执行；未来替代候选通过 Gate 后，仍须对精确路径、风险、回滚和残留另行确认。
4. **账号动作**：当前发布物不执行；未来也只能由用户在工具界面操作，并另行确认退出/扫码登录和桌面名额影响。
5. **元数据连接与正文导出**：当前未实测；未来候选通过 Gate 后，仍须先验证回环/Token/元数据，再单独批准用户所选小范围正文读取。
6. **清理**：只在检查运行副本、数据链接、QCE 配置/缓存/任务文件和本平台导出批次的确切位置后，再请求指定清理授权；不默认删除。

## 微信 / TraceMemo 候选路线

### 公开与静态审计证据

- 候选版本是 [TraceMemo v2.3.0](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0)；轻量 tag 直接指向未签名提交 `40190511fcad656ce439eece2201f1eb9a0d9352`，GitHub Release API 报告 `immutable: false`。
- arm64 DMG 实际下载大小为 234,566,020 bytes，实算 SHA-256 为 `92ec7d0a4f4f0274227ec55e9608ef88f9d826b8033b7034d2c5a74ad3950460`，与发布元数据完全一致，DMG CRC 有效。
- tag 内 `package.json` 仍声明 2.2.3，而发布包 `Info.plist` 和 ASAR 根包元数据为 2.3.0，没有可复现构建说明解释差异。根目录没有标准产品许可证；发布包虽含依赖级许可文件，却没有产品级 LICENSE/COPYING、SBOM、THIRD_PARTY 或完整原生组件汇总。
- 固定源码的构建配置为 `notarize: false`。实际 DMG 未签名；App 无 Developer ID/Team ID、无 Hardened Runtime/sealed resources，深度严格签名校验失败；34 个已查 Mach-O 中 0 个带 Team ID，`xkey_helper` 与 `libwx_key.dylib` 两个架构切片均报告签名无效，Gatekeeper 未接纳，也没有有效 stapled 公证票据。
- `xkey_helper` 的 Git blob 与固定仓库中的二进制 blob 一致，但没有可审计源码；它带 debugger/unsigned-executable-memory entitlement，并导入 `task_for_pid`、`mach_vm_read/write/protect`、`ptrace` 等进程控制/内存能力。
- 上游只明确列出完整测试的 macOS 微信 `4.1.8.100`。本机 `4.1.13` 是未验证组合，但不能写成上游已声明不兼容。

### 数据、密钥与风险判定

固定源码会从选定账号的 `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<account>/db_storage/...` 数据树读取微信 4.x 数据，通过捆绑的原生 WCDB 组件和 64 位十六进制数据库密钥打开。本轮只根据固定源码确认路径和预期机制，没有读取该目录、不知道账号名，也没有验证 4.1.13 的实际 schema、密码参数和分片格式。未找到经验证的微信官方备份格式导入路线。

TraceMemo 自动取钥路线会检查 SIP，请求管理员授权，然后用 `xkey_helper` 扫描正在运行的微信进程内存。当前 SIP 已启用，而本项目明确禁止自动关闭 SIP、提权或进程内存扫描，因此这条路线是 **NO-GO**，不请求开放式或模糊授权，也不用自制脚本绕过。

在发布物审计前，文档识别的唯一有条件路线原本是：用户已经通过独立、合法方式拥有当前账号的正确数据库密钥，并只在 TraceMemo 本机 UI 中输入。固定源码使用 Electron `safeStorage`；macOS 上由 Keychain 支撑，每账号文件设为 `0600`。本平台永远不索取、接收、保存、记录或显示数据库密钥，只保存 TraceMemo 的回环 API Token 引用。该条件只解决密钥来源，不会豁免发布物可信链检查。

离线审计又发现，首次启动会默认开启 `127.0.0.1:6131` API、无条件开启 `127.0.0.1:5300` Agent Hub 并执行包内连接器账号枚举、启动日报调度器，并在约 1.5 秒后检查 GitHub 更新；已有连接器账号时还可能启动额外端口。同一 Bearer Token 不只读聊天，也能执行定时任务写操作和 `/agent/send`。因此，即使用户已经合法拥有数据库密钥，当前二进制也不进入手动密钥初始化。

### 已停止的后续阶段

1. **使用权限与供应链前置**：当前证据未通过；重新评估需要维护者或新发布物补齐许可、签名/公证、SBOM/第三方通知、`xkey_helper` 来源和 4.1.13 兼容性。本项目不会未经授权代替用户联系维护者。
2. **资产静态审计**：已完成，结论 NO-GO。
3. **安装/首次启动**：未批准，也不建议基于当前发布物继续。
4. **手动密钥初始化**：当前发布物不执行；未来候选先通过 Gate 且用户确认已有合法密钥时才重新评估，并继续禁止密钥进入对话、命令、日志、截图或代码库。自动取钥始终不在计划内。
5. **元数据连接与正文导出**：当前未实测；未来候选通过 Gate 后，仍须先验证回环/广权 Token/联系人元数据，再单独批准用户所选范围。无分页/完整性证据时结果仍标记 `unknown`。
6. **清理**：未来若实际初始化，须分开处理平台 Token、导出批次、工具密钥/缓存/索引/日志、Keychain 项和系统权限；未逐项验证前不声称“断开来源”已清除第三方工具痕迹。

## 当前安全停顿点

固定资产下载和离线静态审计已经完成，TraceMemo DMG 已卸载。两个候选均因签名/公证、原生能力、运行副作用、版本/许可或本机兼容证据不足而停在 `blocked / NO-GO`；没有向安装、首次启动、账号操作、密钥输入或正文导出推进。

私有审计目录 `/private/tmp/chat-to-howto-ultra-audit.k88lz8` 曾约 391 MB，含完整公开资产、QCE 解包内容、元数据和一次失败传输留下的不完整文件；未包含本轮读取到的聊天、账号或密钥。2026-09-11，用户对该精确路径给出单独清理批准后，目录已直接删除并复核不存在；删除不具备项目内恢复能力，且没有指向项目仓库、用户数据、聊天记录、数据库或正式交付文档。此后不再继续真实适配探索。手动上传可以继续作为明确标注的 P1 入口，但不能替代 P0 自动导出验收。
