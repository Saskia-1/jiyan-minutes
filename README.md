# 记言 · Minutes

记言 · Minutes 是一个中文、本地优先、单用户的 macOS Web 应用：在用户明确选择并确认后，从 QQ 或微信的受审查本机适配器导出窄范围聊天，原子保存、校验并导入 SQLite；随后可本地搜索、按独立授权调用 DeepSeek 生成带证据的操作指南、人工编辑版本，并导出 Markdown 或 DOCX。

当前代码和合成测试可运行；**真实 QQ、微信导出仍未验证**。平台已提供全本地的 TXT、JSON、JSONL、CSV、XLSX、受支持单文件 ZIP 和粘贴文本导入，必须先预览、再确认，然后复用同一套 Snapshot → Search → Generation → Document 链路；已确认批次可凭当前 HttpOnly 本地浏览器会话在 7 天绝对恢复窗口内分页恢复并逐批删除。QCE v6.2.10 与 TraceMemo v2.3.0 的固定官方发布资产已完成独立摘要核对和离线静态审计，但均因签名/公证、原生能力、启动副作用、许可/版本或兼容证据问题判定为 `blocked / NO-GO`，没有安装或启动。项目不会自动登录账号、读取聊天目录、获取微信数据库密钥、提权、关闭 SIP、授予完全磁盘访问、修改或重签名客户端。详见 [P4 导入与 Accessibility POC](docs/p4-real-chat-ingestion.md)、[Ultra Gate 1 调研记录](docs/ultra-gate-1-research.md)、[发布物静态审计](docs/ultra-gate-1-binary-audit.md)、[验收报告](docs/acceptance-report.md) 和 [来源配置](docs/source-setup.md)。

## Apple Silicon 安装

要求：macOS Apple Silicon、Python 3.11+、Node.js 24.x 与 corepack。脚本只安装本项目锁定依赖和创建用户明确批准的本地目录；不会安装聊天导出工具。
```sh
./scripts/bootstrap_macos.sh
```

脚本默认建议：

- 数据：`~/Library/Application Support/Chat to How-To`
- 导出：`~/Documents/Chat to How-To Exports`

交互界面会显示这两个位置并要求输入 `YES`。也可显式指定绝对路径：

```sh
./scripts/bootstrap_macos.sh --data-dir "/absolute/private/data" --export-root "/absolute/approved/exports"
```

已有 `runtime-config.json` 不会被覆盖，SQLite 通过迁移升级而不是重建。Python 依赖固定在 `backend/requirements.lock`，前端依赖固定在 `frontend/pnpm-lock.yaml`。

## 启动与停止

若使用默认路径：

```sh
./scripts/start_macos.sh
./scripts/stop_macos.sh
```

自定义数据目录时：

```sh
export CHAT_TO_HOWTO_CONFIG="/absolute/private/data/runtime-config.json"
./scripts/start_macos.sh
./scripts/stop_macos.sh
```

服务只监听 [http://127.0.0.1:8765](http://127.0.0.1:8765)，单个 FastAPI 进程同时提供生产构建前端和 API。启动脚本默认打开浏览器；使用 `--no-open` 可关闭此行为。

## 截图与笔记正文导入

在网页「聊天内容」展开「截图 OCR / 笔记文字导入」，可上传 PNG/JPG/JPEG 截图或粘贴笔记文字。截图由本机 macOS Vision 识别；先检查并校对正文与类型，再确认保存。识别出的导航、状态栏和底部操作栏文字会丢弃；边界不确定时必须裁剪或手动校对。原始截图保存在本机私有数据目录，删除对应导入批次时一并清理。

之后在「选择截图／笔记与聊天来源」勾选来源、时间未知内容和逐条正文，再输入主题并预览 AI 外发范围。只有勾选的正文会进入本次生成；可与已导入的聊天一起整理。初次安装由 `bootstrap_macos.sh` 编译本机 OCR 工具；已有安装可单独运行 `./scripts/build_ocr_macos.sh`，需要 macOS 命令行开发工具。

## DeepSeek 配置

运行配置默认选用 `deepseek-v4-flash`，但不会自动寻找、购买或使用任何 API Key。只有进程环境中显式提供 `DEEPSEEK_API_KEY`，并且在 UI 查看完整脱敏外发正文、勾选本次授权后，才会发起网络请求：

```sh
export DEEPSEEK_API_KEY="在本机私密地设置，不要写入仓库或聊天"
./scripts/start_macos.sh
```

已完成一次“工作”主题的真实模型调测，范围与限制见 [调测记录](docs/real-model-work-smoke-test.md)；记录中的新写作规则随后已撤回，当前 v10 总结方式没有追加真实模型质量验收。价格显示为不可用，不会凭空估算；每日请求数默认上限 100，可在私有运行配置中降低。完整说明见 [API 配置指南](docs/api-setup.md)。

## 开发与验证

```sh
make test PYTHON=backend/.venv/bin/python
cd frontend
corepack pnpm test
corepack pnpm run typecheck
corepack pnpm run build
corepack pnpm run test:e2e
```

Playwright 只使用私有临时目录、合成适配器和 FakeProvider，且测试服务器禁止外连。实际测试数字见 [验收报告](docs/acceptance-report.md)。

## 排错

- 启动失败：查看私有数据目录下 `server.log`；访问端口固定为 `8765`，不会自动换到公网地址。
- 前端构建不存在：重新运行 `bootstrap_macos.sh`，它会执行锁文件安装与生产构建。
- 来源未就绪：不要扩大权限；按 [来源配置](docs/source-setup.md) 检查具体阻碍。
- DeepSeek 未配置、认证失败、限流或超时：本地导出、搜索、查看和已有文档编辑仍可用；参考 [API 配置指南](docs/api-setup.md)。
- 停止脚本发现 PID 不属于本项目时会拒绝终止，不会强杀其他进程。

逐按钮后台流程见 [系统工作原理](docs/how-it-works.md)，本地数据与清理边界见 [隐私与清理](docs/privacy-and-cleanup.md)。
