#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$DIR/ThreadsPoster.app"

echo "🔨 Сборка ThreadsPoster.app..."
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

cat << 'EOF' > "$APP_DIR/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>ThreadsPoster</string>
    <key>CFBundleIdentifier</key>
    <string>com.dremin.ThreadsPoster</string>
    <key>CFBundleName</key>
    <string>ThreadsPoster</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
EOF

swiftc -O "$DIR/main.swift" -o "$APP_DIR/Contents/MacOS/ThreadsPoster"
chmod +x "$APP_DIR/Contents/MacOS/ThreadsPoster"

echo "✅ Успешно собрано: $APP_DIR"
