# 记言 · Minutes

记言 · Minutes 是一个中文、本地优先、单用户的 macOS 对话式资料整理智能体。用户手动选择聊天文件、截图或粘贴聊天，选择后默认正确，无需反复确认；直接提出要求，智能体选择工具、本地检索、根据工具结果继续处理，并在对话中展示最终整理结果，交付「整理内容.txt」与独立「核查参考.txt」。支持继续对话修改和保留历次成果。发送要求授权本轮处理，已有的逐次授权、人工编辑版本、Markdown 和 DOCX 导出保留在高级手动工具。

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

## 对话式智能体

1. 新建对话，通过底部「＋」选择当前支持的文件或图片，或直接把聊天记录粘贴到主输入框。选择／粘贴即同意本机导入有效内容及跳过未解析记录，成功后只保留文件名；导入数量与未解析数量可在文件名提示中查看。普通整理要求正常作为消息发送。零有效内容不能空提交，无法确定的截图正文需校对。
2. 发送要求，例如「整理创业的想法，归纳值得尝试的方向」。无需手动填写关键词或选择整理方向。
3. 发送要求授权本轮自动使用本会话所选资料调用已配置的 AI，不再逐次点击外发卡。模型选择本地检索工具；后端执行后，将命中数量等元数据回传模型，再选择继续检索、生成或提问，自动推进至引用核验与交付。可随时停止。
4. 直接阅读整理结果，下载同一保留版本的「整理内容.txt」与「核查参考.txt」。正文不插入系统证据编号或核查附录，来源定位与摘录放在独立参考中。对话用户在右、助手在左，没有头像、重复名称或大幅完成回执。
5. 继续发送「只保留商业模式相关内容，改成简洁的观点总结」等修改要求，复用本会话资料并生成下一版成果。每轮对应独立不可变文档，旧成果卡保留；切换对话不带入其他会话资料。

模型使用已配置的 DeepSeek，仅能选择后端白名单工具；每轮最多 4 次控制调用、3 次本地检索，并与正文生成共用每日请求上限。自动权限仅绑定本轮、当前会话与原来源；新任务、来源变化、停止或重启会撤销，查看旧历史不会自动调用模型。无命中、缺少资料或存在歧义时会询问，不编造结果。费用以供应商实际账单为准。

会话在当前本地浏览器会话范围内可刷新恢复；重启后旧授权和未完成任务作废，可重新发送要求。沿用既有生成主题的 512 字上限，累计完整要求超出时明确提示简化并新建对话，不悄悄丢弃约束。旧本地规则、手动筛选、文档编辑和数据管理可在「设置与高级工具」使用。

原文自确认导入起保留 7 天，可提前删除；已有文档版本继续保留。原文清理后，重新下载的核查参考仅保留不可查看说明，不再提供摘录。平台删除不会删除你已下载的文件。

## 截图与笔记正文导入

通过聊天输入框的「＋」添加 PNG/JPG/JPEG 截图，聊天记录可直接粘贴到主输入框，没有独立的「粘贴笔记」编辑区。普通 TXT 文件无法解析为聊天时，自动按完整文字导入。截图由本机 macOS Vision 识别，可确定的正文自动导入；识别出的导航、状态栏和底部操作栏文字会丢弃，边界不确定时需裁剪或手动校对并一键保存导入。原始截图保存在本机私有数据目录，删除对应导入批次时一并清理。

新确认的截图／笔记会加入当前对话，不会自动纳入其他会话。只有当前选择来源中的匹配正文进入生成，可与聊天文件一起整理。高级工具仍可手动筛选资料。初次安装由 `bootstrap_macos.sh` 编译本机 OCR 工具；已有安装可单独运行 `./scripts/build_ocr_macos.sh`，需要 macOS 命令行开发工具。

## DeepSeek 配置

运行配置默认选用 `deepseek-v4-flash`，不会自动寻找或购买任何 API Key。只有进程环境中显式提供 `DEEPSEEK_API_KEY`，并由用户发送本轮整理要求（主对话）或逐次授权（高级手动工具），才会发起网络请求：

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

Playwright 只使用私有临时目录、合成适配器和 FakeProvider，且测试服务器禁止外连。最新结果见 [对话式智能体验证记录](docs/conversational-agent-verification-2026-10-23.md)；历史记录见 [AI 任务理解验证](docs/ai-agent-planning-verification-2026-10-23.md)、[首版工作台](docs/agent-workspace-verification-2026-10-23.md) 和 [验收报告](docs/acceptance-report.md)。

## 排错

- 启动失败：查看私有数据目录下 `server.log`；访问端口固定为 `8765`，不会自动换到公网地址。
- 前端构建不存在：重新运行 `bootstrap_macos.sh`，它会执行锁文件安装与生产构建。
- 来源未就绪：不要扩大权限；按 [来源配置](docs/source-setup.md) 检查具体阻碍。
- DeepSeek 未配置、认证失败、限流或超时：本地导出、搜索、查看和已有文档编辑仍可用；参考 [API 配置指南](docs/api-setup.md)。
- 停止脚本发现 PID 不属于本项目时会拒绝终止，不会强杀其他进程。

逐按钮后台流程见 [系统工作原理](docs/how-it-works.md)，本地数据与清理边界见 [隐私与清理](docs/privacy-and-cleanup.md)。
