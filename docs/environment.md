# 当前设备与版本记录

检测时间：2026-09-11。以下只来自安全的本机元数据检测与公开资料核查，不扫描聊天目录、账号、消息、密钥或完整环境变量。

| 项目 | 实测结果 | 状态 |
| --- | --- | --- |
| macOS | 26.5.2（Build 25F84；Darwin 25.5.0） | 已检测 |
| 物理/当前进程架构 | arm64 / arm64；未处于 Rosetta 转译 | 已检测 |
| QQ | 7.0.1（Build 52892）；主程序 x86_64 + arm64 universal；Team ID `FN2V63AD2J`；App Sandbox 开启 | 已安装，真实导出未测 |
| 微信 | 4.1.13（Build 269602）；主程序 x86_64 + arm64 universal；Team ID `5A4RE8SF68`；App Sandbox 开启 | 已安装，真实导出未测 |
| QCE | Spotlight 未检出候选 App；40653 无监听 | 本项目未安装/启动；本次未发现运行实例 |
| TraceMemo | Spotlight 未检出候选 App；6131 无监听 | 本项目未安装/启动；本次未发现运行实例 |
| Python | 3.11.9；当前运行 arm64；解释器文件为 universal2 | 已实测 |
| Node.js | 24.19.0；Mach-O arm64 | 已实测 |
| SIP | enabled | 只读检测，未更改 |
| Xcode Command Line Tools | `/Library/Developer/CommandLineTools` | 已检测，未执行签名或安装 |
| 已知数据容器 | QQ/WeChat Container 与 App Group 顶层目录存在，均为 `0700` | 只检查已知目录的存在性/权限，未枚举内容 |

应用内置检测器仍保守返回 `environment_detection_incomplete`；上表新增的架构、entitlement、容器存在性与端口状态来自 Ultra 阶段的额外只读命令，不会被应用自动升格为兼容或 readiness 证据。`codesign --verify` 在当前检测环境对两个已安装 App 均返回 `CSSMERR_TP_NOT_TRUSTED`，`spctl` 返回签名子系统内部错误；这些结果只说明本次无法完成信任链验证，不据此推断 App 已损坏或被篡改。

本记录不代表 QCE、TraceMemo、QQ 或微信已被启动、登录、授权或读取；也不代表真实聊天已保存。本轮没有读取容器内部文件、聊天数据库、Keychain 或进程内存。真实来源状态见 `docs/acceptance-report.md`。

上述“候选工具未检出”是下载前的系统安装/运行实例检查。此后经用户具体批准，固定 QCE/TraceMemo 发布资产只进入私有临时目录做离线静态审计，没有安装或启动；两个发布物均为 NO-GO，详见 [`ultra-gate-1-binary-audit.md`](ultra-gate-1-binary-audit.md)。
