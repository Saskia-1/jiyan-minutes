#!/bin/sh
set -eu

CONFIG_PATH=${CHAT_TO_HOWTO_CONFIG:-"$HOME/Library/Application Support/Chat to How-To/runtime-config.json"}
PID_FILE="$(dirname -- "$CONFIG_PATH")/server.pid"
[ -f "$PID_FILE" ] || { echo "未发现运行中的服务。"; exit 0; }
PID=$(sed -n '1p' "$PID_FILE")
case "$PID" in *[!0-9]*|'') echo "PID 文件无效；未终止任何进程。" >&2; exit 1 ;; esac
if ! kill -0 "$PID" 2>/dev/null; then
  echo "服务已经停止。"
  exit 0
fi
COMMAND=$(/bin/ps -p "$PID" -o command= 2>/dev/null || true)
case "$COMMAND" in *"-m chat_to_howto"*) ;; *) echo "PID 不属于 Chat to How-To；未终止任何进程。" >&2; exit 1 ;; esac
kill -TERM "$PID"
ATTEMPT=0
while kill -0 "$PID" 2>/dev/null && [ "$ATTEMPT" -lt 40 ]; do
  ATTEMPT=$((ATTEMPT + 1))
  sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
  echo "服务未能正常停止；未强制终止，请检查该 PID。" >&2
  exit 1
fi
echo "Chat to How-To 已停止。PID 文件会保留用于诊断，下次启动会安全更新。"
