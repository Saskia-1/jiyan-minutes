#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd -P)"
output="$project_root/backend/native/ocr/build/chat-image-ocr"
mkdir -p "$(dirname "$output")"
xcrun clang -fobjc-arc "$project_root/backend/native/ocr/recognize.m" \
  -framework Foundation -framework Vision -framework ImageIO -framework CoreGraphics -o "$output"
chmod 700 "$output"
printf '%s\n' "$output"
