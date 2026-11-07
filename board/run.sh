#!/usr/bin/env bash
# Start the ThreadTrace gateway in the background on the UNO Q.
# Put your key in ~/threadtrace/.env as:  GEMINI_API_KEY=xxxx
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
pkill -f "python3 server.py" 2>/dev/null
nohup python3 server.py > server.log 2>&1 &
sleep 1
tail -n 3 server.log
echo "Dashboard: http://$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):8000/"
