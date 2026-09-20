#!/bin/sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
CONFIG_PATH=${CHAT_TO_HOWTO_CONFIG:-"$HOME/Library/Application Support/Chat to How-To/runtime-config.json"}
NO_OPEN=0
[ "${1:-}" != "--no-open" ] || NO_OPEN=1
[ -f "$CONFIG_PATH" ] && [ ! -L "$CONFIG_PATH" ] || { echo "找不到私有运行配置；请先执行 bootstrap_macos.sh。" >&2; exit 1; }
[ -x "$PROJECT_ROOT/backend/.venv/bin/python" ] || { echo "Python 环境不存在；请先执行 bootstrap_macos.sh。" >&2; exit 1; }
[ -f "$PROJECT_ROOT/frontend/dist/index.html" ] || { echo "前端生产构建不存在；请先执行 bootstrap_macos.sh。" >&2; exit 1; }

STATE_DIR=$(dirname -- "$CONFIG_PATH")
PID_FILE="$STATE_DIR/server.pid"
LOG_FILE="$STATE_DIR/server.log"
if [ -f "$PID_FILE" ]; then
  PID=$(sed -n '1p' "$PID_FILE")
  case "$PID" in *[!0-9]*|'') PID=0 ;; esac
  if [ "$PID" -gt 1 ] && kill -0 "$PID" 2>/dev/null; then
    echo "服务已在运行：http://127.0.0.1:8765"
    exit 0
  fi
fi

PYTHONPATH="$PROJECT_ROOT/backend/src" CHAT_TO_HOWTO_CONFIG="$CONFIG_PATH" \
  nohup "$PROJECT_ROOT/backend/.venv/bin/python" -m chat_to_howto >"$LOG_FILE" 2>&1 &
PID=$!
printf '%s\n' "$PID" >"$PID_FILE"

READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 40 ]; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "服务启动失败；请查看私有数据目录中的 server.log（日志不应包含聊天正文或密钥）。" >&2
    exit 1
  fi
  if /usr/bin/curl --silent --fail --max-time 1 --header 'Host: 127.0.0.1:8765' http://127.0.0.1:8765/api/session >/dev/null 2>&1; then
    READY=1
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 0.25
done
[ "$READY" -eq 1 ] || { echo "服务未在限定时间内就绪。" >&2; exit 1; }
echo "Chat to How-To 已启动：http://127.0.0.1:8765"
if [ "$NO_OPEN" -ne 1 ]; then
  /usr/bin/open http://127.0.0.1:8765
fi
