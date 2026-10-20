#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="$DIR/ThreadsPoster.app"

if pgrep -f "ThreadsPoster.app" > /dev/null; then
    echo "⚠️ ThreadsPoster уже запущен в статусбаре."
else
    open "$APP_PATH"
    echo "✅ ThreadsPoster запущен в статусбаре macOS."
fi
