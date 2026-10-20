#!/bin/bash
if pgrep -f "ThreadsPoster.app" > /dev/null; then
    pkill -f "ThreadsPoster.app"
    echo "🛑 ThreadsPoster выгружен из статусбара."
else
    echo "ThreadsPoster не был запущен."
fi
