# Chat to How-To

本项目将经用户明确授权的本地聊天导出整理为操作指南。当前基础模块只收集操作系统、指定应用包与 Python 的版本和架构；它不扫描聊天目录、账号、消息、密钥或完整环境变量。

## 本地开发

1. 复制 `.env.example` 到工作区外的受限位置后按需配置；示例文件中的秘密字段必须保持为空。
2. 创建隔离的本地开发环境并安装锁定的测试依赖：

   ```sh
   python3 -m venv backend/.venv
   backend/.venv/bin/python -m pip install --upgrade pip
   backend/.venv/bin/python -m pip install -e "./backend[dev]"
   ```

3. 运行测试：

   ```sh
   make test PYTHON=backend/.venv/bin/python
   ```

   该命令安装且仅锁定开发测试依赖 `pytest==9.1.1`；项目尚未声明其他运行依赖。

服务仅计划监听 `127.0.0.1`。真实聊天源、第三方工具或账号均不会由本项目自动安装、启动或访问。
