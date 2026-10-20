#!/bin/bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.dremin.tg_to_threads.plist"

launchctl unload "$PLIST_PATH" 2>/dev/null
rm -f "$PLIST_PATH"
echo "🛑 Служба com.dremin.tg_to_threads остановлена и удалена из автозапуска."
