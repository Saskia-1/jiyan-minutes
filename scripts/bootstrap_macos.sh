#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
DATA_DIR="$HOME/Library/Application Support/Chat to How-To"
EXPORT_DIR="$HOME/Documents/Chat to How-To Exports"
ASSUME_YES=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --data-dir) DATA_DIR=$2; shift 2 ;;
    --export-root) EXPORT_DIR=$2; shift 2 ;;
    --yes) ASSUME_YES=1; shift ;;
    *) echo "用法：$0 [--data-dir 绝对路径] [--export-root 绝对路径] [--yes]" >&2; exit 2 ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || { echo "本交付脚本仅验收 macOS。" >&2; exit 1; }
[ "$(uname -m)" = "arm64" ] || { echo "本交付脚本仅验收 Apple Silicon arm64。" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "需要 Python 3.11 或更高版本。" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "需要 Node.js 24。" >&2; exit 1; }
command -v corepack >/dev/null 2>&1 || { echo "需要随 Node.js 提供的 corepack。" >&2; exit 1; }
command -v xcrun >/dev/null 2>&1 && xcrun --find clang >/dev/null 2>&1 || { echo "截图 OCR 需要 macOS 命令行开发工具（clang）。" >&2; exit 1; }

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python 版本必须至少为 3.11。" >&2; exit 1; }
node -e 'const m=Number(process.versions.node.split(".")[0]); process.exit(m === 24 ? 0 : 1)' || { echo "Node.js 必须是 24.x。" >&2; exit 1; }

if [ "$ASSUME_YES" -ne 1 ]; then
  [ -t 0 ] || { echo "非交互运行需要 --yes，表示批准创建所示本地目录并安装锁定依赖。" >&2; exit 1; }
  echo "数据目录：$DATA_DIR"
  echo "导出目录：$EXPORT_DIR"
  printf "输入 YES 批准创建目录并安装锁定依赖："
  read -r ANSWER
  [ "$ANSWER" = "YES" ] || { echo "已取消，未初始化。"; exit 1; }
fi

python3 -m venv "$PROJECT_ROOT/backend/.venv"
"$PROJECT_ROOT/backend/.venv/bin/python" -m pip install --requirement "$PROJECT_ROOT/backend/requirements.lock"
(cd "$PROJECT_ROOT/frontend" && corepack pnpm install --frozen-lockfile && corepack pnpm run build)
"$SCRIPT_DIR/build_ocr_macos.sh"

CONFIG_PATH="$DATA_DIR/runtime-config.json"
if [ ! -e "$CONFIG_PATH" ]; then
  "$PROJECT_ROOT/backend/.venv/bin/python" "$SCRIPT_DIR/write_runtime_config.py" \
    --project-root "$PROJECT_ROOT" --data-dir "$DATA_DIR" --export-root "$EXPORT_DIR" \
    --provider deepseek --model deepseek-v4-flash
else
  echo "保留已有运行配置，未覆盖。"
fi

echo "初始化完成。运行：CHAT_TO_HOWTO_CONFIG=\"$CONFIG_PATH\" ./scripts/start_macos.sh"
echo "此脚本没有安装 QQ/微信第三方工具、登录账号、申请敏感权限或降低系统保护。"
