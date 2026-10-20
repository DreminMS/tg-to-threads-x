#!/bin/bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.dremin.tg_to_threads.plist"
SCRIPT_PATH="/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads/crosspost.py"
LOG_DIR="/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads"

mkdir -p "$HOME/Library/LaunchAgents"

cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dremin.tg_to_threads</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>$SCRIPT_PATH</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/stderr.log</string>
    <key>WorkingDirectory</key>
    <string>$LOG_DIR</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"
echo "✅ Служба com.dremin.tg_to_threads успешно установлена и запущена в фоне через launchd."
echo "Проверить статус: launchctl list | grep tg_to_threads"
