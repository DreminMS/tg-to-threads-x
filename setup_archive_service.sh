#!/bin/bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.dremin.threads_archive_poster.plist"
SCRIPT_PATH="/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads/archive_poster.py"
DIR_PATH="/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads"

mkdir -p "$HOME/Library/LaunchAgents"

cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dremin.threads_archive_poster</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>$SCRIPT_PATH</string>
        <string>--daemon</string>
        <string>--interval</string>
        <string>3600</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$DIR_PATH/archive_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR_PATH/archive_stderr.log</string>
    <key>WorkingDirectory</key>
    <string>$DIR_PATH</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"
echo "✅ Фоновая служба com.dremin.threads_archive_poster успешно установлена и запущена."
echo "Посты будут публиковаться каждый час."
