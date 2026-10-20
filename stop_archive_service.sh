#!/bin/bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.dremin.threads_archive_poster.plist"

launchctl unload "$PLIST_PATH" 2>/dev/null
rm -f "$PLIST_PATH"
echo "🛑 Служба com.dremin.threads_archive_poster остановлена и удалена из автозапуска."
