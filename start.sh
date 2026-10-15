#!/bin/bash
cd "$(dirname "$0")"

if pgrep -f "python3 bot.py" > /dev/null; then
    echo "Bot juz dziala."
    exit 1
fi

nohup python3 bot.py > bot.log 2>&1 &
echo "Bot odpalony w tle (PID $!). Logi: bot.log"
