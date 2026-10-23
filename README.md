# 记言 · Minutes

记言 · Minutes 是一个中文、本地优先、单用户的 macOS Web 应用。上传聊天文件、截图或添加笔记后，输入自然语言要求，由 Agent 工作台识别关键词与整理方向、本地检索资料；查看完整脱敏预览并逐次授权后，调用已配置的 DeepSeek 整理，分别下载「整理内容.txt」与「核查参考.txt」。已有的人工编辑版本、Markdown 和 DOCX 导出继续可用。

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

## Agent 工作台

1. 添加 TXT、JSON、JSONL、CSV、XLSX 或受支持的单文件 ZIP 聊天资料，先检查预览再确认导入；也可添加 PNG／JPEG 截图或粘贴笔记。
2. 输入要求，例如「把大家聊的创业点子捋一捋，归纳下一步值得试的方向」，点击「理解并整理」。先核对任务要求的完整脱敏预览，授权本次 AI 理解；这一步只发送要求，不发送资料正文。AI 提取关键词、整理目标与方向后，工作台自动检索当前选中资料。
3. 核对可修改的任务卡，以及资料正文的完整脱敏外发预览，再逐次授权「确认并生成」。AI 理解与正文整理的授权分开；要求或来源改变后必须重新预览和授权。
4. 下载同一保留版本的「整理内容.txt」与「核查参考.txt」。正文通过证据编号关联参考中的来源定位与摘录；用户编辑内容的参考仅供对照，不代表自动核验。

默认任务理解调用已配置的 DeepSeek，最多一次规划请求，新增费用以供应商实际账单为准，并与正文生成共用每日请求上限。未配置模型或理解失败时，可重试或明确切换「本地规则（不联网）」；本地规则只识别常见表达。AI 结果也需要核对，模糊主题或尚未执行的资料排除要求会提示调整关键词、方向或来源后继续。高级设置保留手动关键词流程。历史资料不会自动加入当前任务。

原文自确认导入起保留 7 天，可提前删除；已有文档版本继续保留。原文清理后，重新下载的核查参考仅保留不可查看说明，不再提供摘录。平台删除不会删除你已下载的文件。

## 截图与笔记正文导入

在网页「添加资料」展开「截图 OCR / 笔记文字导入」，可上传 PNG/JPG/JPEG 截图或粘贴笔记文字。截图由本机 macOS Vision 识别；先检查并校对正文与类型，再确认保存。识别出的导航、状态栏和底部操作栏文字会丢弃；边界不确定时必须裁剪或手动校对。原始截图保存在本机私有数据目录，删除对应导入批次时一并清理。

新确认的截图／笔记会加入当前任务，可在「选择截图／笔记与聊天来源」调整来源与时间未知内容，再输入自然语言要求并预览 AI 外发范围。只有当前选择的正文进入生成，可与已导入的聊天一起整理。初次安装由 `bootstrap_macos.sh` 编译本机 OCR 工具；已有安装可单独运行 `./scripts/build_ocr_macos.sh`，需要 macOS 命令行开发工具。

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

Playwright 只使用私有临时目录、合成适配器和 FakeProvider，且测试服务器禁止外连。最新 AI 理解验证见 [AI 任务理解验证记录](docs/ai-agent-planning-verification-2026-10-23.md)，首版工作台记录见 [Agent 工作台验证记录](docs/agent-workspace-verification-2026-10-23.md)，历史验收范围见 [验收报告](docs/acceptance-report.md)。

## 排错

- 启动失败：查看私有数据目录下 `server.log`；访问端口固定为 `8765`，不会自动换到公网地址。
- 前端构建不存在：重新运行 `bootstrap_macos.sh`，它会执行锁文件安装与生产构建。
- 来源未就绪：不要扩大权限；按 [来源配置](docs/source-setup.md) 检查具体阻碍。
- DeepSeek 未配置、认证失败、限流或超时：本地导出、搜索、查看和已有文档编辑仍可用；参考 [API 配置指南](docs/api-setup.md)。
- 停止脚本发现 PID 不属于本项目时会拒绝终止，不会强杀其他进程。

逐按钮后台流程见 [系统工作原理](docs/how-it-works.md)，本地数据与清理边界见 [隐私与清理](docs/privacy-and-cleanup.md)。
