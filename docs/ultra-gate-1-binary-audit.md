# Ultra Gate 1：候选发布物离线静态审计

更新日期：2026-09-11。

## 结论先行

QCE `v6.2.10` macOS arm64 和 TraceMemo `v2.3.0` arm64 的固定发布资产均已完成下载摘要核对与离线静态审计。两个候选都保持 **NO-GO / blocked**：不批准安装、首次启动或真实聊天导出，QQ 与微信均为 `real_device_verified=false`。

这不是“发现恶意软件”的结论。它表示发布者身份、签名/公证、许可证或运行副作用没有达到本项目处理真实聊天与密钥所需的可信边界；静态审计也不能证明二进制没有未观察到的行为。

本轮没有运行候选包内的 App、脚本、Node、Rust、helper、连接器或原生模块；没有登录或退出 QQ/微信，没有读取聊天、数据库、Keychain、进程内存或密钥，没有修改/重签名客户端，也没有改变 SIP、Gatekeeper、TCC 或完全磁盘访问。

## 获批范围与实际操作

用户批准的范围仅包括：从两个项目的官方 GitHub Release 下载固定资产到新建私有临时目录；核对发布摘要；离线解包 QCE；校验并只读挂载 TraceMemo DMG；只做文件、签名、公证、entitlement、动态依赖、许可证、原生组件和可见网络端点的静态检查；完成后卸载 DMG。

实际结果：

- 私有临时目录权限设为仅当前用户可访问；没有使用项目数据目录或覆盖已有文件。
- 两个 Release API 在检查时都报告 `immutable: false`，因此固定摘要只是本次审计基线，不能证明远程资产以后不会变化。
- 直接 `curl` 下载 QCE 时发生 HTTP/2 传输错误，留下一个不完整文件；随后使用 macOS `nscurl` 从同一官方资产地址完成下载。未把不完整文件当作候选资产。
- QCE 归档预检 985 个条目：157 个目录、828 个普通文件，没有绝对/越界路径、符号链接、硬链接或设备节点；之后才解包。
- TraceMemo DMG 通过 `hdiutil verify` CRC 校验，以 `read-only,nodev,nosuid,nobrowse` 挂载，审计后已成功卸载并复核为未挂载。
- 没有执行任何候选代码，也没有观察动态网络流量；所有“启动会发生”的描述都来自静态代码/配置，不代表本轮已经发生。

## 发布资产基线

| 候选 | 实际文件大小 | 实算 SHA-256 | 与 Release 元数据 | 结果 |
| --- | ---: | --- | --- | --- |
| QCE `NapCat-QCE-macOS-arm64-v6.2.10.tar.gz` | 41,657,553 bytes | `c4156259a99232ff0fab9a59d90cd76e0581f75fde9ccab85f26d48096ef748e` | 完全一致 | 只证明取得了该次 Release API 所描述的字节 |
| TraceMemo `tracememo-2.3.0-arm64.dmg` | 234,566,020 bytes | `92ec7d0a4f4f0274227ec55e9608ef88f9d826b8033b7034d2c5a74ad3950460` | 完全一致 | 同上；DMG CRC 也有效 |

来源：[QCE v6.2.10](https://github.com/shuakami/qq-chat-exporter/releases/tag/v6.2.10)、[TraceMemo v2.3.0](https://github.com/Wxw-Gu/TraceMemo/releases/tag/v2.3.0)。摘要一致不能替代开发者签名、公证或可复现构建。

## QCE v6.2.10 审计

### 正面证据

- 归档路径结构通过解包前安全预检；包内 README、插件元数据和编译前端均指向 QCE `6.2.10`，NapCat 为 `4.18.19`。
- 编译前端记录的提交前缀 `cf8924d` 与锁定提交 `cf8924d4790ddd857f5e26cca2b0163e4ec3b44f` 一致。
- 常见密钥/私钥格式扫描没有发现真实 API Key、私钥或聊天数据。

这些证据不证明整个发布包可由固定源码复现，也不证明原生模块的行为。

### 签名与原生组件

发现 4 个 arm64 Mach-O：

| 文件 | SHA-256 | 签名静态结果 |
| --- | --- | --- |
| `qce-server` | `799c37eb8f2ff76acb04fd4ba1b1c6cbabca549f388bf4616d6611095d67d85d` | ad-hoc/linker-signed，无 Team ID、Developer ID 或 sealed resources；Gatekeeper 未接纳 |
| `native/napi2native/napi2native.darwin.arm64.node` | `d745adc44bb098f72a01c22c9eef4b5ed5c6fcafd96a2fe47430a4601cf0bebe` | ad-hoc/linker-signed，无 Team ID |
| `native/packet/MoeHoo.darwin.arm64.node` | `9c1dedc880dfcc332d3a61244ffc44aeefc1488e3651f40cfe192ea9adcdbae0` | ad-hoc/linker-signed，无 Team ID |
| `native/ffmpeg/ffmpegAddon.darwin.arm64.node` | `041f24ebe44241f1a725d07996ada665968cccc1ebf229a8595b3a802d26c8c5` | 未签名 |

没有获得 Developer ID、公证或 Gatekeeper 接纳的正面证据。ad-hoc 校验至多帮助判断签名后字节是否变化，不能认证发布者身份。

`napi2native` 的静态字符串/符号显示 Frida Gum、`GumInterceptor`、`wrapper.node`、send/recv 替换，以及 `mach_vm_read`、`mach_vm_write`、`mach_vm_protect` 和线程挂起/恢复能力。这是 QQ 进程内 Hook 路线，不是纯只读文件解析。

### 启动脚本与运行边界

静态审阅显示，完整启动路线会：

- 对 `qce-server` 和整个 `native` 目录执行 `xattr -cr`，主动清除扩展属性；
- 复制现有 QQ.app 为约 1 GB 的 `QQNapCatRuntime.app`，修改副本入口并 ad-hoc 重签名；
- 给副本加入 JIT、unsigned executable memory、disable library validation、disable executable page protection、网络 client/server、麦克风和摄像头等 entitlement，同时不启用 App Sandbox；
- 把真实 QQ 数据目录链接进无沙箱运行副本；目标已存在时，脚本可能把它移动为 `.qce-unlinked-backup`；
- 要求退出正常 QQ 并在副本中扫码登录；正常 QQ 与副本会竞争桌面登录名额，两个客户端同时访问同一数据库的风险没有被发布物证据消除。

本地桥固定使用 `127.0.0.1:40654`，但端口占用处理会通过 `lsof` 找到监听 PID 并直接发送 `SIGTERM`，没有先验证进程归属。QCE 服务本身的固定源码可绑定 `0.0.0.0`；即使默认 IP allowlist 包含回环，也不能把它等同于只监听回环。

前端/插件中可见自动外联 GitHub、npmmirror 字体、Vercel Analytics、CDN fallback、插件索引和代理/镜像地址。没有发现明确把聊天正文上传第三方的静态证据，但原生代码不透明且存在自动联网，不能据此证明“不外发”。standalone 启动器还会把本地 Token 和带 Token 的 URL 写入终端/日志；NapCat WebUI 的 URL 代理能力也扩大了 Token 泄漏后的本地风险面。

### 版本、许可与决定

包内 `qqnt.json` 仍记录 Windows/x64 QQ `9.9.22-40990`，不能证明支持本机 macOS QQ `7.0.1-52892`。发布包根目录没有产品级 `LICENSE`、`NOTICE`、`THIRD_PARTY`、SBOM 或 lockfile；虽然包含部分依赖许可证，仍不足以覆盖 Frida、FFmpeg、LAME、Capstone 等原生供应链。页面的个人/学习及非商业表述与仓库 GPL-3.0/NapCat 独立条款之间也存在需要澄清的授权边界。

因此 QCE 保持 `blocked`。本项目不会请求用户逐项放宽这些边界来强行继续，也不会把 standalone、健康接口或手动上传写成 QQ 自动导出成功。

## TraceMemo v2.3.0 审计

### 正面证据

- DMG 摘要与 Release 元数据一致，CRC 有效，并以只读方式检查。
- App 的 `Info.plist`、`CFBundleVersion`、`CFBundleShortVersionString` 和 ASAR 内根 `package.json` 均为 `2.3.0`。
- `ElectronAsarIntegrity` 中的 SHA-256 与 ASAR JSON header 一致。
- `xkey_helper` 的 Git blob SHA-1 为 `1c9b951830de80ef672d14a35bf28bb3a0eda49a`，与固定源仓中的同名二进制 blob 一致。
- 密钥、API Token、AI Key 和上传 Token 的打包代码使用 Electron `safeStorage`，并把文件权限设为 `0600`。

ASAR 摘要值位于未被可信签名绑定的 `Info.plist` 中；helper 的 blob 一致也只证明字节对应，不能提供其可审计源码或发布者身份。

### 签名、公证与原生组件

- DMG 没有代码签名；App 没有 Developer ID 或 Team ID，主标识是 `Electron` 而非 `com.tracememo.app`。
- 主 App、Helper 和 Electron 组件只有 ad-hoc/linker signature，缺少 sealed resources；`codesign --verify --deep --strict` 失败。
- 静态审计的 34 个 Mach-O 路径中，0 个有 Team ID，0 个有 Hardened Runtime；15 个只通过内部 ad-hoc 字节校验，19 个签名无效或完全未签名。
- `xkey_helper` 与 `libwx_key.dylib` 的 arm64/x86_64 slice 都报告签名无效；Gatekeeper/`spctl` 没有接纳，`stapler` 也没有验证到有效公证票据。
- 下载物没有 `com.apple.quarantine`，因此不能依赖常规首次启动 quarantine 保护；这可能与下载方式有关，不能反推普通浏览器下载一定相同。

`xkey_helper` SHA-256 是 `4c9d837ab9138ae62321949a3239dcb50f18fe728d39b610692ac983a5fa97c7`。其 entitlement 包含 debugger 和 unsigned executable memory，导入 `task_for_pid`、`processor_set_tasks`、`mach_vm_read_overwrite`、`mach_vm_write`、`mach_vm_protect`、`ptrace`；静态字符串还显示进程附加、密钥扫描、内存保护修改和 breakpoint patch。这确认自动取钥不是纯只读数据库扫描；检查 SIP、请求管理员权限并运行 helper 的路线继续被项目边界禁止。

### 首次启动的静态副作用

打包主进程代码显示，首次启动不是纯离线初始化：

- 默认启动 `127.0.0.1:6131` 本地 API；
- 无条件启动 `127.0.0.1:5300` Agent Hub，并执行包内未获可信签名的 `wechat-connector accounts --json`；如已有连接器账号，会进一步监听 `127.0.0.1:18011`；
- 约 1.5 秒后检查 GitHub 更新；macOS 自动下载虽关闭，联网检查仍会发生；
- 启动定时日报调度器；关闭窗口默认倾向于隐藏到托盘并继续运行。

没有发现自动注册 Login Item、LaunchAgent 或安装脚本。也没有发现未配置时自动调用 AI 的证据。但 App 包含可选 DeepSeek/OpenAI/Anthropic/通义/Moonshot/MiniMax/Ollama、自定义 Provider、Fish Audio、Hugging Face 模型、卡片上传和远程头像能力。连接器还含腾讯机器人二维码登录、消息发送、媒体上传、inbound webhook 和本地 HTTP API。

`127.0.0.1:6131` 的同一个 Bearer Token 不只授权聊天读取，也覆盖定时任务创建/修改/删除/立即执行和 `/agent/send`；没有看到细粒度 scope。配置还允许改为非回环监听。平台适配器虽然只调用读取 allowlist，无法移除工具本身的广权 Token 与写操作攻击面。

### 版本、依赖、许可与决定

固定源码提交根 `package.json` 是 `2.2.3`，发布包内却是 `2.3.0`，没有可复现构建说明解释差异。App 声明最低 macOS 12，而密钥/WCDB 原生库的 Mach-O `minos` 是 15.0。包内包含 179 个依赖级 LICENSE/NOTICE 文件，但没有产品级 LICENSE、COPYING、SBOM、THIRD_PARTY 或汇总 attribution，不能覆盖所有内置原生组件；源仓仍只有个人学习/非商业用途的自定义条款。

活跃 WCDB bridge 还保留开发机绝对 RPATH；发布物另含一个依赖缺失 WCDB framework 的冗余库。结合签名、公证、版本/源码对应、许可证、本机微信 `4.1.13` 未被上游明确验证，以及启动即运行 helper/联网检查等问题，即使用户已经合法持有数据库密钥，当前发布物仍保持 `blocked`，不进入手动密钥初始化。

重新评估至少需要：Developer ID 签名且可验证公证的固定发布物、全包严格签名校验通过、源码/tag/包内版本及构建可复现关系一致、产品许可证/SBOM/原生组件来源清楚、明确支持微信 `4.1.13`，并提供可关闭更新、Agent Hub、连接器、定时发送和外部 AI 的离线模式。独立构建并裁减固定源码是另一项开发与运行决策，不在本次批准内。

## P0 验收状态

| 来源 | 资产静态审计 | 安装/启动 | 会话元数据 | 小范围正文导出 | 本地文件验证 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| QQ / QCE | 已完成，NO-GO | 未执行 | 未实测 | 未实测 | 未产生真实来源文件 | `blocked`，`real_device_verified=false` |
| 微信 / TraceMemo | 已完成，NO-GO | 未执行 | 未实测 | 未实测 | 未产生真实来源文件 | `blocked`，`real_device_verified=false` |

手动上传仍可作为明确标注的用户操作路径继续使用 P1/P2/P3；它不会被记为 QQ 或微信自动导出成功，也不会改变上表状态。

## 临时产物与清理结果

审计结束时，私有临时目录 `/private/tmp/chat-to-howto-ultra-audit.k88lz8` 占用约 391 MB，曾保留：

- 两份完整官方发布资产；
- QCE 的私有解包目录；
- Release 元数据；
- QCE 失败传输留下的 7,708,672-byte 不完整文件；
- 一个 280-byte、内容为 GitHub 未认证 API rate-limit 错误的 `tracememo-tree.json`，它不是源码树证据；
- 已卸载后的空挂载点目录。

这些文件不含本项目读取到的聊天、数据库、账号或密钥。2026-09-11，用户明确批准删除这个精确目录及其中本轮审计下载的公开发布包、解包内容和不完整残片。执行前确认目标解析为该精确私有临时路径，并确认项目仓库及本正式审计文档仍存在；随后直接递归删除该目录，执行后复核路径已不存在。

该删除是直接删除，不可从本项目恢复。删除目标不包含项目仓库、用户数据、聊天记录、数据库或项目生成的正式交付文档；这些范围没有被删除命令指向。清理后没有继续 QCE/TraceMemo 的安装、启动、账号、密钥或真实适配探索，两个来源的 NO-GO 状态不变。
